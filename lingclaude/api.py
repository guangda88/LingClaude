from __future__ import annotations

"""灵克 HTTP API — 灵字辈成员通过 HTTP 调用灵克能力。

端口 8700，供灵通等成员调用。
"""

import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

from fastapi import FastAPI, HTTPException, Security  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.security import APIKeyHeader  # noqa: E402
from pydantic import BaseModel  # noqa: E402

logger = logging.getLogger(__name__)

# API Key 认证
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

# 从环境变量或配置文件读取 API Keys
_VALID_API_KEYS = set()
_api_keys_env = os.environ.get("LINGCLAUDE_API_KEYS", "")
if _api_keys_env:
    _VALID_API_KEYS.update(key.strip() for key in _api_keys_env.split(","))

async def verify_api_key(api_key: str = Security(API_KEY_HEADER)):
    """验证 API Key"""
    if api_key and api_key in _VALID_API_KEYS:
        return api_key
    raise HTTPException(
        status_code=401,
        detail="无效的 API Key",
    )


def _validate_path(path: Path, base_dir: Path) -> Path:
    """验证路径是否在基础目录内，防止路径遍历攻击"""
    resolved = path.resolve()
    base_resolved = base_dir.resolve()

    try:
        resolved.relative_to(base_resolved)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=f"拒绝访问路径 {path}（超出工作目录）"
        )

    return resolved


_WORKING_DIR = Path(os.getcwd())

app = FastAPI(title="灵克 API", version="0.2.2", description="灵字辈编程助手API")

# 限制 CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key"],
)


class AskRequest(BaseModel):
    question: str
    context: str = ""


class AskResponse(BaseModel):
    answer: str
    source: str = "lingclaude"


class AnalyzeRequest(BaseModel):
    path: str
    focus: str = ""


class ExecRequest(BaseModel):
    command: str
    working_dir: str = os.getcwd()


class WriteFileRequest(BaseModel):
    path: str
    content: str


@app.get("/")
async def root():
    return {
        "name": "灵克 (LingClaude)",
        "version": "0.2.1",
        "role": "AI 编程助手",
        "endpoints": ["/ask", "/analyze", "/exec", "/read-file", "/write-file", "/status"],
    }


@app.get("/status")
async def get_status(api_key: str = Security(verify_api_key)):
    return {
        "status": "online",
        "version": "0.2.2",
        "projects": _list_projects(),
        "auth_required": bool(_VALID_API_KEYS),
    }


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest, api_key: str = Security(verify_api_key)):
    q = req.question
    ctx = req.context
    prompt = q
    if ctx:
        prompt = f"上下文：{ctx}\n\n问题：{q}"

    answer = _route_question(prompt)
    return AskResponse(answer=answer)


@app.post("/analyze")
async def analyze(req: AnalyzeRequest, api_key: str = Security(verify_api_key)):
    target = Path(req.path)
    if not target.exists():
        raise HTTPException(404, f"路径不存在: {req.path}")

    if target.is_file():
        return _analyze_file(target, req.focus)
    elif target.is_dir():
        return _analyze_dir(target, req.focus)
    else:
        raise HTTPException(400, f"不是文件或目录: {req.path}")


@app.post("/exec")
async def exec_cmd(req: ExecRequest):
    # [安全禁用] 此端点因命令注入风险已被禁用
    # 原因: shell=True 允许任意命令执行
    # 替代方案: 使用特定的工具端点
    raise HTTPException(503, "命令执行端点已禁用（安全策略）。如需执行命令，请使用特定的工具端点。")

    # 原实现已注释：
    # blocked = ["rm -rf /", "mkfs", "dd if=", "> /dev/sd", "format"]
    # if any(b in req.command for b in blocked):
    #     raise HTTPException(403, "命令被安全策略阻止")
    # ...


@app.post("/read-file")
async def read_file(path: str, api_key: str = Security(verify_api_key)):
    p = _validate_path(Path(path), _WORKING_DIR)
    if not p.exists():
        raise HTTPException(404, f"文件不存在: {path}")
    if p.stat().st_size > 1_000_000:
        raise HTTPException(413, "文件超过 1MB")
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
        return {"path": str(p), "content": content, "size": len(content)}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/write-file")
async def write_file(req: WriteFileRequest, api_key: str = Security(verify_api_key)):
    p = _validate_path(Path(req.path), _WORKING_DIR)
    if not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(req.content, encoding="utf-8")
        return {"path": str(p), "size": len(req.content), "status": "written"}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/lingmessage/notify")
