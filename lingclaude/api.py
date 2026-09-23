from __future__ import annotations

"""灵克 HTTP API — 灵字辈成员通过 HTTP 调用灵克能力。

端口 8700，供灵通等成员调用。
"""

import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

from fastapi import FastAPI, HTTPException, Security  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from fastapi.security import APIKeyHeader  # noqa: E402
from pydantic import BaseModel  # noqa: E402

# T0-3: 会话级审批回路 — 决策经 record_permission_decision 写入会话 store，
# CodingRuntime.execute_tool / sensitive_path_gate 通过 get_permission_store 读取（同一注册表）
from lingclaude.core.permissions import record_permission_decision
# P4: 引擎进程 LingBus 任务消费者（原仅 CLI 交互进程消费；env 门控启动，见 _start_bus_consumer_if_enabled）
from lingclaude.coordination.bus_consumer import start_bus_consumer_background
# I3 (2026-09-15): 外部查询业务逻辑抽离至 seams/external_query（api.py 986→667 行）。
# 以下 re-export 兼容层保证既有引用（_route_question/_list_projects/_analyze_file/
# _analyze_dir 及测试 test_w5p1_dynamic_llm.py 直接引用）行为不变；repo_path 现仅由
# external_query 内部使用，此处不再顶层导入。新代码请直接从 lingclaude.seams.external_query 导入。
from lingclaude.seams.external_query import (  # noqa: E402
    _STATIC_LLM_FALLBACK,
    _PROXY_API_KEY,
    _PROXY_URL,
    _analyze_dir,
    _analyze_file,
    _call_llm,
    _call_llm_direct,
    _format_projects,
    _http_get_json,
    _list_projects,
    _llm_providers_snapshot,
    _load_env_keys,
    _query_github_stars,
    _query_pypi_downloads,
    _query_recent_commits,
    _query_versions,
    _resolve_llm_chain,
    _route_question,
)

logger = logging.getLogger(__name__)

# API Key 认证
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

# 从环境变量或配置文件读取 API Keys
_VALID_API_KEYS = set()
_api_keys_env = os.environ.get("LINGCLAUDE_API_KEYS", "")
if _api_keys_env:
    _VALID_API_KEYS.update(key.strip() for key in _api_keys_env.split(","))

async def verify_api_key(api_key: str = Security(API_KEY_HEADER)):
    """验证 API Key。

    请求时惰性读取环境变量（而非仅导入时快照）：修复非标准测试收集顺序下
    api 模块早于 fixture 设置 LINGCLAUDE_API_KEYS 被导入 → 密钥集缓存为空 →
    全端点 401 的测试污染。生产首次请求代价一次 env 读取，可忽略。
    """
    env = os.environ.get("LINGCLAUDE_API_KEYS", "")
    if env:
        _VALID_API_KEYS.update(k.strip() for k in env.split(",") if k.strip())
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
# F4 修复:webui 默认端口 13458 必须放行。env LINGCLAUDE_CORS_ORIGINS 可覆盖。
# F8 决策锁定(2026-08-29):api.py 全部端点只用 GET/POST,allow_methods 不含
# PUT/PATCH;e2e test_cors_methods_cover_all_routes 防回归。
_default_cors_origins = (
    "http://localhost:3000,"  # npm dev server
    "http://localhost:13458,"  # webui default
    "http://127.0.0.1:13458,"  # webui default loopback
    "http://localhost:8700"  # engine self-loopback
)
_cors_env = os.environ.get("LINGCLAUDE_CORS_ORIGINS", _default_cors_origins)
_cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key"],
)


def _register_webui_seam_safe() -> None:
    """注册 webui seam 到 SeamRegistry（fail-soft：失败只警告，不阻断启动）。

    接线修复：webui_seam.py 此前仅在 __main__ 自检调用，未在主循环接线。
    """
    try:
        from lingclaude.webui_seam import register_webui_seam
        register_webui_seam()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "webui seam 注册失败（不影响 API 启动）: %s: %s",
            type(e).__name__, e,
        )


_BUS_CONSUMER_STOP: Any = None


def _start_bus_consumer_if_enabled() -> None:
    """P4: 引擎进程启用 LingBus 任务消费者（env 门控，RFC v0.1 §3 A1-1）。

    cli 交互进程默认消费（repl_turn 兼容壳）；api 引擎进程原不消费 ——
    灵通派给灵克的任务在引擎模式下无人应答。统一走 coordination/bus_consumer
    模块，避免 repl_turn 的 CLI 专属接线被 API 复用。

    门控与 conftest.py 对齐：LINGCLAUDE_BUS_LISTENER=0 显式关闭（测试默认），
    未设置/1 时开启。注册失败只告警不阻断 API 启动（fail-soft）。
    """
    global _BUS_CONSUMER_STOP
    if os.environ.get("LINGCLAUDE_BUS_LISTENER") == "0":
        return
    try:
        _BUS_CONSUMER_STOP = start_bus_consumer_background(
            thread_name="lingclaude-api-bus-consumer",
        )
        logger.info("LingBus consumer enabled for API engine (LINGCLAUDE_BUS_LISTENER)")
    except Exception as e:  # noqa: BLE001 — 消费者启动失败不阻断 API
        logger.warning("LingBus consumer 启动失败（不影响 API 启动）: %s: %s", type(e).__name__, e)


