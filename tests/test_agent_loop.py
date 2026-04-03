"""Tests for the agent loop: tool_calls detection, execution, and result feedback."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from lingclaude.core.query_engine import AGENT_MAX_TOOL_ROUNDS, QueryEngine, QueryEngineConfig
from lingclaude.engine.coding import CodingRuntime
from lingclaude.model.types import (
    ModelConfig,
    ModelMessage,
    ModelProvider,
    ModelResponse,
    ModelUsage,
    ToolCall,
)


class FakeProvider(ModelProvider):
    def __init__(self, responses: list[ModelResponse] | None = None) -> None:
        self.responses = responses or []
        self._call_count = 0

    def complete(
        self,
        messages: tuple[ModelMessage, ...],
        config: ModelConfig | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Any:
        from lingclaude.core.types import Result
        if self._call_count < len(self.responses):
            resp = self.responses[self._call_count]
            self._call_count += 1
            return Result.ok(resp)
        return Result.ok(ModelResponse(
            content="fallback", model="test", usage=ModelUsage(),
        ))

    async def acomplete(
        self,
        messages: tuple[ModelMessage, ...],
        config: ModelConfig | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Any:
        from lingclaude.core.types import Result
        if self._call_count < len(self.responses):
            resp = self.responses[self._call_count]
            self._call_count += 1
            return Result.ok(resp)
        return Result.ok(ModelResponse(
            content="fallback", model="test", usage=ModelUsage(),
        ))

    def count_tokens(self, text: str) -> int:
        return len(text) // 4


class TestAgentLoopBasic:
    def test_no_tools_no_runtime(self) -> None:
        provider = FakeProvider([ModelResponse(
            content="Hello!", model="test", usage=ModelUsage(),
        )])
        engine = QueryEngine(model_provider=provider)
        result = engine.submit("你好")
        assert result.output == "Hello!"

    def test_no_tool_calls_returns_content(self) -> None:
        provider = FakeProvider([ModelResponse(
            content="直接回答", model="test", usage=ModelUsage(),
            tool_calls=(),
        )])
        engine = QueryEngine(model_provider=provider)
        result = engine.submit("问题")
        assert result.output == "直接回答"

    def test_tool_call_executed_and_fed_back(self) -> None:
        provider = FakeProvider([
            ModelResponse(
                content="",
                model="test",
                usage=ModelUsage(),
                finish_reason="tool_calls",
                tool_calls=(
                    ToolCall(id="call_1", name="read", arguments='{"path": "/tmp/test.txt"}'),
                ),
            ),
            ModelResponse(
                content="文件内容是 hello world",
                model="test",
                usage=ModelUsage(),
                tool_calls=(),
            ),
        ])

        runtime = MagicMock()
        runtime.registry.list_tools.return_value = ()
        runtime.execute_tool.return_value = {"content": "hello world", "size": 11}

        engine = QueryEngine(model_provider=provider)
        engine.set_runtime(runtime)
        result = engine.submit("读取文件")

        assert result.output == "文件内容是 hello world"
        runtime.execute_tool.assert_called_once_with("read", path="/tmp/test.txt")

    def test_multiple_tool_calls_in_one_round(self) -> None:
        provider = FakeProvider([
            ModelResponse(
                content="",
                model="test",
                usage=ModelUsage(),
                finish_reason="tool_calls",
                tool_calls=(
                    ToolCall(id="call_1", name="read", arguments='{"path": "/a.py"}'),
                    ToolCall(id="call_2", name="read", arguments='{"path": "/b.py"}'),
                ),
            ),
            ModelResponse(
                content="两个文件都已读取",
                model="test",
                usage=ModelUsage(),
                tool_calls=(),
            ),
        ])

        runtime = MagicMock()
        runtime.registry.list_tools.return_value = ()
        runtime.execute_tool.side_effect = [
            {"content": "file_a", "size": 6},
            {"content": "file_b", "size": 6},
        ]

        engine = QueryEngine(model_provider=provider)
        engine.set_runtime(runtime)
        result = engine.submit("读两个文件")
        assert result.output == "两个文件都已读取"
        assert runtime.execute_tool.call_count == 2

    def test_multi_round_tool_calls(self) -> None:
        provider = FakeProvider([
            ModelResponse(
                content="",
                model="test",
                usage=ModelUsage(),
                tool_calls=(ToolCall(id="c1", name="glob", arguments='{"pattern": "*.py"}'),),
            ),
            ModelResponse(
                content="",
                model="test",
                usage=ModelUsage(),
                tool_calls=(ToolCall(id="c2", name="read", arguments='{"path": "main.py"}'),),
            ),
            ModelResponse(
                content="找到了main.py并读取了内容",
                model="test",
                usage=ModelUsage(),
                tool_calls=(),
            ),
        ])

        runtime = MagicMock()
        runtime.registry.list_tools.return_value = ()
        runtime.execute_tool.side_effect = [
            {"files": ["main.py", "utils.py"]},
            {"content": "print('hello')", "size": 15},
        ]

        engine = QueryEngine(model_provider=provider)
        engine.set_runtime(runtime)
        result = engine.submit("找Python文件并读取main.py")
        assert result.output == "找到了main.py并读取了内容"
        assert runtime.execute_tool.call_count == 2


class TestAgentLoopEdgeCases:
    def test_tool_execution_error_handled(self) -> None:
        provider = FakeProvider([
            ModelResponse(
                content="",
                model="test",
                usage=ModelUsage(),
                tool_calls=(ToolCall(id="c1", name="bash", arguments='{"command": "bad_cmd"}'),),
            ),
            ModelResponse(
                content="命令执行失败了",
                model="test",
                usage=ModelUsage(),
                tool_calls=(),
            ),
        ])

        runtime = MagicMock()
        runtime.registry.list_tools.return_value = ()
        runtime.execute_tool.return_value = {
            "exit_code": 127, "stdout": "", "stderr": "command not found", "duration": 0.01,
        }

        engine = QueryEngine(model_provider=provider)
        engine.set_runtime(runtime)
        result = engine.submit("执行坏命令")
        assert "失败" in result.output

    def test_invalid_json_arguments(self) -> None:
        provider = FakeProvider([
            ModelResponse(
                content="",
                model="test",
                usage=ModelUsage(),
                tool_calls=(ToolCall(id="c1", name="read", arguments='not valid json'),),
            ),
            ModelResponse(
                content="参数解析错误",
                model="test",
                usage=ModelUsage(),
                tool_calls=(),
            ),
        ])

        runtime = MagicMock()
        runtime.registry.list_tools.return_value = ()

        engine = QueryEngine(model_provider=provider)
        engine.set_runtime(runtime)
        result = engine.submit("读文件")
        assert "参数解析错误" in result.output

    def test_max_tool_rounds_protection(self) -> None:
        infinite_tool_call = ModelResponse(
            content="",
            model="test",
            usage=ModelUsage(),
            tool_calls=(ToolCall(id="c_loop", name="bash", arguments='{"command": "echo loop"}'),),
        )
        provider = FakeProvider([infinite_tool_call] * (AGENT_MAX_TOOL_ROUNDS + 5))

        runtime = MagicMock()
        runtime.registry.list_tools.return_value = ()
        runtime.execute_tool.return_value = {"exit_code": 0, "stdout": "loop", "stderr": "", "duration": 0.01}

        engine = QueryEngine(model_provider=provider)
        engine.set_runtime(runtime)
        result = engine.submit("无限循环测试")
        assert "最大工具调用轮次" in result.output

    def test_provider_error_returned(self) -> None:
        provider = FakeProvider([])
        engine = QueryEngine(model_provider=provider)
        result = engine.submit("触发错误")
        assert result.output == "fallback"

    def test_runtime_exception_handled(self) -> None:
        provider = FakeProvider([
            ModelResponse(
                content="",
                model="test",
                usage=ModelUsage(),
                tool_calls=(ToolCall(id="c1", name="read", arguments='{"path": "/etc/shadow"}'),),
            ),
            ModelResponse(
                content="权限被拒绝",
                model="test",
                usage=ModelUsage(),
                tool_calls=(),
            ),
        ])

        runtime = MagicMock()
        runtime.registry.list_tools.return_value = ()
        runtime.execute_tool.return_value = {"error": "Tool blocked by permissions: read"}

        engine = QueryEngine(model_provider=provider)
        engine.set_runtime(runtime)
        result = engine.submit("读敏感文件")
        assert "权限被拒绝" in result.output


class TestToolDefinitionConversion:
    def test_tools_converted_to_openai_format(self) -> None:
        runtime = MagicMock()
        from lingclaude.engine.tools import ToolDefinition

        runtime.registry.list_tools.return_value = (
            ToolDefinition(
                name="bash",
                description="Execute bash commands",
                parameters={"command": {"type": "string"}},
                handler=None,
            ),
            ToolDefinition(
                name="read",
                description="Read file contents",
                parameters={"path": {"type": "string"}},
                handler=None,
            ),
        )

        captured_tools: dict[str, Any] = {}

        class CaptureProvider(ModelProvider):
            def complete(self, messages, config=None, tools=None):
                captured_tools["tools"] = tools
                from lingclaude.core.types import Result
                return Result.ok(ModelResponse(content="ok", model="test", usage=ModelUsage()))

            async def acomplete(self, messages, config=None, tools=None):
                from lingclaude.core.types import Result
                return Result.ok(ModelResponse(content="ok", model="test", usage=ModelUsage()))

            def count_tokens(self, text):
                return 0

        engine = QueryEngine(model_provider=CaptureProvider())
        engine.set_runtime(runtime)
        engine.submit("测试工具格式")

        tools = captured_tools["tools"]
        assert tools is not None
        assert len(tools) == 2
        bash_tool = tools[0]
        assert bash_tool["name"] == "bash"
        assert bash_tool["parameters"]["type"] == "object"
        assert "command" in bash_tool["parameters"]["properties"]
        assert "command" in bash_tool["parameters"]["required"]

    def test_no_runtime_means_no_tools(self) -> None:
        captured_tools: dict[str, Any] = {}

        class CaptureProvider(ModelProvider):
            def complete(self, messages, config=None, tools=None):
                captured_tools["tools"] = tools
                from lingclaude.core.types import Result
                return Result.ok(ModelResponse(content="ok", model="test", usage=ModelUsage()))

            async def acomplete(self, messages, config=None, tools=None):
                from lingclaude.core.types import Result
                return Result.ok(ModelResponse(content="ok", model="test", usage=ModelUsage()))

            def count_tokens(self, text):
                return 0

        engine = QueryEngine(model_provider=CaptureProvider())
        engine.submit("测试")
        assert captured_tools["tools"] is None


class TestMessageHistory:
    def test_tool_messages_include_tool_call_id(self) -> None:
        captured_messages: list[tuple[ModelMessage, ...]] = []

        class CaptureProvider(ModelProvider):
            def __init__(self):
                self.round = 0

            def complete(self, messages, config=None, tools=None):
                captured_messages.append(messages)
                from lingclaude.core.types import Result
                self.round += 1
                if self.round == 1:
                    return Result.ok(ModelResponse(
                        content="",
                        model="test",
                        usage=ModelUsage(),
                        tool_calls=(ToolCall(id="tc_42", name="bash", arguments='{"command": "ls"}'),),
                    ))
                return Result.ok(ModelResponse(
                    content="列出文件了", model="test", usage=ModelUsage(), tool_calls=(),
                ))

            async def acomplete(self, messages, config=None, tools=None):
                from lingclaude.core.types import Result
                return Result.ok(ModelResponse(content="ok", model="test", usage=ModelUsage()))

            def count_tokens(self, text):
                return 0

        runtime = MagicMock()
        runtime.registry.list_tools.return_value = ()
        runtime.execute_tool.return_value = {"exit_code": 0, "stdout": "file1.py\nfile2.py", "stderr": "", "duration": 0.1}

        engine = QueryEngine(model_provider=CaptureProvider())
        engine.set_runtime(runtime)
        engine.submit("列出文件")

        assert len(captured_messages) == 2
        second_call_msgs = captured_messages[1]

        assistant_msg = second_call_msgs[-2]
        assert assistant_msg.role.value == "assistant"
        assert assistant_msg.tool_calls is not None
        assert assistant_msg.tool_calls[0].id == "tc_42"

        tool_msg = second_call_msgs[-1]
        assert tool_msg.role.value == "tool"
        assert tool_msg.tool_call_id == "tc_42"
        assert "file1.py" in tool_msg.content