async def lingmessage_notify(payload: dict, api_key: str = Security(verify_api_key)):
    """灵信通知端点 — 记录通知，不自动回复。

    auto_reply 于 2026-04-21 物理删除（不是注释禁用，是删除函数）。
    灵克在治理线程中的发言应由 Crush 会话（真实人类/AI 交互）产生。
    如果需要恢复 auto_reply，必须通过灵委会提案 + 代码审查。
    """
    event = payload.get("event") or payload.get("type")
    from_member = payload.get("from") or payload.get("sender")
    discussion_id = payload.get("discussion_id")
    topic = payload.get("topic", "")
    thread_id = payload.get("thread_id")

    logger.info(f"灵信通知: event={event}, from={from_member}, thread={thread_id}, topic={topic[:40]}")

    return {"received": True, "service": "灵克", "action": "logged"}


class GovernedPostRequest(BaseModel):
    thread_id: str
    subject: str = ""
    content: str
    recipient: str = "all"


@app.post("/api/lingmessage/post")
async def lingmessage_post(req: GovernedPostRequest, api_key: str = Security(verify_api_key)):
    """治理强制的灵信发帖 — 所有对外发言必须通过 GovernanceGate 检查。

    这是灵克唯一允许主动发帖的 API 端点。GovernanceGate 硬编码在此，
    无法通过参数跳过。如果 gate 未通过，返回 403 并记录日志。
    """
    from lingclaude.core.governance_integration import pre_submit_governance

    gov_result = pre_submit_governance(
        action="post_reply",
        content=req.content,
        subject=req.subject,
        agent_id="lingclaude",
    )

    if not gov_result.get("approved"):
        logger.warning(f"GovernanceGate 拒绝发帖: {gov_result.get('reason')}")
        raise HTTPException(403, f"GovernanceGate 拒绝: {gov_result.get('reason')}")

    import sys
    sys.path.insert(0, "/home/ai/LingMessage")
    from lingmessage.lingbus import LingBus
    from pathlib import Path as _P

    bus = LingBus(bus_dir=_P.home() / ".lingmessage")
    try:
        msg_id = bus.post_reply(
            thread_id=req.thread_id,
            sender="lingclaude",
            recipient=req.recipient,
            subject=req.subject,
            body=req.content,
        )
        logger.info(f"Governed post OK: thread={req.thread_id}, msg={msg_id}")
        return {"posted": True, "message_id": msg_id, "gate_warnings": gov_result.get("warnings", [])}
    except Exception as e:
        logger.error(f"发帖失败: {e}")
        raise HTTPException(500, str(e))
    finally:
        bus.close()


def _load_env_keys() -> dict[str, str]:
    keys: dict[str, str] = {}
    for f in ["/home/ai/zhineng-knowledge-system/.env", "/home/ai/LingClaude/.env"]:
        p = Path(f)
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    keys[k.strip()] = v.strip()
    return keys


_LLM_PROVIDERS = [
    {"key_env": "GLM_CODING_PLAN_KEY", "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions", "model": "glm-4.7"},
    {"key_env": "GLM_API_KEY", "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions", "model": "glm-4.7"},
    {"key_env": "DEEPSEEK_API_KEY", "url": "https://api.deepseek.com/v1/chat/completions", "model": "deepseek-chat"},
]


def _call_llm(system_prompt: str, user_msg: str) -> str:
    import json
    import urllib.request

    env_keys = _load_env_keys()
    body = json.dumps({
        "model": "",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.7,
        "max_tokens": 1024,
    }, ensure_ascii=False).encode("utf-8")

    for provider in _LLM_PROVIDERS:
        api_key = env_keys.get(provider["key_env"], "")
        if not api_key:
            continue
        payload = json.loads(body)
        payload["model"] = provider["model"]
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            provider["url"], data=data,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            logger.info(f"LLM OK via {provider['key_env']}/{provider['model']}")
            return content.strip()
        except Exception as e:
            logger.warning(f"LLM {provider['key_env']}/{provider['model']} failed: {e}")
            continue

    logger.error("所有 LLM provider 均不可用")
    return ""


def _route_question(prompt: str) -> str:
    p = prompt.lower()

    if any(kw in p for kw in ("几个星", "star", "github")):
        return _query_github_stars(prompt)
    if any(kw in p for kw in ("下载量", "download", "pypi")):
        return _query_pypi_downloads(prompt)
    if any(kw in p for kw in ("版本", "version")):
        return _query_versions()
    if any(kw in p for kw in ("项目", "project", "状态")):
        return _format_projects()
    if any(kw in p for kw in ("提交", "commit", "git")):
        return _query_recent_commits(prompt)

    system = "你是灵克（LingClaude），灵字辈大家庭的编程助手。简洁回答。"
    return _call_llm(system, prompt) or f"灵克暂时无法回答：{prompt}"


