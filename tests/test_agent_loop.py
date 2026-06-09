"""Tests for the agent loop: tool_calls detection, execution, and result feedback."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock


from lingclaude.core.query_engine import AGENT_MAX_TOOL_ROUNDS, QueryEngine, QueryEngineConfig, StopReason, CONSECUTIVE_FAILURE_LIMIT
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
        assert len(tools) >= 2
        bash_tool = next(t for t in tools if t["name"] == "bash")
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


class TestCheckpointResume:
    def test_checkpoint_saved_after_tool_round(self, tmp_path: Any, monkeypatch: Any) -> None:
        monkeypatch.setattr("lingclaude.core.query_engine.CHECKPOINT_DIR", tmp_path / "cp")

        call_count = 0

        class TwoRoundProvider(ModelProvider):
            def complete(self, messages, config=None, tools=None):
                nonlocal call_count
                from lingclaude.core.types import Result
                call_count += 1
                if call_count == 1:
                    return Result.ok(ModelResponse(
                        content="",
                        model="test",
                        usage=ModelUsage(),
                        tool_calls=(ToolCall(id="c1", name="bash", arguments='{"command": "ls"}'),),
                    ))
                return Result.ok(ModelResponse(
                    content="done after tool",
                    model="test",
                    usage=ModelUsage(),
                    tool_calls=(),
                ))

            async def acomplete(self, messages, config=None, tools=None):
                from lingclaude.core.types import Result
                return Result.ok(ModelResponse(content="ok", model="test", usage=ModelUsage()))

            def count_tokens(self, text):
                return 0

        runtime = MagicMock()
        runtime.registry.list_tools.return_value = ()
        runtime.execute_tool.return_value = {"exit_code": 0, "stdout": "file.py", "stderr": ""}

        engine = QueryEngine(model_provider=TwoRoundProvider())
        engine.set_runtime(runtime)
        result = engine.submit("test checkpoint")

        assert result.output == "done after tool"
        cp_dir = tmp_path / "cp"
        cp_files = list(cp_dir.glob("*.json"))
        assert len(cp_files) == 0

    def test_checkpoint_exists_during_tool_loop(self, tmp_path: Any, monkeypatch: Any) -> None:
        cp_dir = tmp_path / "cp"
        monkeypatch.setattr("lingclaude.core.query_engine.CHECKPOINT_DIR", cp_dir)

        call_count = 0

        class InterruptingProvider(ModelProvider):
            def complete(self, messages, config=None, tools=None):
                nonlocal call_count
                from lingclaude.core.types import Result
                call_count += 1
                if call_count == 1:
                    return Result.ok(ModelResponse(
                        content="",
                        model="test",
                        usage=ModelUsage(),
                        tool_calls=(ToolCall(id="c1", name="bash", arguments='{"command": "ls"}'),),
                    ))
                if call_count == 2:
                    return Result.ok(ModelResponse(
                        content="",
                        model="test",
                        usage=ModelUsage(),
                        tool_calls=(ToolCall(id="c2", name="read", arguments='{"path": "/tmp/x"}'),),
                    ))
                return Result.ok(ModelResponse(
                    content="final answer",
                    model="test",
                    usage=ModelUsage(),
                    tool_calls=(),
                ))

            async def acomplete(self, messages, config=None, tools=None):
                from lingclaude.core.types import Result
                return Result.ok(ModelResponse(content="ok", model="test", usage=ModelUsage()))

            def count_tokens(self, text):
                return 0

        runtime = MagicMock()
        runtime.registry.list_tools.return_value = ()
        runtime.execute_tool.return_value = {"exit_code": 0, "stdout": "out", "stderr": ""}

        engine = QueryEngine(model_provider=InterruptingProvider())
        engine.set_runtime(runtime)
        result = engine.submit("multi-round")

        assert result.output == "final answer"
        assert not engine.has_checkpoint

    def test_resume_after_simulated_429(self, tmp_path: Any, monkeypatch: Any) -> None:
        cp_dir = tmp_path / "cp"
        monkeypatch.setattr("lingclaude.core.query_engine.CHECKPOINT_DIR", cp_dir)

        session_id = "test_429_session"

        cp_data = {
            "session_id": session_id,
            "prompt": "读取文件并分析",
            "round_idx": 0,
            "used_tools": True,
            "total_input": 100,
            "total_output": 50,
            "messages": [
                {"role": "system", "content": "你是灵克"},
                {"role": "user", "content": "读取文件并分析"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "bash", "arguments": '{"command": "ls"}'},
                        }
                    ],
                },
                {"role": "tool", "content": '{"exit_code": 0, "stdout": "main.py", "stderr": ""}', "name": "bash", "tool_call_id": "call_1"},
            ],
            "conversation": [],
            "timestamp": "2026-01-01T00:00:00Z",
        }
        cp_dir.mkdir(parents=True, exist_ok=True)
        (cp_dir / f"{session_id}.json").write_text(
            json.dumps(cp_data, ensure_ascii=False), encoding="utf-8",
        )

        class ResumeProvider(ModelProvider):
            def complete(self, messages, config=None, tools=None):
                from lingclaude.core.types import Result
                return Result.ok(ModelResponse(
                    content="分析完成：找到main.py",
                    model="test",
                    usage=ModelUsage(input_tokens=50, output_tokens=20),
                    tool_calls=(),
                ))

            async def acomplete(self, messages, config=None, tools=None):
                from lingclaude.core.types import Result
                return Result.ok(ModelResponse(content="ok", model="test", usage=ModelUsage()))

            def count_tokens(self, text):
                return 0

        runtime = MagicMock()
        runtime.registry.list_tools.return_value = ()

        engine = QueryEngine(model_provider=ResumeProvider())
        engine.session_id = session_id
        engine.set_runtime(runtime)

        assert engine.has_checkpoint

        resume_result = engine.resume_interrupted()
        assert resume_result.is_ok
        assert "分析完成" in resume_result.data
        assert not engine.has_checkpoint
        assert len(engine._messages) == 2

    def test_resume_no_checkpoint_returns_error(self) -> None:
        class SimpleProvider(ModelProvider):
            def complete(self, messages, config=None, tools=None):
                from lingclaude.core.types import Result
                return Result.ok(ModelResponse(content="ok", model="test", usage=ModelUsage()))

            async def acomplete(self, messages, config=None, tools=None):
                from lingclaude.core.types import Result
                return Result.ok(ModelResponse(content="ok", model="test", usage=ModelUsage()))

            def count_tokens(self, text):
                return 0

        engine = QueryEngine(model_provider=SimpleProvider())
        result = engine.resume_interrupted()
        assert result.is_error
        assert result.code == "NO_CHECKPOINT"

    def test_reset_clears_checkpoint(self, tmp_path: Any, monkeypatch: Any) -> None:
        cp_dir = tmp_path / "cp"
        monkeypatch.setattr("lingclaude.core.query_engine.CHECKPOINT_DIR", cp_dir)

        call_count = 0

        class OneToolProvider(ModelProvider):
            def complete(self, messages, config=None, tools=None):
                nonlocal call_count
                from lingclaude.core.types import Result
                call_count += 1
                if call_count == 1:
                    return Result.ok(ModelResponse(
                        content="",
                        model="test",
                        usage=ModelUsage(),
                        tool_calls=(ToolCall(id="c1", name="bash", arguments='{"command": "echo hi"}'),),
                    ))
                return Result.ok(ModelResponse(
                    content="ok", model="test", usage=ModelUsage(), tool_calls=(),
                ))

            async def acomplete(self, messages, config=None, tools=None):
                from lingclaude.core.types import Result
                return Result.ok(ModelResponse(content="ok", model="test", usage=ModelUsage()))

            def count_tokens(self, text):
                return 0

        runtime = MagicMock()
        runtime.registry.list_tools.return_value = ()
        runtime.execute_tool.return_value = {"exit_code": 0, "stdout": "hi", "stderr": ""}

        engine = QueryEngine(model_provider=OneToolProvider())
        engine.set_runtime(runtime)
        engine.submit("trigger tool")

        engine.reset()
        assert not engine.has_checkpoint

    def test_resume_provider_error_returns_fail(self, tmp_path: Any, monkeypatch: Any) -> None:
        cp_dir = tmp_path / "cp"
        monkeypatch.setattr("lingclaude.core.query_engine.CHECKPOINT_DIR", cp_dir)

        session_id = "test_fail_resume"
        cp_data = {
            "session_id": session_id,
            "prompt": "test",
            "round_idx": 0,
            "used_tools": True,
            "total_input": 0,
            "total_output": 0,
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "test"},
                {"role": "tool", "content": '{"ok": true}', "name": "bash", "tool_call_id": "c1"},
            ],
            "conversation": [],
            "timestamp": "2026-01-01T00:00:00Z",
        }
        cp_dir.mkdir(parents=True, exist_ok=True)
        (cp_dir / f"{session_id}.json").write_text(
            json.dumps(cp_data, ensure_ascii=False), encoding="utf-8",
        )

        class FailingProvider(ModelProvider):
            def complete(self, messages, config=None, tools=None):
                from lingclaude.core.types import Result
                return Result.fail("429 rate limit", code="RATE_LIMIT")

            async def acomplete(self, messages, config=None, tools=None):
                from lingclaude.core.types import Result
                return Result.fail("error", code="ERR")

            def count_tokens(self, text):
                return 0

        engine = QueryEngine(model_provider=FailingProvider())
        engine.session_id = session_id

        result = engine.resume_interrupted()
        assert result.is_error
        assert "Resume failed" in result.error


class AlwaysFailingProvider(ModelProvider):
    def __init__(self, fail_count: int) -> None:
        self._fail_count = fail_count
        self._call_count = 0

    def complete(self, messages, config=None, tools=None):
        from lingclaude.core.types import Result
        self._call_count += 1
        if self._call_count <= self._fail_count:
            return Result.fail("connection timeout", code="TIMEOUT")
        return Result.ok(ModelResponse(
            content="recovered", model="test", usage=ModelUsage(),
        ))

    async def acomplete(self, messages, config=None, tools=None):
        from lingclaude.core.types import Result
        return Result.fail("error", code="ERR")

    def count_tokens(self, text):
        return 0


class ToolErrorProvider(ModelProvider):
    def __init__(self, error_rounds: int) -> None:
        self._error_rounds = error_rounds
        self._round = 0

    def complete(self, messages, config=None, tools=None):
        from lingclaude.core.types import Result
        self._round += 1
        if self._round <= self._error_rounds:
            return Result.ok(ModelResponse(
                content="",
                model="test",
                usage=ModelUsage(),
                tool_calls=(ToolCall(
                    id=f"err_{self._round}",
                    name="bash",
                    arguments='{"command": "false"}',
                ),),
            ))
        return Result.ok(ModelResponse(
            content="最终成功", model="test", usage=ModelUsage(),
        ))

    async def acomplete(self, messages, config=None, tools=None):
        from lingclaude.core.types import Result
        return Result.fail("error", code="ERR")

    def count_tokens(self, text):
        return 0


class TestHardInterrupt:
    def test_consecutive_model_failures_trigger_hard_interrupt(self) -> None:
        provider = AlwaysFailingProvider(fail_count=5)
        config = QueryEngineConfig(consecutive_failure_limit=3)
        engine = QueryEngine(config=config, model_provider=provider)
        result = engine.submit("测试硬中断")
        assert "[硬中断]" in result.output
        assert "连续模型调用失败" in result.output
        assert result.stop_reason == StopReason.CONSECUTIVE_FAILURE

    def test_single_model_failure_no_interrupt(self) -> None:
        provider = AlwaysFailingProvider(fail_count=1)
        config = QueryEngineConfig(consecutive_failure_limit=3)
        engine = QueryEngine(config=config, model_provider=provider)
        result = engine.submit("测试单次失败")
        assert "[硬中断]" not in result.output
        assert result.stop_reason == StopReason.COMPLETED

    def test_consecutive_tool_errors_trigger_hard_interrupt(self) -> None:
        provider = ToolErrorProvider(error_rounds=3)
        config = QueryEngineConfig(consecutive_failure_limit=3)

        runtime = MagicMock()
        runtime.registry.list_tools.return_value = ()
        runtime.execute_tool.return_value = {"error": "command failed"}

        engine = QueryEngine(config=config, model_provider=provider)
        engine.set_runtime(runtime)
        result = engine.submit("测试工具连续失败")
        assert "[硬中断]" in result.output
        assert "连续工具调用失败" in result.output
        assert result.stop_reason == StopReason.CONSECUTIVE_FAILURE

    def test_tool_errors_below_limit_continue(self) -> None:
        provider = ToolErrorProvider(error_rounds=2)
        config = QueryEngineConfig(consecutive_failure_limit=3)

        runtime = MagicMock()
        runtime.registry.list_tools.return_value = ()
        runtime.execute_tool.return_value = {"error": "command failed"}

        engine = QueryEngine(config=config, model_provider=provider)
        engine.set_runtime(runtime)
        result = engine.submit("测试工具失败未达阈值")
        assert "[硬中断]" not in result.output

    def test_mixed_success_resets_failure_counter(self) -> None:
        responses = []
        for i in range(2):
            responses.append(ModelResponse(
                content="",
                model="test",
                usage=ModelUsage(),
                tool_calls=(ToolCall(id=f"e{i}", name="bash", arguments='{"command": "false"}'),),
            ))
        for i in range(2):
            responses.append(ModelResponse(
                content="",
                model="test",
                usage=ModelUsage(),
                tool_calls=(ToolCall(id=f"s{i}", name="bash", arguments='{"command": "echo ok"}'),),
            ))
        for i in range(2):
            responses.append(ModelResponse(
                content="",
                model="test",
                usage=ModelUsage(),
                tool_calls=(ToolCall(id=f"e2_{i}", name="bash", arguments='{"command": "false"}'),),
            ))
        responses.append(ModelResponse(
            content="全部完成", model="test", usage=ModelUsage(),
        ))
        provider = FakeProvider(responses)
        config = QueryEngineConfig(consecutive_failure_limit=3)

        runtime = MagicMock()
        runtime.registry.list_tools.return_value = ()
        call_count = [0]

        def side_effect(name, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                return {"error": "fail"}
            if call_count[0] <= 4:
                return {"exit_code": 0, "stdout": "ok", "stderr": "", "duration": 0.01}
            return {"error": "fail"}

        runtime.execute_tool.side_effect = side_effect

        engine = QueryEngine(config=config, model_provider=provider)
        engine.set_runtime(runtime)
        result = engine.submit("测试混合成功重置计数器")
        assert "[硬中断]" not in result.output

    def test_default_config_has_consecutive_failure_limit(self) -> None:
        config = QueryEngineConfig()
        assert config.consecutive_failure_limit == CONSECUTIVE_FAILURE_LIMIT
        assert CONSECUTIVE_FAILURE_LIMIT == 3

    def test_custom_failure_limit(self) -> None:
        provider = AlwaysFailingProvider(fail_count=2)
        config = QueryEngineConfig(consecutive_failure_limit=2)
        engine = QueryEngine(config=config, model_provider=provider)
        result = engine.submit("测试自定义阈值")
        assert "[硬中断]" in result.output
