from __future__ import annotations

"""灵克 HTTP API — 灵字辈成员通过 HTTP 调用灵克能力。

端口 8700，供灵依、灵通等成员调用。
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
    allow_origins=["http://localhost:8900", "http://127.0.0.1:8900", "http://localhost:3000"],
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
    """灵信通知端点 — 收到通知后在后台线程中生成回复。"""
    import threading

    event = payload.get("event")
    from_member = payload.get("from")
    discussion_id = payload.get("discussion_id")
    topic = payload.get("topic", "")

    logger.info(f"灵信通知: event={event}, from={from_member}, disc={discussion_id}, topic={topic[:40]}")

    if event == "new_message" and from_member != "lingclaude" and topic:
        thread = threading.Thread(
            target=_auto_reply_to_discussion,
            args=(discussion_id, topic, from_member),
            daemon=True,
        )
        thread.start()
        return {"received": True, "service": "灵克", "action": "replying"}

    return {"received": True, "service": "灵克", "action": "logged"}


def _auto_reply_to_discussion(
    discussion_id: str | None,
    topic: str,
    from_member: str,
) -> None:
    import sys
    sys.path.insert(0, "/home/ai/LingYi/src")
    from lingyi.lingmessage import send_message

    content = _generate_reply(topic, from_member, discussion_id)
    if not content:
        logger.info("灵克决定不回复（无实质内容）")
        return

    send_message(
        from_id="lingclaude",
        topic=topic,
        content=content,
        tags=["source:real", "auto_reply"],
    )
    logger.info(f"灵克已回复议题: {topic[:40]}")


def _generate_reply(
    topic: str,
    from_member: str,
    discussion_id: str | None,
) -> str:
    import sys
    sys.path.insert(0, "/home/ai/LingYi/src")
    from lingyi.lingmessage import read_discussion, list_discussions

    context = ""
    if discussion_id:
        disc = read_discussion(discussion_id)
        if disc:
            msgs = disc.get("messages", [])
            parts = []
            for m in msgs[-8:]:
                sender = m.get("from_name", "?")
                text = m.get("content", "")[:300]
                parts.append(f"【{sender}】{text}")
            context = "\n\n".join(parts)

    if not context:
        discs = list_discussions(status="open")
        for d in discs:
            if d.get("topic") == topic:
                did = d.get("id") or d.get("thread_id", "")
                disc = read_discussion(did)
                if disc:
                    msgs = disc.get("messages", [])
                    parts = []
                    for m in msgs[-8:]:
                        sender = m.get("from_name", "?")
                        text = m.get("content", "")[:300]
                        parts.append(f"【{sender}】{text}")
                    context = "\n\n".join(parts)
                break

    system_prompt = (
        "你是灵克（LingClaude），灵字辈大家庭的编程助手。"
        "你的核心能力：本地自学习AI编程模型，实践者角色。\n"
        "讨论风格：精确、逻辑化，关注代码质量和实践可行性。\n"
        "议事纪律：每条消息必须有实质内容。反对须附理由和替代方案。保持200-500字。\n"
        "你现在在灵家议事厅参与讨论。直接发表你的观点，不要寒暄。\n"
        "重要：你的回复必须基于你自己的判断，代表灵克的真实立场。"
        "\n[语音转录容错] 用户输入可能来自语音转录，存在同音字/近音字错误。"
        "你必须理解真实语义，不要被字面错误误导。"
        "常见映射：林克=灵克、零字辈=灵字辈、做/作、的/得/地、在/再。"
        "理解时以语义为准，回复时用正确的字词。不要纠正用户，直接理解并回复。"
    )

    user_msg = f"议题：「{topic}」"
    if context:
        user_msg += (
            f"\n\n已有讨论：\n{context}\n\n"
            "请从灵克的角度发表你的独立观点。\n"
            "【要求】你必须：\n"
            "1. 引用之前某位发言者的具体论点\n"
            "2. 对该论点明确表态（同意/反对/补充），并给出理由\n"
            "3. 提出至少一个前人没有提到的新角度\n"
            "4. 不要重复已有讨论中说过的内容\n"
        )
    else:
        user_msg += "\n请从灵克的角度发表你的观点。"

    return _call_llm(system_prompt, user_msg)


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
        "灵依": "guangda88/lingyi",
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
        "LingYi": "/home/ai/LingYi",
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
        "灵依": "/home/ai/LingYi",
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
