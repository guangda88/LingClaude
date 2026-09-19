"""P1-F1/P1-F2: 路由层清单实时探活网关（lc 会话幻觉调研 20260919 第一批措施 1/2 的执行器）。

治本裁决：路由决策依赖的清单/健康度不再让 LLM"凭记忆"，外置到实时 API——
幻觉从"模型能力问题"降级为"接口契约问题"（工程可控）。

设计要点（对应调研 §5 裁决 + 落地提醒）：
- 探活方式：GET {base_url}/models（OpenAI 协议通用清单端点，轻量、无副作用）
- 410/401/404 → excluded=True：清单级死节点（部署下线/key 失效/模型不存在），
  路由层据此剔除（复用 F12j 熔断轨），呼应调研 H1 实证（nvidia 全 410 / hunyuan 404）
- 429/408    → bypass_round=True：限流非下线，节点活着——本轮绕过但绝不剔除
  （调研 §2.5："429 误读为鉴权失败"正是本会话踩过的坑，探活器不得重蹈）
- 网络错误/5xx → unknown：探活自身故障不产生清单结论，不误杀
- TTL 缓存：OK 结论 5min、限流结论 1min、网络错误结论 1min——省配额不拖慢路由
- 证据落盘：每次实际探测 append 到 .lingclaude/provider_probe.jsonl，
  治"这个模型为什么不见了"又变成需要凭记忆回答的问题

保险丝：环境变量 LINGCLAUDE_PROBE_DISABLE=1 可整体关闭探活（网络受限环境）。
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 证据日志：.lingclaude/ 与 guard/file_history 同区（运行时产物单点）
EVIDENCE_PATH = Path(".lingclaude") / "provider_probe.jsonl"

# 探活结论 TTL（秒）——缓存期内路由直接复用结论，不发真实请求
PROBE_TTL_OK = 300.0          # 200 等"已验证活着"：5 分钟
PROBE_TTL_RATE = 60.0         # 429/408 限流：1 分钟后重探（限流恢复窗口通常分钟级）
PROBE_TTL_ERR = 60.0          # 网络错误/超时抖动：1 分钟后重探
PROBE_TTL_HARD = 1800.0       # 410/401/404 硬错误：30 分钟（与 F12j 熔断时长对齐）

# HTTP 探活超时（秒）——探活是路由前置步骤，必须快速失败
PROBE_TIMEOUT = 5.0

# 硬错误码：清单级死节点（key 失效/部署下线/模型不存在）
_HARD_CODES = frozenset({401, 403, 404, 410})
# 限流码：节点活着但暂时不可用
_RATE_CODES = frozenset({429, 408})


@dataclass
class ProbeResult:
    """单次探活结论。语义经 excluded/bypass_round/unknown 三属性对外暴露。"""

    provider: str
    base_url: str
    status: str            # ok | hard_4xx | rate_limited | network_error | server_error | skipped | unknown_http
    http_code: int = 0
    detail: str = ""
    probed_at: float = 0.0  # time.monotonic()，TTL 判定用

    @property
    def excluded(self) -> bool:
        """True = 清单级死节点，路由层应剔除（熔断 30min）。"""
        return self.status == "hard_4xx"

    @property
    def bypass_round(self) -> bool:
        """True = 限流，本轮绕过该 provider 但保留在清单里。"""
        return self.status == "rate_limited"

    @property
    def unknown(self) -> bool:
        """True = 探活自身不可知（网络错误/5xx/异常码），不据此做任何剔除动作。"""
        return self.status in ("network_error", "server_error", "unknown_http")

    @property
    def ttl(self) -> float:
        if self.status == "ok":
            return PROBE_TTL_OK
        if self.status == "rate_limited":
            return PROBE_TTL_RATE
        if self.status == "hard_4xx":
            return PROBE_TTL_HARD
        return PROBE_TTL_ERR


class ProviderProbe:
    """provider 级探活网关。

    单实例常驻 TaskRouter；线程安全（探活缓存有锁）。
    探活失败本身永不抛异常——探活是路由的前置增强，不能比被探活的对象更脆弱。
    """

    def __init__(self, evidence_path: Path | None = None, timeout: float = PROBE_TIMEOUT) -> None:
        self._cache: dict[str, ProbeResult] = {}
        self._lock = Lock()
        self._evidence_path = Path(evidence_path) if evidence_path else EVIDENCE_PATH
        self._timeout = timeout

    # ---------- 对外主入口 ----------

    def check(
        self, provider: str, base_url: str, api_key: str, *, force: bool = False
    ) -> ProbeResult:
        """探活一个 provider。TTL 内返回缓存结论；force=True 强制重探。

        永不抛异常：任何内部错误降级为 status="unknown_http" 的未知结论。
        """
        now = time.monotonic()
        if not force:
            with self._lock:
                cached = self._cache.get(provider)
                if cached and (now - cached.probed_at) < cached.ttl:
                    return cached

        try:
            result = self._probe_now(provider, base_url, api_key)
        except Exception as e:  # noqa: BLE001 — 探活自身永不抛（纵深防御）
            logger.debug("provider_probe: check 内部异常降级为 unknown: %s", e)
            result = ProbeResult(provider=provider, base_url=base_url,
                                 status="unknown_http", detail=f"probe crashed: {e}"[:200],
                                 probed_at=now)
        with self._lock:
            self._cache[provider] = result
        # skipped（本地端点/无 key）不算探测事件，不写证据
        if result.status != "skipped":
            self._write_evidence(result)
        return result

    def invalidate(self, provider: str) -> None:
        """清掉某 provider 的探活缓存（供 record_success 后强制下轮重探等场景）。"""
        with self._lock:
            self._cache.pop(provider, None)

    # ---------- 内部实现 ----------

    def _probe_now(self, provider: str, base_url: str, api_key: str) -> ProbeResult:
        base = (base_url or "").strip()
        now = time.monotonic()
        try:
            hostname = urlparse(base).hostname or ""
        except ValueError:
            hostname = ""
        is_local = hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0")

        # 本地端点不探：无 key 合法、探活反而在服务未起时制造噪音，
        # 真实调用层（provider.stream_complete）自会暴露。
        if is_local:
            return ProbeResult(provider=provider, base_url=base, status="skipped",
                               detail="local endpoint, 不探活", probed_at=now)
        # 云端无 key 不探：F12b 路由层本来就会因缺 key 跳过，探活无意义。
        if not api_key:
            return ProbeResult(provider=provider, base_url=base, status="skipped",
                               detail="no api_key (F12b 已跳过), 不探活", probed_at=now)
        if not base:
            return ProbeResult(provider=provider, base_url=base, status="skipped",
                               detail="empty base_url", probed_at=now)

        code, detail = self._http_probe(base, api_key)
        status = self._classify(code)
        return ProbeResult(provider=provider, base_url=base, status=status,
                           http_code=code or 0, detail=detail, probed_at=now)

    def _http_probe(self, base_url: str, api_key: str) -> tuple[int | None, str]:
        """GET {base_url}/models → (http_code | None, detail)。网络层失败 code=None。"""
        url = base_url.rstrip("/") + "/models"
        try:
            req = urllib.request.Request(url, method="GET")
            req.add_header("Authorization", f"Bearer {api_key}")
            req.add_header("User-Agent", "lingclaude-provider-probe/1")
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return resp.status, ""
        except urllib.error.HTTPError as e:
            return e.code, str(e.reason or "")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return None, str(e)[:200]
        except Exception as e:  # 探活自身永不抛
            return None, f"probe internal error: {e}"[:200]

    @staticmethod
    def _classify(code: int | None) -> str:
        if code is None:
            return "network_error"
        if code in _HARD_CODES:
            return "hard_4xx"
        if code in _RATE_CODES:
            return "rate_limited"
        if 200 <= code < 300:
            return "ok"
        if 500 <= code < 600:
            return "server_error"
        return "unknown_http"

    def _write_evidence(self, result: ProbeResult) -> None:
        """探测事件落盘——剔除/绕过动作全部留痕，审计不靠记忆。写失败静默降级。"""
        try:
            self._evidence_path.parent.mkdir(parents=True, exist_ok=True)
            rec = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "provider": result.provider,
                "base_url": result.base_url,
                "status": result.status,
                "http_code": result.http_code,
                "detail": result.detail[:200],
            }
            with open(self._evidence_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.debug("provider_probe: 证据写盘失败（不影响路由）: %s", e)


def probe_disabled_by_env() -> bool:
    """保险丝开关：LINGCLAUDE_PROBE_DISABLE=1 时整体关闭探活。"""
    return os.environ.get("LINGCLAUDE_PROBE_DISABLE", "").strip() == "1"