_register_webui_seam_safe()
_start_bus_consumer_if_enabled()


class AskRequest(BaseModel):
    question: str
    context: str = ""


class AskResponse(BaseModel):
    answer: str
    source: str = "lingclaude"


class PermissionRequest(BaseModel):
    session_id: str = ""
    decision: str  # allow / deny / always_allow / allow_persist
    tool_name: str = ""
    reason: str = ""
    command: str = ""  # P0 通电（2026-09-23）：被批命令原文，用于前缀沉淀资产化


class PermissionResponse(BaseModel):
    success: bool
    decision: str


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
        "name": "灵克 (lingclaude)",
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


@app.get("/live/events")
async def live_events(since: int = 0, api_key: str = Security(verify_api_key)):
    """webUI `/live` 增量事件轮询端点 — 返回 since 之后的新工具执行事件。

    供 lingclaude-webui Rust server 轮询并转发为前端 LiveWireEvent。
    """
    from lingclaude.engine.tool_pipeline import read_tool_events

    events, latest = read_tool_events(since)
    return {"events": events, "latest": latest}


# ---------------------------------------------------------------------------
# C2: Session snapshot 接口（webui LingBus 断线续传）
# ---------------------------------------------------------------------------

class SessionSnapshotRequest(BaseModel):
    session_id: str
    project_path: str = ""


@app.get("/sessions")
async def list_sessions(api_key: str = Security(verify_api_key)):
    """列出所有会话（供 webUI 侧会话管理）。"""
    from lingclaude.core.session import SessionManager

    mgr = SessionManager()
    sessions = mgr.list_sessions()
    return {"sessions": list(sessions)}


@app.get("/sessions/{session_id}")
async def get_session(session_id: str, project_path: str = "", api_key: str = Security(verify_api_key)):
    """获取单个会话详情（供 webUI 快照恢复）。"""
    from lingclaude.core.session import SessionManager

    mgr = SessionManager()
    result = mgr.load(session_id, project_path)
    if result.is_error:
        raise HTTPException(404, f"Session not found: {result.error}")
    return result.data.to_dict_redacted()


@app.post("/sessions/snapshot")
async def create_snapshot(req: SessionSnapshotRequest, api_key: str = Security(verify_api_key)):
    """创建会话快照（供 webUI 断线续传）。"""
    from lingclaude.core.session import SessionManager

    mgr = SessionManager()
    # 先加载 session
    load_result = mgr.load(req.session_id, req.project_path)
    if load_result.is_error:
        raise HTTPException(404, f"Session not found: {req.session_id}")
    snap_result = mgr.snapshot(load_result.data)
    if snap_result.is_error:
        raise HTTPException(500, f"Snapshot failed: {snap_result.error}")
    return {"path": str(snap_result.data), "session_id": req.session_id}


@app.post("/sessions/{session_id}/stop")
async def stop_session(session_id: str, project_path: str = "", api_key: str = Security(verify_api_key)):
    """停止会话：标记过期（供 webUI 侧会话管理）。"""
    from lingclaude.core.session import SessionManager

    mgr = SessionManager()
    result = mgr.stop(session_id, project_path)
    if result.is_error:
        raise HTTPException(404, f"Failed to stop session: {result.error}")
    return result.data.to_dict_redacted()


@app.get("/sessions/{session_id}/projection")
async def get_session_projection(session_id: str, view: str = "all", api_key: str = Security(verify_api_key)):
    """T3-2/案 5: 会话投影 — 多视角分析（token 用量/工具调用频率/轮次统计）。

    修复死接线第 5 案：session_projection.py 已存在但零消费方。
    """
    from lingclaude.core.session import SessionManager
    from lingclaude.core.session_projection import (
        project_session,
        project_tokens,
        project_tools,
        project_rounds,
    )

    mgr = SessionManager()
    result = mgr.load(session_id)
    if result.is_error:
        raise HTTPException(404, f"Session not found: {session_id}")
    session = result.data

    if view == "tokens":
        return project_tokens(session).to_dict()
    elif view == "tools":
        return project_tools(session).to_dict()
    elif view == "rounds":
        return project_rounds(session).to_dict()
    else:
        return project_session(session)


# ── T3-1/任务1: LACP 插件市场端点（接线 marketplace.py 死接线第6 案）──


