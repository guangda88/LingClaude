# lingclaude/model/openrouter_oauth.py
"""OpenRouter OAuth PKCE 一键接入（P1-8，2026-09-21，学 atomcode 同款体验）。

背景：GLM CodingPlan 5h 窗口耗尽时（1310 熔断），lingke 此前只能等重置。
atomcode 的 /openrouter 体验：一键 OAuth 授权 → key 落盘 → 免费模型发现
→ 立即可切。本插片同款移植。

OAuth 契约（openrouter.ai/docs/oauth，2026-09-21 web_fetch 验证）：
1. 生成 code_verifier（高熵随机串）+ S256 code_challenge；
2. 浏览器打开 https://openrouter.ai/auth?code_challenge=<C>&
   code_challenge_method=S256&callback_url=http://127.0.0.1:<port>/callback
3. 用户授权后浏览器回调本地端口，带 ?code=<authorization_code>；
4. POST https://openrouter.ai/api/v1/auth/keys
   body={"code": ..., "code_verifier": ...} → {"key": "sk-or-..."}。

落盘策略：
- key 存 ~/.lingclaude/credentials/openrouter_key.json（0600）——本仓自有
  凭据域，不写 lingcode/config.json（跨项目文件只读不写，模型列表除外，
  那是隔壁 sync 脚本已确立的惯例：routing.providers.openrouter.models）；
- 授权完成即刻 os.environ 注入 OPENROUTER_API_KEY（TaskRouter 的
  _resolve_api_key env 兜底链当下即生效，无需重启）；
- 下次进程启动由 cmd 入口检查凭据文件并惰性注入。

免费模型发现：GET /api/v1/models（公开端点免 key）→ id 以 ":free" 结尾
者为免费模型 → 合并写 lingcode/config.json 的 openrouter.models
（保留既有非免费条目，去重排序）。

停层声明（铁律 2 细则 5）：
- 内核 = PKCE 授权流 + 凭据仓（文件 0600）
- 接缝 = authorize_start()/exchange_code()/refresh_free_models() 协议
- 实现 = 单实现（urllib + http.server 标准库，无三方依赖）
边界纪律：本插片只管「拿 key、存 key、列免费模型」；选 provider 仍是
task_router 职责（分工同 credential_pool：池管账号，路由管方向）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

logger = logging.getLogger(__name__)

OPENROUTER_AUTH_URL = "https://openrouter.ai/auth"
OPENROUTER_EXCHANGE_URL = "https://openrouter.ai/api/v1/auth/keys"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
ENV_KEY_NAME = "OPENROUTER_API_KEY"

_CREDENTIALS_DIR = Path.home() / ".lingclaude" / "credentials"
_KEY_FILE = _CREDENTIALS_DIR / "openrouter_key.json"

# lingcode config.json 的模型清单写回点（隔壁 sync 脚本同款惯例）
_LINGCODE_CONFIG = Path("/home/ai/lingcode/config.json")

_CALLBACK_TIMEOUT_S = 300.0  # 授权回调等待上限 5 分钟

# 最近一次授权 URL（CLI 打印用；authorize() 起好回调服务器后写入）
_last_auth_url: str = ""


def _b64url_no_pad(raw: bytes) -> str:
    """base64url 无填充（RFC 7636 S256 challenge 编码）。"""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def make_pkce_pair() -> tuple[str, str]:
    """生成 (code_verifier, S256 code_challenge)。

    verifier：43-128 字符的 [A-Za-z0-9-._~]（RFC 7636 §4.1）；
    challenge = BASE64URL(SHA256(verifier))。
    """
    verifier = secrets.token_urlsafe(48)  # 64 字符，落在合法区间
    challenge = _b64url_no_pad(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def build_auth_url(challenge: str, port: int) -> str:
    """构造授权页 URL（callback_url 指向本地回调端口）。"""
    q = urllib.parse.urlencode({
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "callback_url": f"http://127.0.0.1:{port}/callback",
    })
    return f"{OPENROUTER_AUTH_URL}?{q}"


@dataclass
class ExchangeResult:
    ok: bool
    key: str = ""
    error: str = ""


def exchange_code(code: str, verifier: str, timeout: float = 15.0) -> ExchangeResult:
    """用授权码 + verifier 换 API key（POST /api/v1/auth/keys）。"""
    body = json.dumps({"code": code, "code_verifier": verifier}).encode("utf-8")
    req = urllib.request.Request(
        OPENROUTER_EXCHANGE_URL,
        data=body,
        headers={"Content-Type": "application/json",
                 "User-Agent": "lingclaude-openrouter/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:200]
        except Exception:
            pass
        return ExchangeResult(ok=False, error=f"HTTP {e.code}: {detail}")
    except Exception as e:  # 网络层故障 fail-closed（不假成功）
        return ExchangeResult(ok=False, error=str(e))
    key = data.get("key", "")
    if not key:
        return ExchangeResult(ok=False, error=f"响应缺 key 字段: {list(data.keys())}")
    return ExchangeResult(ok=True, key=key)


# ── 凭据仓 ──

def save_key(key: str) -> Path:
    """key 落盘（0600，仅本用户可读）。"""
    _CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    _KEY_FILE.write_text(json.dumps({"api_key": key}, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(_KEY_FILE, 0o600)
    except OSError:
        pass
    return _KEY_FILE


def load_saved_key() -> str:
    """读已存 key（无文件/损坏 → 空串）。"""
    try:
        data = json.loads(_KEY_FILE.read_text(encoding="utf-8"))
        return str(data.get("api_key", "") or "")
    except (OSError, ValueError):
        return ""


def ensure_env_key() -> bool:
    """环境无 key 时从凭据仓惰性注入。返回注入后 env 是否有 key。

    进程启动后首次 /openrouter（或配额提示）时调用——TaskRouter 的
    _resolve_api_key env 兜底链即可读到。
    """
    if os.environ.get(ENV_KEY_NAME):
        return True
    saved = load_saved_key()
    if saved:
        os.environ[ENV_KEY_NAME] = saved
        return True
    return False


def clear_saved_key() -> bool:
    """注销：删凭据文件 + 清 env。"""
    try:
        _KEY_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    os.environ.pop(ENV_KEY_NAME, None)
    return True


# ── 免费模型发现 ──

def fetch_free_models(timeout: float = 15.0) -> list[str] | None:
    """拉 OpenRouter 公开模型清单，返回 :free 结尾的 id 列表。

    网络失败返回 None（调用方维持现状，不写坏 config）。
    """
    req = urllib.request.Request(
        OPENROUTER_MODELS_URL, headers={"User-Agent": "lingclaude-openrouter/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning("openrouter: 拉取模型清单失败: %s", e)
        return None
    ids = [m.get("id", "") for m in data.get("data", []) if isinstance(m, dict)]
    return sorted({i for i in ids if i.endswith(":free")})


def merge_free_models_into_lingcode(free_ids: list[str]) -> dict[str, int]:
    """把免费模型并入 lingcode/config.json 的 openrouter.models。

    保留既有非免费条目；返回 {"added": 新增数, "total": 合并后总数}。
    lingcode config 不存在/无 openrouter provider 时跳过写回（返回 0/0）。
    """
    if not _LINGCODE_CONFIG.exists():
        return {"added": 0, "total": 0}
    try:
        cfg = json.loads(_LINGCODE_CONFIG.read_text(encoding="utf-8"))
        prov = cfg["routing"]["providers"]["openrouter"]
        existing = list(prov.get("models", []))
        merged = sorted(set(existing) | set(free_ids))
        added = len(merged) - len(set(existing))
        if added > 0 or len(merged) != len(existing):
            prov["models"] = merged
            if not prov.get("model"):
                prov["model"] = merged[0]
            _LINGCODE_CONFIG.write_text(
                json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"added": added, "total": len(merged)}
    except (OSError, ValueError, KeyError) as e:
        logger.warning("openrouter: 写回 lingcode config 失败: %s", e)
        return {"added": 0, "total": 0}


# ── 本地回调服务器（非阻塞：后台线程跑，主线程立即返回授权 URL） ──

@dataclass
class CallbackWaiter:
    """一次性本地回调服务器。start() 后立即返回授权 URL；
    result() 阻塞等回调（或超时）。"""

    auth_url: str
    code: str | None = None
    error: str | None = None

    def start(self) -> None:
        waiter = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802（http.server 契约）
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                qs = urllib.parse.parse_qs(parsed.query)
                if "code" in qs:
                    waiter.code = qs["code"][0]
                    body = b"<h1>lingclaude: OpenRouter authorization received, you can return to the terminal.</h1>"
                    self.send_response(200)
                else:
                    waiter.error = "回调缺 code 参数"
                    body = b"<h1>lingclaude: missing code param</h1>"
                    self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # 静默（TUI 下别刷 stderr）
                pass

        self._server = HTTPServer(("127.0.0.1", 0), _Handler)  # 内核随机端口
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True, name="or-callback")
        self._thread.start()

    def wait(self, timeout: float = _CALLBACK_TIMEOUT_S) -> None:
        """阻塞等回调（轮询 code/error 状态位），到点关服务器。"""
        deadline = time.monotonic() + timeout
        while self.code is None and self.error is None and time.monotonic() < deadline:
            time.sleep(0.2)
        self._server.shutdown()




def authorize() -> ExchangeResult:
    """一键授权全流程：起回调服务器 → 返回 URL（打印给用户）→ 等回调 → 换 key。

    阻塞语义：调用方（CLI 命令）在后台线程跑本函数，立即向用户展示
    auth_url；成功后 key 自动落盘 + env 注入（save_key + ensure_env_key）。
    """
    verifier, challenge = make_pkce_pair()
    waiter = CallbackWaiter(auth_url="")
    waiter.start()
    waiter.auth_url = build_auth_url(challenge, waiter.port)
    global _last_auth_url
    _last_auth_url = waiter.auth_url  # CLI 立即读取打印（轮询 10s 内可见）
    try:
        waiter.wait()
    finally:
        try:
            waiter._server.server_close()
        except Exception:
            pass
    if waiter.error or waiter.code is None:
        return ExchangeResult(ok=False, error=waiter.error or "授权超时（5 分钟无回调）")
    ex = exchange_code(waiter.code, verifier)
    if ex.ok:
        save_key(ex.key)
        os.environ[ENV_KEY_NAME] = ex.key
    return ex
