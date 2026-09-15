"""MCP subagent backend — 把外部 MCP server 包装成 SubagentBackend（第三后端）。

对标 Ekko Studio 的"运行时适配器"：任意实现标准 MCP 协议的 server
（stdio 子进程或 HTTP 端点）都可作为子代理后端被 SubagentManager 调度。

协议约定（MCP server 需暴露以下 tools）：
- run(task, context="", max_rounds=5, config={}) -> {output, success, error, agent_id, status}
  执行子任务，返回统一结果结构。
- abort(agent_id) -> bool    （可选，控制通道）
- status(agent_id) -> str    （可选，控制通道）

复用 MCPClientPool 做连接缓存 + 失效重连（stdio spawn 成本高）。

设计纪律：
- fail-closed：connect 失败/无 run tool → SubagentResult(success=False, error=明确原因)
- 结果截断：防止超长输出撑爆上下文（max_output_chars，默认 8000，与 ACP 后端一致）
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from typing import Any

from lingclaude.engine.mcp_client import MCPClientPool, get_client_pool
from lingclaude.engine.subagent.base import (
    SubagentBackend,
    SubagentContext,
    SubagentRequest,
    SubagentResult,
    SubagentStatus,
)

logger = logging.getLogger("lingclaude.engine.subagent.mcp")


@dataclass(frozen=True)
class MCPBackendConfig:
    """MCP 后端配置。

    transport: "stdio" 或 "http"
    command:   stdio 用 — 子进程启动命令（如 ["python", "lingzhi/mcp_server.py"]）
    url:       http 用 — 服务端点（如 "http://127.0.0.1:5001/mcp"）
    server_key: 连接池 key（默认自动生成；显式指定可共享同一 server 的连接）
    timeout_s: 单次调用超时
    max_output_chars: 结果截断上限（防上下文撑爆）
    """

    transport: str = "stdio"
    command: tuple[str, ...] = ()
    url: str = ""
    server_key: str = ""
    timeout_s: float = 60.0
    max_output_chars: int = 8000

    def __post_init__(self) -> None:
        if self.transport not in ("stdio", "http"):
            raise ValueError(f"MCP backend transport must be 'stdio' or 'http', got {self.transport!r}")
        if self.transport == "stdio" and not self.command:
            raise ValueError("MCP stdio backend requires command")
        if self.transport == "http" and not self.url:
            raise ValueError("MCP http backend requires url")


class MCPSubagentBackend(SubagentBackend):
    """External agent backend via MCP (stdio/http)."""

    name = "mcp"

    def __init__(
        self,
        config: MCPBackendConfig | None = None,
        pool: MCPClientPool | None = None,
    ) -> None:
        self._config = config or MCPBackendConfig(transport="stdio", command=("python3", "-c", "pass"))
        self._pool = pool or get_client_pool()
        self._lock = threading.Lock()
        # 控制通道：agent_id -> 最近状态缓存（MCP server 无状态时本地兜底）
        self._local_status: dict[str, SubagentStatus] = {}

    @property
    def key(self) -> str:
        if self._config.server_key:
            return self._config.server_key
        if self._config.transport == "stdio":
            return "mcp:" + " ".join(self._config.command)
        return "mcp:" + self._config.url

    # ------------------------------------------------------------------
    # 连接池辅助
    # ------------------------------------------------------------------
    def _get_client(self) -> Any:
        """取（或建）缓存 client；失败抛 RuntimeError（由 run 收敛）。"""
        res = self._pool.get(
            self.key,
            self._config.transport,
            command=self._config.command or None,
            url=self._config.url or None,
            timeout=self._config.timeout_s,
        )
        if res.is_error:
            raise RuntimeError(f"MCP backend connect failed: {res.error}")
        return res.data

    def _has_tool(self, client: Any, name: str) -> bool:
        try:
            tools = client.list_tools()
            if tools.is_error:
                return False
            return any(t.name == name for t in tools.data)
        except Exception:  # noqa: BLE001 — 探测失败视为不可用
            return False

    # ------------------------------------------------------------------
    # Backend contract
    # ------------------------------------------------------------------
    def run(self, request: SubagentRequest, ctx: SubagentContext) -> SubagentResult:
        try:
            client = self._get_client()
        except RuntimeError as e:
            logger.warning("MCP backend unavailable: %s", e)
            return SubagentResult(
                agent_id=f"mcp-{uuid.uuid4().hex[:8]}",
                task=request.task,
                output="",
                success=False,
                error=str(e),
                provider=self.name,
                status=SubagentStatus.FAILED,
            )

        agent_id = f"mcp-{uuid.uuid4().hex[:8]}"
        payload: dict[str, Any] = {
            "task": request.task,
            "context": request.context,
            "max_rounds": request.max_rounds,
            "config": request.config,
        }
        if request.persona:
            payload["persona"] = request.persona
        if request.tool_filter:
            payload["tool_filter"] = list(request.tool_filter)

        # 本地兜底状态（供 status() 查询）
        with self._lock:
            self._local_status[agent_id] = SubagentStatus.RUNNING

        try:
            res = client.call_tool("run", payload)
        except Exception as e:  # noqa: BLE001 — 传输异常统一收敛
            res = None
            err = f"MCP call_tool('run') failed: {e}"
            with self._lock:
                self._local_status[agent_id] = SubagentStatus.FAILED
            return SubagentResult(
                agent_id=agent_id,
                task=request.task,
                output="",
                success=False,
                error=err,
                provider=self.name,
                status=SubagentStatus.FAILED,
            )

        if res is None or res.is_error:
            error = str(res.error) if res is not None else "MCP run returned no result"
            with self._lock:
                self._local_status[agent_id] = SubagentStatus.FAILED
            return SubagentResult(
                agent_id=agent_id,
                task=request.task,
                output="",
                success=False,
                error=error,
                provider=self.name,
                status=SubagentStatus.FAILED,
            )

        # 解析统一结果结构
        data = res.data
        if isinstance(data, dict):
            output = str(data.get("output", ""))
            success = bool(data.get("success", True))
            error = data.get("error")
            server_agent_id = data.get("agent_id") or agent_id
            raw_status = data.get("status", "completed" if success else "failed")
        else:
            output = str(data)
            success = True
            error = None
            server_agent_id = agent_id
            raw_status = "completed"

        if len(output) > self._config.max_output_chars:
            output = output[: self._config.max_output_chars] + "\n...[truncated]"

        status = self._map_status(raw_status)
        with self._lock:
            self._local_status[server_agent_id] = status
        return SubagentResult(
            agent_id=server_agent_id,
            task=request.task,
            output=output,
            success=success,
            error=error,
            provider=self.name,
            status=status,
        )

    def abort(self, agent_id: str) -> bool:
        """尝试 MCP abort；server 未提供则本地置 ABORTED（尽力而为）。"""
        try:
            client = self._get_client()
            if self._has_tool(client, "abort"):
                res = client.call_tool("abort", {"agent_id": agent_id})
                if res.is_ok and bool(res.data):
                    with self._lock:
                        self._local_status[agent_id] = SubagentStatus.ABORTED
                    return True
        except Exception:  # noqa: BLE001 — abort 失败不阻塞
            pass
        # 尽力而为：本地置 ABORTED（server 无控制通道时语义兜底）
        with self._lock:
            self._local_status[agent_id] = SubagentStatus.ABORTED
        return True

    def status(self, agent_id: str) -> SubagentStatus:
        try:
            client = self._get_client()
            if self._has_tool(client, "status"):
                res = client.call_tool("status", {"agent_id": agent_id})
                if res.is_ok:
                    return self._map_status(str(res.data))
        except Exception:  # noqa: BLE001 — 查询失败回退本地
            pass
        with self._lock:
            return self._local_status.get(agent_id, SubagentStatus.COMPLETED)

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _map_status(raw: str) -> SubagentStatus:
        try:
            return SubagentStatus(raw.lower())
        except ValueError:
            return SubagentStatus.COMPLETED
