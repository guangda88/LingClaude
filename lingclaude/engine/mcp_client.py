"""MCP 标准 client — stdio/HTTP 传输 + tools/list 发现（T1-5）。

对标 AtomCode `mcp/`（5022 行）的 JSON-RPC 协议层：
- MCPStdioClient: spawn 子进程，JSON-RPC over stdio（initialize → tools/list → tools/call）
- MCPHttpClient: HTTP/SSE 传输（streamable HTTP 的 POST /tools/call 路径）

与 mcp_proxy.py 的进程内模块加载互补：本模块服务"标准协议"的 MCP server
（外部进程或远程 HTTP），mcp_proxy 保持"灵族内部 Python 模块"通道。
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from lingclaude.core.types import Result

logger = logging.getLogger(__name__)

_PROTOCOL_VERSION = "2025-03-26"


@dataclass
class MCPTool:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


class _JSONRPCError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"[JSON-RPC {code}] {message}")
        self.code = code
        self.message = message


class MCPStdioClient:
    """stdio 传输 MCP client — spawn 子进程 + 行式 JSON-RPC。

    用法：
        client = MCPStdioClient(command=["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"])
        client.connect()
        tools = client.list_tools()
        result = client.call_tool("read_file", {"path": "/tmp/x"})
        client.close()
    """

    def __init__(self, command: list[str], cwd: str | None = None, timeout: float = 30.0) -> None:
        self._command = list(command)
        self._cwd = cwd
        self._timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._next_id = 0
        self._lock = threading.Lock()

    # ----- 生命周期 -----

    def connect(self) -> Result[None]:
        if self._proc is not None:
            return Result.ok(None)
        try:
            self._proc = subprocess.Popen(  # nosec B603 — command 来自受信配置（LACP manifest）
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self._cwd,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as e:
            return Result.fail(f"MCP server binary not found: {self._command[0]}", code="SPAWN_FAILED")
        init_res = self._request("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "lingclaude", "version": "0.2"},
        })
        if init_res.is_error:
            self.close()
            return init_res
        # notifications/initialized 后正式可用
        self._send_notification("notifications/initialized", {})
        logger.info("MCP stdio client connected: %s", self._command[0])
        return Result.ok(None)

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001 — 关闭失败不阻塞
                proc.kill()

    # ----- MCP 方法 -----

    def list_tools(self) -> Result[list[MCPTool]]:
        res = self._request("tools/list", {})
        if res.is_error:
            return res
        tools: list[MCPTool] = []
        for item in (res.data.get("tools") or []):
            tools.append(MCPTool(
                name=str(item.get("name", "")),
                description=str(item.get("description", "")),
                input_schema=item.get("inputSchema") or {},
            ))
        return Result.ok(tools)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Result[Any]:
        res = self._request("tools/call", {
            "name": name,
            "arguments": arguments,
        })
        if res.is_error:
            return res
        content = res.data.get("content") or []
        is_error = bool(res.data.get("isError"))
        # 提取 text 内容
        text_parts = [
            c.get("text", "") for c in content if c.get("type") == "text"
        ]
        output = "\n".join(text_parts) if text_parts else content
        if is_error:
            return Result.fail(str(output)[:500], code="TOOL_ERROR")
        return Result.ok(output)

    # ----- JSON-RPC 传输 -----

    def _request(self, method: str, params: dict[str, Any]) -> Result[dict[str, Any]]:
        with self._lock:
            self._next_id += 1
            req_id = self._next_id
            payload = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params,
            }
            try:
                self._write_line(json.dumps(payload))
                resp = self._read_line()
                data = json.loads(resp)
            except Exception as e:  # noqa: BLE001 — 传输层错误统一包装
                return Result.fail(f"stdio transport error: {e}", code="TRANSPORT_ERROR")
            if "error" in data and data["error"]:
                err = data["error"]
                return Result.fail(f"JSON-RPC error: {err}", code="JSONRPC_ERROR")
            return Result.ok(data.get("result") or {})

    def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        try:
            self._write_line(json.dumps({
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }))
        except Exception:  # noqa: BLE001 — notification 失败可忽略
            pass

    def _write_line(self, line: str) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("MCP client not connected")
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()

    def _read_line(self) -> str:
        if self._proc is None or self._proc.stdout is None:
            raise RuntimeError("MCP client not connected")
        import select

        fd = self._proc.stdout.fileno()
        readable, _, _ = select.select([fd], [], [], self._timeout)
        if not readable:
            raise TimeoutError(f"MCP request timed out after {self._timeout}s")
        line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError("MCP server closed stdout")
        return line.strip()


class MCPHttpClient:
    """HTTP/SSE 传输 MCP client — streamable HTTP（POST JSON-RPC）。

    简化实现：单请求-响应模式（不支持 server→client streaming），
    适用于 HTTP 版 MCP server（tools/list + tools/call）。
    """

    def __init__(self, url: str, timeout: float = 30.0, headers: dict[str, str] | None = None) -> None:
        self._url = url.rstrip("/")
        self._timeout = timeout
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **(headers or {}),
        }
        self._next_id = 0

    def list_tools(self) -> Result[list[MCPTool]]:
        res = self._request("tools/list", {})
        if res.is_error:
            return res
        tools: list[MCPTool] = []
        for item in (res.data.get("tools") or []):
            tools.append(MCPTool(
                name=str(item.get("name", "")),
                description=str(item.get("description", "")),
                input_schema=item.get("inputSchema") or {},
            ))
        return Result.ok(tools)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Result[Any]:
        res = self._request("tools/call", {"name": name, "arguments": arguments})
        if res.is_error:
            return res
        content = res.data.get("content") or []
        is_error = bool(res.data.get("isError"))
        text_parts = [c.get("text", "") for c in content if c.get("type") == "text"]
        output = "\n".join(text_parts) if text_parts else content
        if is_error:
            return Result.fail(str(output)[:500], code="TOOL_ERROR")
        return Result.ok(output)

    def _request(self, method: str, params: dict[str, Any]) -> Result[dict[str, Any]]:
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
            "params": params,
        }
        try:
            req = urllib.request.Request(
                self._url,
                data=json.dumps(payload).encode("utf-8"),
                headers=self._headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # nosec B310 — URL 来自受信配置
                body = resp.read().decode("utf-8", errors="replace")
            # SSE 响应：data: {...} 行；JSON 响应直接解析
            if body.lstrip().startswith("data:"):
                data_line = next(
                    (l for l in body.splitlines() if l.startswith("data:")),
                    "{}",
                )
                data = json.loads(data_line[5:].strip())
            else:
                data = json.loads(body)
        except Exception as e:  # noqa: BLE001 — 传输错误统一包装
            return Result.fail(f"HTTP transport error: {e}", code="TRANSPORT_ERROR")
        if "error" in data and data["error"]:
            return Result.fail(f"JSON-RPC error: {data['error']}", code="JSONRPC_ERROR")
        return Result.ok(data.get("result") or {})


def discover_and_register(
    key: str,
    name: str,
    transport: str,
    *,
    command: list[str] | None = None,
    url: str | None = None,
    cwd: str | None = None,
    timeout: float = 30.0,
) -> Result[list[str]]:
    """T1-5: 连接标准 MCP server，tools/list 发现并注册工具名列表。

    返回工具名列表（供 mcp_proxy.register_server 使用）。连接失败返回 Result.fail。
    """
    if transport == "stdio" and command:
        client: Any = MCPStdioClient(command=command, cwd=cwd, timeout=timeout)
    elif transport == "http" and url:
        client = MCPHttpClient(url=url, timeout=timeout)
    else:
        return Result.fail(f"Invalid MCP transport config: {transport}", code="BAD_TRANSPORT")

    if isinstance(client, MCPStdioClient):
        conn = client.connect()
        if conn.is_error:
            return conn
    try:
        tools = client.list_tools()
        if tools.is_error:
            return tools
        names = [t.name for t in tools.data]
        return Result.ok(names)
    finally:
        if isinstance(client, MCPStdioClient):
            client.close()