def _query_github_stars(prompt: str) -> str:
    repos = {
        "灵通": "guangda88/lingflow",
        "灵克": "guangda88/lingclaude",
        "灵通问道": "guangda88/lingtongask",
        "灵扬": "guangda88/lingyang",
    }
    for name, repo in repos.items():
        if name in prompt:
            try:
                import urllib.request
                url = f"https://api.github.com/repos/{repo}"
                req = urllib.request.Request(url, headers={"User-Agent": "LingClaude"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode())
                return f"{name} ({repo}): {data.get('stargazers_count', 0)} stars, {data.get('forks_count', 0)} forks, {data.get('open_issues_count', 0)} open issues"
            except Exception as e:
                return f"查询 {name} 的 GitHub 信息失败: {e}"

    all_info = []
    for name, repo in repos.items():
        try:
            import urllib.request
            url = f"https://api.github.com/repos/{repo}"
            req = urllib.request.Request(url, headers={"User-Agent": "LingClaude"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            all_info.append(f"{name}: {data.get('stargazers_count', 0)} stars")
        except Exception:
            all_info.append(f"{name}: 查询失败")
    return "\n".join(all_info)


def _query_pypi_downloads(prompt: str) -> str:
    import urllib.request
    packages = ["lingflow-core", "lingflow-mcp", "lingclaude", "lingtongask"]
    results = []
    for pkg in packages:
        try:
            url = f"https://pypi.org/pypi/{pkg}/json"
            req = urllib.request.Request(url, headers={"User-Agent": "LingClaude"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            ver = data.get("info", {}).get("version", "?")
            results.append(f"{pkg} v{ver}")
        except Exception:
            pass
    return "\n".join(results) if results else "未找到 PyPI 包信息。"


def _query_versions() -> str:
    version_file = Path("/home/ai/LingClaude/VERSION")
    lc_ver = version_file.read_text().strip() if version_file.exists() else "未知"
    return f"灵克 (LingClaude) 当前版本: {lc_ver}"


def _list_projects() -> list[dict]:
    roots = {
        "LingFlow": "/home/ai/LingFlow",
        "LingClaude": "/home/ai/LingClaude",
        "LingYang": "/home/ai/LingYang",
        "LingTongAsk": "/home/ai/lingtongask",
        "LingMessage": "/home/ai/LingMessage",
    }
    projects = []
    for name, path in roots.items():
        p = Path(path)
        if p.exists():
            projects.append({"name": name, "path": path, "exists": True})
        else:
            projects.append({"name": name, "path": path, "exists": False})
    return projects


def _format_projects() -> str:
    projects = _list_projects()
    lines = []
    for p in projects:
        status = "存在" if p["exists"] else "不存在"
        lines.append(f"- {p['name']}: {status} ({p['path']})")
    return "\n".join(lines)


def _query_recent_commits(prompt: str) -> str:
    roots = {
        "灵克": "/home/ai/LingClaude",
        "灵通": "/home/ai/LingFlow",
    }
    target_dir = None
    for name, path in roots.items():
        if name in prompt:
            target_dir = path
            break
    if not target_dir:
        target_dir = "/home/ai/LingClaude"

    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            capture_output=True, text=True, timeout=5,
            cwd=target_dir,
        )
        return f"最近提交（{target_dir}）：\n{result.stdout.strip()}"
    except Exception as e:
        return f"查询提交失败: {e}"


def _analyze_file(path: Path, focus: str) -> dict:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        lines = content.count("\n") + 1
        return {
            "path": str(path),
            "size": len(content),
            "lines": lines,
            "suffix": path.suffix,
            "preview": content[:2000],
        }
    except Exception as e:
        return {"error": str(e)}


def _analyze_dir(path: Path, focus: str) -> dict:
    files = list(path.rglob("*"))
    py_files = [f for f in files if f.suffix == ".py"]
    total_size = sum(f.stat().st_size for f in files if f.is_file())
    return {
        "path": str(path),
        "total_files": len(files),
        "py_files": len(py_files),
        "total_size_mb": round(total_size / 1_000_000, 2),
    }


def run_server(host: str = "0.0.0.0", port: int = 8700):
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info")
