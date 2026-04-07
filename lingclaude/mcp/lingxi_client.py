"""LingXi MCP Client - 灵犀 MCP 客户端"""
from __future__ import annotations

import json
import subprocess
from typing import Optional, Any
from dataclasses import dataclass

@dataclass
class MCPRequest:
    """MCP JSON-RPC Request"""
    jsonrpc: str
    id: int
    method: str
    params: dict[str, Any]

@dataclass
class MCPResponse:
    """MCP JSON-RPC Response"""
    jsonrpc: str
    id: int
    result: Optional[dict[str, Any]] = None
    error: Optional[dict[str, Any]] = None

class LingXiClient:
    """LingXi MCP 客户端

    通过 stdio JSON-RPC 与 LingXi MCP 服务器通信
    """

    def __init__(
        self,
        server_path: str = "/home/ai/Ling-term-mcp/dist/index.js",
        node_path: str = "node",
    ):
        """初始化客户端

        Args:
            server_path: LingXi MCP 服务器路径
            node_path: Node.js 可执行文件路径
        """
        self.server_path = server_path
        self.node_path = node_path
        self.process: Optional[subprocess.Popen] = None
        self._id_counter = 0
        self._initialized = False

    def __enter__(self):
        """支持上下文管理器"""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出时关闭连接"""
        self.close()

    def start(self) -> None:
        """启动 MCP 服务器进程"""
        if self.process is not None:
            raise RuntimeError("Client already started")

        self.process = subprocess.Popen(
            [self.node_path, self.server_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # 行缓冲
        )

        # 初始化连接
        self._initialize()

    def _initialize(self) -> None:
        """初始化 MCP 连接

        发送 initialize 请求以建立连接
        """
        response = self._send_request({
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "prompts": {},
                    "resources": {},
                    "tools": {},
                },
                "clientInfo": {
                    "name": "LingClaude",
                    "version": "0.2.1",
                },
            },
        })

        if "error" in response:
            raise RuntimeError(f"Failed to initialize: {response['error']}")

        # 发送 initialized 通知
        self._send_notification({
            "method": "notifications/initialized",
        })

        self._initialized = True

    def _send_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """发送 JSON-RPC 请求

        Args:
            request: 请求数据（不包含 jsonrpc 和 id）

        Returns:
            响应数据
        """
        if self.process is None:
            raise RuntimeError("Client not started")

        self._id_counter += 1
        full_request = {
            "jsonrpc": "2.0",
            "id": self._id_counter,
            **request,
        }

        # 发送请求
        request_json = json.dumps(full_request)
        self.process.stdin.write(request_json + "\n")
        self.process.stdin.flush()

        # 读取响应
        response_line = self.process.stdout.readline()
        if not response_line:
            # 读取 stderr 以获取错误信息
            stderr_output = self.process.stderr.read()
            raise RuntimeError(
                f"No response from server. "
                f"Stderr: {stderr_output}"
            )

        response = json.loads(response_line)

        # 检查错误
        if "error" in response:
            return response

        return response.get("result", response)

    def _send_notification(self, notification: dict[str, Any]) -> None:
        """发送 JSON-RPC 通知（无响应）

        Args:
            notification: 通知数据
        """
        if self.process is None:
            raise RuntimeError("Client not started")

        full_notification = {
            "jsonrpc": "2.0",
            **notification,
        }

        notification_json = json.dumps(full_notification)
        self.process.stdin.write(notification_json + "\n")
        self.process.stdin.flush()

    def close(self) -> None:
        """关闭连接"""
        if self.process is not None:
            try:
                self._send_notification({"method": "shutdown"})
                self._send_notification({"method": "exit"})
                self.process.wait(timeout=5)
            except Exception:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()

            self.process = None
            self._initialized = False

    def list_tools(self) -> list[dict[str, Any]]:
        """列出所有可用的工具

        Returns:
            工具列表
        """
        response = self._send_request({
            "method": "tools/list",
        })

        if "error" in response:
            raise RuntimeError(f"Failed to list tools: {response['error']}")

        return response.get("tools", [])

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """调用工具

        Args:
            name: 工具名称
            arguments: 工具参数

        Returns:
            工具返回的内容列表
        """
        response = self._send_request({
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments,
            },
        })

        if "error" in response:
            raise RuntimeError(f"Tool call failed: {response['error']}")

        return response.get("content", [])

    def execute_command(
        self,
        command: str,
        args: Optional[list[str]] = None,
        timeout: Optional[int] = 60,
    ) -> str:
        """执行终端命令

        Args:
            command: 命令名称
            args: 命令参数
            timeout: 超时时间（秒）

        Returns:
            命令输出
        """
        arguments: dict[str, Any] = {
            "command": command,
            "args": args or [],
        }

        if timeout is not None:
            arguments["timeout"] = timeout

        content = self.call_tool("execute_command", arguments)

        # 提取文本内容
        for item in content:
            if item.get("type") == "text":
                return item.get("text", "")

        raise RuntimeError("No text content in response")

    def list_sessions(self) -> list[dict[str, Any]]:
        """列出所有会话

        Returns:
            会话列表
        """
        content = self.call_tool("list_sessions", {})

        for item in content:
            if item.get("type") == "text":
                return json.loads(item.get("text", "[]"))

        return []

    def create_session(
        self,
        name: str,
        cwd: Optional[str] = None,
    ) -> str:
        """创建新会话

        Args:
            name: 会话名称
            cwd: 工作目录

        Returns:
            会话 ID
        """
        arguments: dict[str, Any] = {"name": name}

        if cwd is not None:
            arguments["cwd"] = cwd

        content = self.call_tool("create_session", arguments)

        for item in content:
            if item.get("type") == "text":
                return item.get("text", "")

        raise RuntimeError("Failed to create session")

    def destroy_session(self, session_id: str) -> None:
        """删除会话

        Args:
            session_id: 会话 ID
        """
        self.call_tool("destroy_session", {"sessionId": session_id})

    def sync_terminal(self) -> dict[str, Any]:
        """同步终端状态

        Returns:
            终端状态信息
        """
        content = self.call_tool("sync_terminal", {})

        for item in content:
            if item.get("type") == "text":
                return json.loads(item.get("text", "{}"))

        return {}
