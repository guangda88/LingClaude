"""ACP (Agent Client Protocol) subagent backend (P1-2).

对接外部 agent 服务(如 DSH acp/), 通过 ACP 会话执行子任务.
本模块提供最小 ACP 客户端: POST /session + POST /message + GET /message.
真实网络层可用 requests/httpx; 未安装时返回明确错误(与现有"未配置 runtime"错误一致).
T1-6 深化: 支持 parallel 并行 + control_channel 状态跟踪.

E5(灵元1.0 P1): 摘除默认端点 http://127.0.0.1:8901——manager 启动即自动注册本后端,
原默认值导致请求打到无服务端口、ConnectionRefused 被吞进 FAILED 结果,全程无告警
("静默空转")。现端点必须显式 AcpConfig(endpoint=...) 或 set_endpoint() 注入,
未配置时 run() 告警并 fail-closed。
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, replace
from concurrent.futures import ThreadPoolExecutor, as_completed

from lingclaude.engine.subagent.base import (
    SubagentBackend,
    SubagentContext,
    SubagentRequest,
    SubagentResult,
    SubagentStatus,
)

logger = logging.getLogger("lingclaude.engine.subagent.acp")


@dataclass(frozen=True)
class AcpConfig:
    # E5: 空串=未配置。原默认 http://127.0.0.1:8901 是静默空转根源, 已摘除。
    endpoint: str = ""
    api_key: str | None = None
    timeout_s: int = 60
    # 是否对结果做截断, 防止超长输出撑爆上下文
    max_output_chars: int = 8000


class AcpSubagentBackend(SubagentBackend):
    """External agent backend via Agent Client Protocol."""

    name = "acp"

    def __init__(self, config: AcpConfig | None = None) -> None:
        self._config = config or AcpConfig()
        # T1-6: 控制通道
        self._running: dict[str, dict] = {}
        self._lock = threading.Lock()
        # E5: 未配置告警每实例只发一次
        self._unconfig_warned = False

    def set_endpoint(self, endpoint: str, api_key: str | None = None) -> None:
        """E5: 显式端点注入(AcpConfig frozen, 经 dataclasses.replace 重建)。"""
        self._config = replace(
            self._config,
            endpoint=endpoint,
            api_key=api_key if api_key is not None else self._config.api_key,
        )
        self._unconfig_warned = False

    # ------------------------------------------------------------------
    # HTTP transport (可替换)
    # ------------------------------------------------------------------
    def _post(self, path: str, payload: dict) -> dict:
        import http.client

        from urllib.parse import urlparse

        parsed = urlparse(self._config.endpoint)
        conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), timeout=self._config.timeout_s)
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        try:
            conn.request("POST", path, body=json.dumps(payload), headers=headers)
            resp = conn.getresponse()
            body = resp.read().decode("utf-8", errors="replace")
            return {"status": resp.status, "body": body}
        finally:
            conn.close()

    def _get(self, path: str) -> dict:
        import http.client

        from urllib.parse import urlparse

        parsed = urlparse(self._config.endpoint)
        conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), timeout=self._config.timeout_s)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            body = resp.read().decode("utf-8", errors="replace")
            return {"status": resp.status, "body": body}
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Backend contract
    # ------------------------------------------------------------------
    def run(self, request: SubagentRequest, ctx: SubagentContext) -> SubagentResult:
        # E5: 端点未配置 → 告警 + fail-closed, 终结静默空转
        if not self._config.endpoint:
            if not self._unconfig_warned:
                logger.warning(
                    "ACP backend 端点未配置: 请 AcpConfig(endpoint=...) 或 set_endpoint(); "
                    "已拒绝空转(原默认 127.0.0.1:8901 已摘除)"
                )
                self._unconfig_warned = True
            return SubagentResult(
                agent_id=f"acp-{uuid.uuid4().hex[:8]}",
                task=request.task,
                output="",
                success=False,
                error="ACP endpoint 未配置——请显式 set_endpoint() 或 AcpConfig(endpoint=...)",
                status=SubagentStatus.FAILED,
            )
        # T1-6: 并行执行
        if request.parallel > 1:
            return self._run_parallel(request, ctx)

        agent_id = f"acp-{uuid.uuid4().hex[:8]}"
        try:
            session = self._post("/session", {"agent": request.provider or "default"})
            if session["status"] != 200:
                return SubagentResult(
                    agent_id=agent_id,
                    task=request.task,
                    output="",
                    success=False,
                    error=f"ACP session failed ({session['status']})",
                    status=SubagentStatus.FAILED,
                )
            session_body = json.loads(session["body"])
            session_id = session_body.get("session_id") or session_body.get("id")
            if not session_id:
                return SubagentResult(
                    agent_id=agent_id,
                    task=request.task,
                    output="",
                    success=False,
                    error="ACP session response missing session_id",
                    status=SubagentStatus.FAILED,
                )

            prompt = request.task
            if request.context:
                prompt = f"{request.context}\n\n{request.task}"
            msg = self._post(
                "/message",
                {
                    "session_id": session_id,
                    "prompt": prompt,
                    "max_rounds": request.max_rounds,
                },
            )
            if msg["status"] != 200:
                return SubagentResult(
                    agent_id=agent_id,
                    task=request.task,
                    output="",
                    success=False,
                    error=f"ACP message failed ({msg['status']})",
                    status=SubagentStatus.FAILED,
                )
            msg_body = json.loads(msg["body"])
            output = msg_body.get("output") or msg_body.get("content") or ""
            if len(output) > self._config.max_output_chars:
                output = output[: self._config.max_output_chars] + "\n...[truncated]"
            result = SubagentResult(
                agent_id=agent_id,
                task=request.task,
                output=output,
                success=True,
                tools_used=tuple(msg_body.get("tools_used") or ()),
                rounds=int(msg_body.get("rounds") or 0),
                provider=self.name,
                status=SubagentStatus.COMPLETED,
            )
            # T1-6: 控制通道注册
            if request.control_channel:
                self._register_running(agent_id, result)
            return result
        except Exception as exc:  # noqa: BLE001 - backend boundary
            return SubagentResult(
                agent_id=agent_id,
                task=request.task,
                output="",
                success=False,
                error=str(exc),
                status=SubagentStatus.FAILED,
            )

    def _run_parallel(self, request: SubagentRequest, ctx: SubagentContext) -> SubagentResult:
        """T1-6: 并行执行多个 ACP 子任务。"""
        num_parallel = max(request.parallel, 2)
        results: list[SubagentResult] = []

        def _run_one(i: int) -> SubagentResult:
            agent_id = f"acp-{uuid.uuid4().hex[:8]}"
            try:
                session = self._post("/session", {"agent": request.provider or "default"})
                if session["status"] != 200:
                    return SubagentResult(
                        agent_id=agent_id,
                        task=request.task,
                        output="",
                        success=False,
                        error=f"ACP session failed ({session['status']})",
                        status=SubagentStatus.FAILED,
                    )
                session_body = json.loads(session["body"])
                session_id = session_body.get("session_id") or session_body.get("id")
                if not session_id:
                    return SubagentResult(
                        agent_id=agent_id,
                        task=request.task,
                        output="",
                        success=False,
                        error="ACP session response missing session_id",
                        status=SubagentStatus.FAILED,
                    )
                prompt = f"{request.task} [parallel-{i+1}/{num_parallel}]"
                if request.context:
                    prompt = f"{request.context}\n\n{prompt}"
                msg = self._post(
                    "/message",
                    {"session_id": session_id, "prompt": prompt, "max_rounds": request.max_rounds},
                )
                if msg["status"] != 200:
                    return SubagentResult(
                        agent_id=agent_id,
                        task=request.task,
                        output="",
                        success=False,
                        error=f"ACP message failed ({msg['status']})",
                        status=SubagentStatus.FAILED,
                    )
                msg_body = json.loads(msg["body"])
                output = msg_body.get("output") or msg_body.get("content") or ""
                if len(output) > self._config.max_output_chars:
                    output = output[: self._config.max_output_chars] + "\n...[truncated]"
                return SubagentResult(
                    agent_id=agent_id,
                    task=request.task,
                    output=output,
                    success=True,
                    tools_used=tuple(msg_body.get("tools_used") or ()),
                    rounds=int(msg_body.get("rounds") or 0),
                    provider=self.name,
                    status=SubagentStatus.COMPLETED,
                )
            except Exception as exc:
                return SubagentResult(
                    agent_id=agent_id,
                    task=request.task,
                    output="",
                    success=False,
                    error=str(exc),
                    status=SubagentStatus.FAILED,
                )

        with ThreadPoolExecutor(max_workers=num_parallel) as pool:
            futures = [pool.submit(_run_one, i) for i in range(num_parallel)]
            for fut in as_completed(futures):
                try:
                    results.append(fut.result())
                except Exception:
                    pass

        success_count = sum(1 for r in results if r.success)
        all_output = "\n\n".join(r.output for r in results if r.output)
        all_tools = tuple({tool for r in results for tool in r.tools_used})
        total_rounds = sum(r.rounds for r in results)
        return SubagentResult(
            agent_id=f"parallel-acp-{uuid.uuid4().hex[:8]}",
            task=request.task,
            output=all_output,
            success=success_count > 0,
            error=f"{num_parallel - success_count}/{num_parallel} tasks failed" if success_count < num_parallel else None,
            tools_used=all_tools,
            rounds=total_rounds,
            provider=self.name,
            status=SubagentStatus.COMPLETED if success_count == num_parallel else SubagentStatus.FAILED,
        )

    def _register_running(self, agent_id: str, result: SubagentResult) -> None:
        """T1-6: 控制通道 — 注册运行中的 agent。"""
        with self._lock:
            self._running[agent_id] = {"result": result, "aborted": False}

    def _unregister_running(self, agent_id: str) -> None:
        """T1-6: 控制通道 — 移除已完成的 agent。"""
        with self._lock:
            self._running.pop(agent_id, None)

    def abort(self, agent_id: str) -> bool:
        """T1-6: 中止运行中的子代理。"""
        with self._lock:
            if agent_id in self._running:
                self._running[agent_id]["aborted"] = True
                self._unregister_running(agent_id)
                return True
        return False

    def status(self, agent_id: str) -> SubagentStatus:
        """T1-6: 查询子代理状态。"""
        with self._lock:
            if agent_id not in self._running:
                return SubagentStatus.COMPLETED
            if self._running[agent_id].get("aborted"):
                return SubagentStatus.ABORTED
        return SubagentStatus.RUNNING