@app.post("/marketplace/upload")
async def marketplace_upload(
    name: str,
    version: str,
    code: str,  # base64 or plain text
    uploader: str = "anonymous",
    api_key: str = Security(verify_api_key),
):
    """上传插件（含静态安全分析 + 风险评级）。

    修复死接线第6 案：marketplace.py 已有 PluginMarketplace 但零消费方。
    """
    from lingclaude.lacp.marketplace import get_marketplace
    from lingclaude.lacp.manifest import Plugin, Interface, Transport

    mp = get_marketplace()
    manifest = Plugin(
        name=name,
        version=version,
        owner=uploader,
        description="Uploaded via /marketplace/upload",
        interface=Interface(input_schema={}, output_schema={}),
        transports=[Transport.CLI],
    )
    success, error = mp.upload(manifest, code.encode("utf-8"), uploader, secret=b"")
    return {"success": success, "error": error, "plugin_id": name}


@app.get("/marketplace/list")
async def marketplace_list(api_key: str = Security(verify_api_key)):
    """列出已上传的插件 + 风险等级 + 信誉评分。"""
    from lingclaude.lacp.marketplace import get_marketplace

    mp = get_marketplace()
    plugins = mp.list_plugins() if hasattr(mp, "list_plugins") else []
    return {"plugins": plugins}


@app.get("/marketplace/reputation/{plugin_id}")
async def marketplace_reputation(plugin_id: str, api_key: str = Security(verify_api_key)):
    """查询插件信誉评分（综合安全评分 + 用户评分）。"""
    from lingclaude.lacp.marketplace import get_marketplace

    mp = get_marketplace()
    return {
        "plugin_id": plugin_id,
        "rating": mp.get_rating(plugin_id),
        "trust_score": mp.get_trust_score(plugin_id),
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


@app.post("/ask/stream")
async def ask_stream(req: AskRequest, api_key: str = Security(verify_api_key)):
    """流式提问端点 — 供 lingclaude-webui `/chat` SSE 桥接。

    基于 QueryEngine.stream_submit 的逐事件流（message_start / message_delta /
    tool_call_start / tool_call_end / status / error / message_stop），
    以 text/event-stream 逐事件推送。
    """
    from lingclaude.core.query_engine import QueryEngine

    engine_result = QueryEngine.from_config_file()
    if engine_result.is_error:
        raise HTTPException(500, f"QueryEngine 初始化失败: {engine_result.error}")

    engine = engine_result.data
    prompt = req.question
    if req.context:
        prompt = f"上下文：{req.context}\n\n问题：{req.question}"

    def event_gen():
        yield "retry: 3000\n\n"
        for ev in engine.stream_submit(prompt):
            event_type = ev.get("type", "unknown")
            # 对齐前端 SSEEvent：message_delta → text，message_stop → done
            if event_type == "message_delta":
                payload = {"type": "text", "content": ev.get("text", "")}
            elif event_type == "message_stop":
                payload = {"type": "done", "session_id": ev.get("session_id", "")}
            elif event_type == "tool_call_start":
                payload = {
                    "type": "tool_start",
                    "id": ev.get("call_id", ""),
                    "name": ev.get("name", ""),
                    "arguments": ev.get("arguments", {}),
                }
            elif event_type == "tool_call_end":
                payload = {
                    "type": "tool_result",
                    "id": ev.get("call_id", ""),
                    "name": ev.get("name", ""),
                    "output": str(ev.get("output_preview", "")),
                    "success": not ev.get("is_error", False),
                    "duration_ms": 0,
                }
            elif event_type == "error":
                payload = {"type": "error", "message": str(ev.get("error", "error"))}
            elif event_type == "status":
                payload = {"type": "warning", "message": str(ev.get("message", ""))}
            else:
                payload = {"type": "text", "content": ""}
            yield f"event: {event_type}\ndata: {__import__('json').dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/permission", response_model=PermissionResponse)
