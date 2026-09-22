"""Schema 透传 + 懒发现 + idle 回收 专项测试。

2026-09-22 修复的回归防护：
1. Schema 透传：_build_openai_tools 必须优先用 ToolDefinition.input_schema（完整 JSON Schema），
   不得退回压平的 parameters dict（导致灵信 poll_messages 等带必填参数的 MCP 工具必败）。
2. ToolDefinition.input_schema 字段存在且默认空 dict。
3. MCPStdioClient idle_timeout 回收逻辑。
"""
from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from lingclaude.core.types import ToolDefinition


class TestSchemaPassthrough:
    """钉死 schema 透传修复——回归即红。"""

    def test_tool_definition_has_input_schema_field(self) -> None:
        """ToolDefinition 必须有 input_schema 字段，默认空 dict。"""
        td = ToolDefinition(name="test", description="test", parameters={})
        assert hasattr(td, "input_schema")
        assert td.input_schema == {}

    def test_tool_definition_input_schema_roundtrip(self) -> None:
        """input_schema 存取完整 JSON Schema 不丢失 required/嵌套结构。"""
        full_schema = {
            "type": "object",
            "properties": {
                "recipient": {"type": "string", "description": "接收者"},
                "payload": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
            "required": ["recipient"],
        }
        td = ToolDefinition(
            name="poll_messages",
            description="[MCP:灵信] poll_messages",
            parameters={},
            input_schema=full_schema,
        )
        assert td.input_schema["required"] == ["recipient"]
        assert "payload" in td.input_schema["properties"]
        # 嵌套 required 不丢
        assert td.input_schema["properties"]["payload"]["required"] == ["text"]

    def _make_mixin(self, tools: list[ToolDefinition]) -> Any:
        """构造带最小 mock 状态的 McpToolsMixin 实例。"""
        from lingclaude.core.mcp_tools import McpToolsMixin

        mixin = McpToolsMixin.__new__(McpToolsMixin)
        mock_runtime = MagicMock()
        mock_runtime.registry.list_tools.return_value = tools
        mixin._runtime = mock_runtime
        mixin._mcp_initialized = True  # 跳过 _ensure_mcp 的懒初始化
        return mixin

    def test_build_schema_prefers_input_schema(self) -> None:
        """_build_openai_tools 优先用 input_schema，不用压平 parameters。"""
        full_schema = {
            "type": "object",
            "properties": {"recipient": {"type": "string"}},
            "required": ["recipient"],
        }
        tool = ToolDefinition(
            name="poll_messages",
            description="[MCP:灵信] poll_messages",
            parameters={"recipient": {"type": "string"}},  # 压平版（旧路径）
            required_params=("recipient",),
            input_schema=full_schema,
        )

        mixin = self._make_mixin([tool])
        result = mixin._build_openai_tools()
        assert result is not None
        schema = result[0]["parameters"]
        # 必须是完整 input_schema，不是压平重建
        assert schema.get("required") == ["recipient"]
        assert schema.get("type") == "object"

    def test_build_schema_fallback_when_no_input_schema(self) -> None:
        """无 input_schema 时退回 parameters+required_params 重建（旧行为兼容）。"""
        tool = ToolDefinition(
            name="legacy_tool",
            description="legacy",
            parameters={"arg1": {"type": "string"}},
            required_params=("arg1",),
        )

        mixin = self._make_mixin([tool])
        result = mixin._build_openai_tools()
        assert result is not None
        schema = result[0]["parameters"]
        # fallback 路径：从 parameters 重建
        assert schema["properties"]["arg1"] == {"type": "string"}
        assert schema["required"] == ["arg1"]


class TestIdleTimeout:
    """MCPStdioClient idle 回收逻辑钉死。"""

    def test_idle_timeout_closes_proc(self) -> None:
        """超过 idle_timeout 无活动，maybe_close_idle 必须关闭子进程。"""
        from lingclaude.engine.mcp_client import MCPStdioClient

        client = MCPStdioClient(command=["echo", "test"], idle_timeout=0.1)
        # 模拟活跃子进程
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.poll.return_value = None  # 仍在运行
        client._proc = mock_proc
        client._last_used = time.time() - 1.0  # 1 秒前活跃，超过 0.1s timeout

        result = client.maybe_close_idle()
        assert result is True
        mock_proc.terminate.assert_called_once()

    def test_idle_timeout_not_triggered_when_active(self) -> None:
        """活跃 client 不得被 maybe_close_idle 误关。"""
        from lingclaude.engine.mcp_client import MCPStdioClient

        client = MCPStdioClient(command=["echo", "test"], idle_timeout=300.0)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        client._proc = mock_proc
        client._last_used = time.time()  # 刚活跃

        result = client.maybe_close_idle()
        assert result is False
        mock_proc.terminate.assert_not_called()

    def test_touch_updates_timestamp(self) -> None:
        """_touch() 必须刷新 _last_used 到当前时间。"""
        from lingclaude.engine.mcp_client import MCPStdioClient

        client = MCPStdioClient(command=["echo", "test"])
        old = client._last_used
        time.sleep(0.01)
        client._touch()
        assert client._last_used > old

    def test_no_proc_returns_false(self) -> None:
        """无子进程时 maybe_close_idle 返回 False 不报错。"""
        from lingclaude.engine.mcp_client import MCPStdioClient

        client = MCPStdioClient(command=["echo", "test"])
        client._proc = None
        assert client.maybe_close_idle() is False
