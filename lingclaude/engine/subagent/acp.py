"""ACP (Agent Client Protocol) subagent backend (P1-2).

对接外部 agent 服务(如 DSH acp/), 通过 ACP 会话执行子任务.
本模块提供最小 ACP 客户端: POST /session + POST /message + GET /message.
真实网络层可用 requests/httpx; 未安装时返回明确错误(与现有"未配置 runtime"错误一致).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from lingclaude.engine.subagent.base import (
    SubagentBackend,
    SubagentContext,
    SubagentRequest,
    SubagentResult,
)


@dataclass(frozen=True)
class AcpConfig:
    endpoint: str = "http://127.0.0.1:8901"
    api_key: str | None = None
    timeout_s: int = 60
    # 是否对结果做截断, 防止超长输出撑爆上下文
    max_output_chars: int = 8000


class AcpSubagentBackend(SubagentBackend):
    """External agent backend via Agent Client Protocol."""

    name = "acp"

    def __init__(self, config: AcpConfig | None = None) -> None:
        self._config = config or AcpConfig()

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
            )
        except Exception as exc:  # noqa: BLE001 - backend boundary
            return SubagentResult(
                agent_id=agent_id,
                task=request.task,
                output="",
                success=False,
                error=str(exc),
            )