async def permission(req: PermissionRequest, api_key: str = Security(verify_api_key)):
    """webUI 审批决策入口 — 对接 governance_v2 / verification_gate。

    lingclaude-webui 的 `/chat/permission` 桥接此端点。决策记录到审批日志，
    并接入 GovernanceRouter（工具审批作为治理提案流转）；未知决策返回 400。
    """
    valid = {"allow", "deny", "always_allow", "allow_persist"}
    if req.decision not in valid:
        raise HTTPException(400, f"无效决策: {req.decision}（允许: {sorted(valid)}）")

    # 审批日志：供 governance_v2 / verification_gate 审计
    _log_approval(
        session_id=req.session_id,
        decision=req.decision,
        tool_name=req.tool_name,
        reason=req.reason,
    )

    # 接入 GovernanceRouter：工具审批决策作为治理投票流转（失败不阻塞 webUI 响应）
    try:
        from lingclaude.governance.governance_router import GovernanceRouter

        router = GovernanceRouter()
        if req.decision == "always_allow" or req.decision == "allow_persist":
            router.propose(
                title=f"工具审批: {req.tool_name or 'unknown'}",
                proposer="webui",
                body=f"session={req.session_id} decision={req.decision} reason={req.reason}",
            )
        else:
            router.propose(
                title=f"工具审批决策: {req.decision}",
                proposer="webui",
                body=f"tool={req.tool_name or 'unknown'} session={req.session_id} reason={req.reason}",
            )
    except Exception as e:  # noqa: BLE001 — 治理后端不可用时降级为纯日志
        logger.warning("GovernanceRouter 不可用，仅记录审批日志: %s", e)

    # T0-3: 回灌 PermissionStore（审批回路 — 会话级，与 execute_tool 同一注册表）
    # P0 通电（2026-09-23）：command 字段透传 → always_allow/allow_persist 时沉淀前缀资产
    record_permission_decision(req.session_id or "default", req.tool_name or "unknown",
                               req.decision, command=req.command)
    logger.info(f"T0-3: permission recorded session={req.session_id} tool={req.tool_name} "
                f"decision={req.decision} command={'<redacted-len>' if req.command else 'none'}")

    return PermissionResponse(success=True, decision=req.decision)


class PermissionModeRequest(BaseModel):
    """T1-2 深化: 设置全局 permission mode 请求体。"""
    mode: str


@app.get("/permission/mode")
async def get_permission_mode_endpoint(api_key: str = Security(verify_api_key)):
    """T1-2 深化: 读取当前全局 permission mode（auto/ask/strict）。"""
    from lingclaude.core.permissions import get_permission_mode
    return {"mode": get_permission_mode()}


@app.post("/permission/mode")
async def set_permission_mode_endpoint(
    req: PermissionModeRequest,
    api_key: str = Security(verify_api_key),
):
    """T1-2 深化: 设置全局 permission mode（auto/ask/strict），持久化落盘。"""
    from lingclaude.core.permissions import set_permission_mode
    ok = set_permission_mode(req.mode)
    if not ok:
        raise HTTPException(400, f"无效 mode: {req.mode}（允许: auto/ask/strict）")
    return {"success": True, "mode": req.mode.lower()}


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


class LingMessageNotifyRequest(BaseModel):
    """F7:灵信通知端点 Pydantic 模型 — 替代 payload: dict。

    ⚠️ 必须在 @app.post 之前定义,否则 FastAPI 把 req 当 query param(422)。
    """
    event: str | None = None
    sender: str | None = None
    topic: str = ""
    thread_id: str | None = None
    data: dict[str, Any] = {}


@app.post("/api/lingmessage/notify")
async def lingmessage_notify(req: LingMessageNotifyRequest, api_key: str = Security(verify_api_key)):
    """灵信通知端点 — 记录通知，不自动回复。

    auto_reply 于 2026-04-21 物理删除（不是注释禁用，是删除函数）。
    灵克在治理线程中的发言应由 Crush 会话（真实人类/AI 交互）产生。
    如果需要恢复 auto_reply，必须通过灵委会提案 + 代码审查。
    """
    # F7:req 是 LingMessageNotifyRequest
    event = req.event
    from_member = req.sender
    topic = req.topic
    thread_id = req.thread_id

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

    from lingclaude.lacp.cross_repo_seam import ensure_import_path

    ensure_import_path("lingmessage")
    from lingmessage.lingbus import LingBus

    bus = LingBus(bus_dir=Path.home() / ".lingmessage")
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


_APPROVAL_LOG: list[dict[str, str]] = []


def _log_approval(session_id: str, decision: str, tool_name: str, reason: str) -> None:
    """记录 webUI 审批决策 — 供 governance_v2 / verification_gate 审计。

    内存日志（进程退出即失效）；如需持久化可扩展为文件/数据库追加。
    """
    _APPROVAL_LOG.append(
        {
            "session_id": session_id,
            "decision": decision,
            "tool_name": tool_name,
            "reason": reason,
            "ts": __import__("datetime").datetime.now().isoformat(),
        }
    )


def run_server(host: str = "127.0.0.1", port: int = 8700):  # nosec B104 — 默认本地绑定
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    # N6-fix: python -m lingclaude.api 现在真正启动 server (此前缺 __main__ 块)
    import argparse

    _parser = argparse.ArgumentParser(description="灵克 HTTP API (端口 8700)")
    _parser.add_argument("--host", default="127.0.0.1", help="绑定地址 (默认 127.0.0.1)")
    _parser.add_argument("--port", type=int, default=8700, help="绑定端口 (默认 8700)")
    _args = _parser.parse_args()
    run_server(host=_args.host, port=_args.port)
