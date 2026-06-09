from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from lingclaude.core.query_engine import QueryEngine, QueryEngineConfig, StopReason
from lingclaude.core.session import SessionManager
from lingclaude.engine.tools import ToolDefinition, ToolRegistry
from lingclaude.model.types import (
    ModelConfig,
    ModelMessage,
    ModelProvider,
    ModelResponse,
    ModelUsage,
    ToolCall,
)


class _FakeProvider(ModelProvider):
    def __init__(self, responses: list[ModelResponse] | None = None) -> None:
        self.responses = responses or []
        self._idx = 0
        self.call_log: list[tuple[list[ModelMessage], ModelConfig | None]] = []

    def _next(self) -> ModelResponse:
        if self._idx < len(self.responses):
            resp = self.responses[self._idx]
            self._idx += 1
            return resp
        return ModelResponse(content="fallback", model="test", usage=ModelUsage())

    def complete(
        self,
        messages: tuple[ModelMessage, ...],
        config: ModelConfig | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Any:
        from lingclaude.core.types import Result
        self.call_log.append((list(messages), config))
        return Result.ok(self._next())

    async def acomplete(self, messages, config=None, tools=None):
        from lingclaude.core.types import Result
        return Result.ok(ModelResponse(content="async", model="test", usage=ModelUsage()))

    def count_tokens(self, text: str) -> int:
        return len(text) // 4


class _ToolRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def read(self, path: str = "") -> dict[str, Any]:
        self.calls.append(("read", {"path": path}))
        return {"content": f"// file: {path}\nprint('hello')", "size": 20}

    def grep(self, pattern: str = "", path: str = "") -> dict[str, Any]:
        self.calls.append(("grep", {"pattern": pattern, "path": path}))
        return {"matches": [f"{path}/main.py:1: match"]}

    def glob(self, pattern: str = "") -> dict[str, Any]:
        self.calls.append(("glob", {"pattern": pattern}))
        return {"files": [f"src/{pattern.replace('**/', '')}"]}

    def write(self, path: str = "", content: str = "") -> dict[str, Any]:
        self.calls.append(("write", {"path": path, "content": content}))
        return {"ok": True, "size": len(content)}


def _make_registry_with_tools(recorder: _ToolRecorder) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="read",
        description="Read file",
        parameters={"path": {"type": "string"}},
        handler=recorder.read,
    ))
    registry.register(ToolDefinition(
        name="grep",
        description="Search files",
        parameters={"pattern": {"type": "string"}, "path": {"type": "string"}},
        handler=recorder.grep,
    ))
    registry.register(ToolDefinition(
        name="glob",
        description="Find files",
        parameters={"pattern": {"type": "string"}},
        handler=recorder.glob,
    ))
    registry.register(ToolDefinition(
        name="write",
        description="Write file",
        parameters={"path": {"type": "string"}, "content": {"type": "string"}},
        handler=recorder.write,
    ))
    return registry


def _make_runtime(registry: ToolRegistry) -> MagicMock:
    runtime = MagicMock()
    runtime.registry = registry
    runtime.execute_tool = registry.execute
    return runtime


class TestMultiStepToolChain:
    """复杂问题：多步工具调用链（读→分析→写）"""

    @staticmethod
    def _make_engine(provider: _FakeProvider) -> QueryEngine:
        engine = QueryEngine(model_provider=provider)
        engine.L1_MESSAGE_THRESHOLD = 999999
        engine.L2_MESSAGE_THRESHOLD = 999999
        engine._total_messages_sent = 0
        engine._l1_last_triggered_at = -1
        engine._messages.clear()
        engine._conversation.clear()
        return engine

    def test_three_step_tool_chain(self) -> None:
        recorder = _ToolRecorder()
        registry = _make_registry_with_tools(recorder)
        runtime = _make_runtime(registry)

        provider = _FakeProvider([
            ModelResponse(
                content="", model="test", usage=ModelUsage(),
                tool_calls=(ToolCall(id="tc1", name="read", arguments='{"path": "src/main.py"}'),),
            ),
            ModelResponse(
                content="", model="test", usage=ModelUsage(),
                tool_calls=(ToolCall(id="tc2", name="grep", arguments='{"pattern": "TODO", "path": "src"}'),),
            ),
            ModelResponse(
                content="", model="test", usage=ModelUsage(),
                tool_calls=(ToolCall(id="tc3", name="write", arguments='{"path": "src/main.py", "content": "fixed code"}'),),
            ),
            ModelResponse(content="已完成三步修改", model="test", usage=ModelUsage()),
        ])

        engine = self._make_engine(provider)
        engine.set_runtime(runtime)
        result = engine.submit("帮我修复 src/main.py 中的所有 TODO")

        assert result.stop_reason == StopReason.COMPLETED
        assert len(recorder.calls) == 3
        assert recorder.calls[0][0] == "read"
        assert recorder.calls[1][0] == "grep"
        assert recorder.calls[2][0] == "write"
        assert result.output == "已完成三步修改"

    def test_tool_chain_preserves_context(self) -> None:
        recorder = _ToolRecorder()
        registry = _make_registry_with_tools(recorder)
        runtime = _make_runtime(registry)

        provider = _FakeProvider([
            ModelResponse(
                content="", model="test", usage=ModelUsage(),
                tool_calls=(ToolCall(id="tc1", name="read", arguments='{"path": "a.py"}'),),
            ),
            ModelResponse(content="a.py 的内容是 X", model="test", usage=ModelUsage()),
            ModelResponse(content="是的，上一轮我读了 a.py，内容是 X", model="test", usage=ModelUsage()),
        ])

        engine = self._make_engine(provider)
        engine.set_runtime(runtime)
        engine.submit("读 a.py")

        r2 = engine.submit("你刚才读了什么？")

        assert "a.py" in r2.output
        assert engine.turn_count == 2
        assert len(engine._conversation) == 4

    def test_parallel_tool_calls(self) -> None:
        recorder = _ToolRecorder()
        registry = _make_registry_with_tools(recorder)
        runtime = _make_runtime(registry)

        provider = _FakeProvider([
            ModelResponse(
                content="", model="test", usage=ModelUsage(),
                tool_calls=(
                    ToolCall(id="tc1", name="read", arguments='{"path": "a.py"}'),
                    ToolCall(id="tc2", name="read", arguments='{"path": "b.py"}'),
                ),
            ),
            ModelResponse(content="两个文件都读完了", model="test", usage=ModelUsage()),
        ])

        engine = self._make_engine(provider)
        engine.set_runtime(runtime)
        engine.submit("同时读 a.py 和 b.py")

        assert len(recorder.calls) == 2
        assert recorder.calls[0] == ("read", {"path": "a.py"})
        assert recorder.calls[1] == ("read", {"path": "b.py"})


class TestMultiTurnConversation:
    """复杂问题：多轮对话上下文连贯性"""

    @staticmethod
    def _make_engine(provider: _FakeProvider) -> QueryEngine:
        engine = QueryEngine(model_provider=provider)
        engine.L1_MESSAGE_THRESHOLD = 999999
        engine.L2_MESSAGE_THRESHOLD = 999999
        engine._total_messages_sent = 0
        engine._l1_last_triggered_at = -1
        return engine

    def test_five_turn_referential_chain(self) -> None:
        provider = _FakeProvider()
        engine = self._make_engine(provider)

        topics = ["Python 的 GIL", "它有什么影响？", "能举个例子吗？", "有替代方案吗？", "总结一下"]
        provider.responses = [
            ModelResponse(content=f"回答{i+1}: 关于{prompt}", model="test", usage=ModelUsage())
            for i, prompt in enumerate(topics)
        ]
        provider._idx = 0
        for i, prompt in enumerate(topics):
            result = engine.submit(prompt)
            assert result.stop_reason == StopReason.COMPLETED

        assert engine.turn_count == 5
        assert len(engine._messages) == 10
        assert len(engine._conversation) == 10

        last_call = provider.call_log[-1]
        messages = last_call[0]
        user_msgs = [m for m in messages if m.role.value == "user"]
        assert len(user_msgs) == 5

    def test_context_survives_error_turn(self) -> None:
        class _SelectiveErrorProvider(ModelProvider):
            def __init__(self) -> None:
                self._responses = [
                    ModelResponse(content="第一个回答", model="test", usage=ModelUsage()),
                    ModelResponse(content="", model="test", usage=ModelUsage()),
                    ModelResponse(content="成功恢复", model="test", usage=ModelUsage()),
                ]
                self._idx = 0

            def _next(self) -> ModelResponse:
                if self._idx < len(self._responses):
                    r = self._responses[self._idx]
                    self._idx += 1
                    return r
                return ModelResponse(content="fallback", model="test", usage=ModelUsage())

            def complete(self, messages, config=None, tools=None):
                from lingclaude.core.types import Result
                return Result.ok(self._next())

            async def acomplete(self, messages, config=None, tools=None):
                from lingclaude.core.types import Result
                return Result.ok(ModelResponse(content="async", model="test", usage=ModelUsage()))

            def count_tokens(self, text: str) -> int:
                return len(text) // 4

        engine = QueryEngine(model_provider=_SelectiveErrorProvider())
        r1 = engine.submit("问题1")
        assert r1.output == "第一个回答"

        r2 = engine.submit("重试")
        assert r2.output == ""

        r3 = engine.submit("继续")
        assert r3.output == "成功恢复"
        assert engine.turn_count == 3

    def test_conversation_restored_after_session_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sm = SessionManager(save_dir=Path(tmpdir))
            provider = _FakeProvider()
            engine = QueryEngine(
                model_provider=provider,
                session_manager=sm,
            )

            provider.responses = [
                ModelResponse(content="回答A", model="test", usage=ModelUsage()),
                ModelResponse(content="回答B", model="test", usage=ModelUsage()),
            ]
            engine.submit("问题A")
            engine.submit("问题B")

            persist_result = engine.persist_session()
            assert persist_result.is_ok
            session_id = engine.session_id

            engine2 = QueryEngine(
                model_provider=provider,
                session_manager=sm,
            )
            loaded = engine2.load_session(session_id)
            assert loaded
            assert engine2.turn_count == 2
            assert len(engine2._conversation) == 4

            provider.responses = [
                ModelResponse(content="是的，之前讨论了 A 和 B", model="test", usage=ModelUsage()),
            ]
            engine2.submit("我们之前讨论了什么？")

            last_msgs = provider.call_log[-1][0]
            conv_msgs = [m for m in last_msgs if m.role.value in ("user", "assistant")]
            assert len(conv_msgs) >= 4


class TestLongConversationCompaction:
    """复杂问题：长对话压缩"""

    @staticmethod
    def _make_engine(provider: _FakeProvider) -> QueryEngine:
        engine = QueryEngine(model_provider=provider)
        engine.L1_MESSAGE_THRESHOLD = 999999
        engine.L2_MESSAGE_THRESHOLD = 999999
        engine._total_messages_sent = 0
        engine._l1_last_triggered_at = -1
        return engine

    def test_compaction_preserves_recent_context(self) -> None:
        provider = _FakeProvider([
            ModelResponse(content=f"R{i}", model="test", usage=ModelUsage())
            for i in range(6)
        ])
        engine = QueryEngine(
            config=QueryEngineConfig(compact_after_turns=4),
            model_provider=provider,
        )

        for i in range(6):
            engine.submit(f"问题{i}")

        assert len(engine._messages) <= 7
        assert any("压缩" in m for m in engine._messages)

        recent_responses = [m for m in engine._messages if m.startswith("R")]
        assert len(recent_responses) >= 2

    def test_compaction_keeps_even_count(self) -> None:
        engine = QueryEngine(config=QueryEngineConfig(compact_after_turns=4))
        for i in range(10):
            engine._messages.append(f"p{i}")
            engine._messages.append(f"r{i}")
        engine._compact_if_needed()

        prompt_response_pairs = len(engine._messages) - (1 if "压缩" in engine._messages[0] else 0)
        assert prompt_response_pairs % 2 == 0


class TestStreamingComplexScenarios:
    """复杂问题：流式调用"""

    @staticmethod
    def _make_engine(provider: _FakeProvider) -> QueryEngine:
        engine = QueryEngine(model_provider=provider)
        engine.L1_MESSAGE_THRESHOLD = 999999
        engine.L2_MESSAGE_THRESHOLD = 999999
        engine._total_messages_sent = 0
        engine._l1_last_triggered_at = -1
        engine._messages.clear()
        engine._conversation.clear()
        return engine

    def test_stream_with_tool_calls(self) -> None:
        recorder = _ToolRecorder()
        registry = _make_registry_with_tools(recorder)
        runtime = _make_runtime(registry)

        provider = _FakeProvider([
            ModelResponse(
                content="", model="test", usage=ModelUsage(),
                tool_calls=(ToolCall(id="tc1", name="read", arguments='{"path": "test.py"}'),),
            ),
            ModelResponse(content="文件内容已分析", model="test", usage=ModelUsage()),
        ])

        engine = self._make_engine(provider)
        engine.set_runtime(runtime)

        events = list(engine.stream_call_model("分析 test.py"))

        event_types = [e["type"] for e in events]
        assert "tool_call_start" in event_types
        assert "tool_call_end" in event_types
        assert "done" in event_types

        assert len(recorder.calls) == 1
        assert recorder.calls[0][0] == "read"

        assert len(engine._conversation) == 2
        assert engine._conversation[-1] == ("assistant", "文件内容已分析")

    def test_stream_persists_to_messages(self) -> None:
        provider = _FakeProvider([
            ModelResponse(content="流式回答", model="test", usage=ModelUsage()),
        ])
        engine = self._make_engine(provider)

        events = list(engine.stream_call_model("问题"))
        assert events[-1]["type"] == "done"

        assert len(engine._conversation) == 2
        assert engine._conversation[0] == ("user", "问题")
        assert engine._conversation[1] == ("assistant", "流式回答")


class TestMaxTurnsAndBudget:
    """复杂问题：轮次和预算限制"""

    @staticmethod
    def _make_engine(provider: _FakeProvider) -> QueryEngine:
        engine = QueryEngine(model_provider=provider)
        engine.L1_MESSAGE_THRESHOLD = 999999
        engine.L2_MESSAGE_THRESHOLD = 999999
        engine._total_messages_sent = 0
        engine._l1_last_triggered_at = -1
        return engine


    def test_max_turns_stops_new_queries(self) -> None:
        engine = QueryEngine(config=QueryEngineConfig(max_turns=2))
        engine._messages = ["p1", "r1", "p2", "r2"]
        engine._conversation = [("user", "p1"), ("assistant", "r1"), ("user", "p2"), ("assistant", "r2")]

        result = engine.submit("问题3")
        assert result.stop_reason == StopReason.MAX_TURNS_REACHED
        assert "最大轮次" in result.output

    def test_usage_accumulates_across_turns(self) -> None:
        provider = _FakeProvider([
            ModelResponse(
                content="回答1", model="test",
                usage=ModelUsage(input_tokens=100, output_tokens=50),
            ),
            ModelResponse(
                content="回答2", model="test",
                usage=ModelUsage(input_tokens=200, output_tokens=100),
            ),
        ])
        engine = self._make_engine(provider)
        engine.submit("问题1")
        engine.submit("问题2")

        stats = engine.get_stats()
        assert stats["turns"] == 2


class TestSessionPersistenceRoundtrip:
    """复杂问题：会话持久化完整回环"""

    @staticmethod
    def _make_engine(provider: _FakeProvider) -> QueryEngine:
        engine = QueryEngine(model_provider=provider)
        engine.L1_MESSAGE_THRESHOLD = 999999
        engine.L2_MESSAGE_THRESHOLD = 999999
        engine._total_messages_sent = 0
        engine._l1_last_triggered_at = -1
        return engine


    def test_save_load_preserves_all_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sm = SessionManager(save_dir=Path(tmpdir))
            provider = _FakeProvider([
                ModelResponse(content=f"回答{i}", model="test", usage=ModelUsage())
                for i in range(5)
            ])
            engine = QueryEngine(session_manager=sm, model_provider=provider)

            for i in range(5):
                engine.submit(f"问题{i}")

            sid = engine.session_id
            assert engine.persist_session().is_ok

            engine2 = QueryEngine(session_manager=sm, model_provider=provider)
            assert engine2.load_session(sid)
            assert engine2.turn_count == 5
            assert engine2._messages == engine._messages
            assert len(engine2._conversation) == 10

            for i in range(5):
                assert ("user", f"问题{i}") in engine2._conversation
                assert ("assistant", f"回答{i}") in engine2._conversation

    def test_multiple_sessions_coexist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sm = SessionManager(save_dir=Path(tmpdir))
            provider = _FakeProvider()

            engine1 = QueryEngine(session_manager=sm, model_provider=provider)
            provider.responses = [ModelResponse(content="A", model="test", usage=ModelUsage())]
            engine1.submit("问A")
            assert engine1.persist_session().is_ok

            engine2 = QueryEngine(session_manager=sm, model_provider=provider)
            provider.responses = [ModelResponse(content="B", model="test", usage=ModelUsage())]
            engine2.submit("问B")
            assert engine2.persist_session().is_ok

            engine1_loaded = QueryEngine(session_manager=sm, model_provider=provider)
            assert engine1_loaded.load_session(engine1.session_id)
            assert engine1_loaded.turn_count == 1

            engine2_loaded = QueryEngine(session_manager=sm, model_provider=provider)
            assert engine2_loaded.load_session(engine2.session_id)
            assert engine2_loaded.turn_count == 1

            assert engine1_loaded._messages != engine2_loaded._messages

    def test_reset_clears_all_state(self) -> None:
        provider = _FakeProvider([
            ModelResponse(content="回答", model="test", usage=ModelUsage()),
        ])
        engine = self._make_engine(provider)
        engine.submit("问题")

        old_sid = engine.session_id
        engine.reset()

        assert engine.session_id != old_sid
        assert engine.turn_count == 0
        assert len(engine._messages) == 0
        assert len(engine._conversation) == 0


class TestErrorRecoveryComplex:
    """复杂问题：错误恢复"""

    @staticmethod
    def _make_engine(provider: _FakeProvider) -> QueryEngine:
        engine = QueryEngine(model_provider=provider)
        engine.L1_MESSAGE_THRESHOLD = 999999
        engine.L2_MESSAGE_THRESHOLD = 999999
        engine._total_messages_sent = 0
        engine._l1_last_triggered_at = -1
        return engine

    def test_tool_error_followed_by_retry(self) -> None:
        recorder = _ToolRecorder()
        registry = _make_registry_with_tools(recorder)
        runtime = _make_runtime(registry)

        provider = _FakeProvider([
            ModelResponse(
                content="", model="test", usage=ModelUsage(),
                tool_calls=(ToolCall(id="tc1", name="read", arguments='{"path": "/nonexistent.py"}'),),
            ),
            ModelResponse(
                content="", model="test", usage=ModelUsage(),
                tool_calls=(ToolCall(id="tc2", name="read", arguments='{"path": "real.py"}'),),
            ),
            ModelResponse(content="找到了，内容是...", model="test", usage=ModelUsage()),
        ])

        engine = self._make_engine(provider)
        engine.set_runtime(runtime)
        result = engine.submit("读取 /nonexistent.py")

        assert result.stop_reason == StopReason.COMPLETED
        assert len(recorder.calls) == 2

    def test_malformed_tool_arguments(self) -> None:
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="read", description="Read",
            parameters={"path": {"type": "string"}},
            handler=lambda path="": {"content": "ok"},
        ))
        runtime = _make_runtime(registry)

        engine = QueryEngine()
        engine.set_runtime(runtime)

        result = engine._execute_tool("read", "not valid json {{{")
        parsed = json.loads(result)
        assert "error" in parsed

    def test_provider_error_returns_gracefully(self) -> None:
        class _ErrorProvider(ModelProvider):
            def complete(self, messages, config=None, tools=None):
                from lingclaude.core.types import Result
                return Result.fail("API 超时", code="TIMEOUT")

            async def acomplete(self, messages, config=None, tools=None):
                from lingclaude.core.types import Result
                return Result.fail("API 超时", code="TIMEOUT")

            def count_tokens(self, text: str) -> int:
                return 0

        engine = QueryEngine(model_provider=_ErrorProvider())
        result = engine.submit("问题")
        assert "模型调用失败" in result.output
        assert engine.turn_count == 1


class TestToolArgumentRetry:
    """复杂问题：工具参数自动修复"""

    @staticmethod
    def _make_engine(provider: _FakeProvider) -> QueryEngine:
        engine = QueryEngine(model_provider=provider)
        engine.L1_MESSAGE_THRESHOLD = 999999
        engine.L2_MESSAGE_THRESHOLD = 999999
        engine._total_messages_sent = 0
        engine._l1_last_triggered_at = -1
        return engine


    def test_read_retry_with_glob(self) -> None:
        engine = QueryEngine()
        result = engine._fix_tool_arguments(
            "glob",
            json.dumps({"pattern": "test.py"}),
            '{"error": "找不到"}',
        )
        assert result is not None
        parsed = json.loads(result)
        assert "**/" in parsed["pattern"]


class TestConversationIntegrity:
    """复杂问题：对话完整性（_messages 和 _conversation 同步）"""

    @staticmethod
    def _make_engine(provider: _FakeProvider) -> QueryEngine:
        engine = QueryEngine(model_provider=provider)
        engine.L1_MESSAGE_THRESHOLD = 999999
        engine.L2_MESSAGE_THRESHOLD = 999999
        engine._total_messages_sent = 0
        engine._l1_last_triggered_at = -1
        return engine


    def test_messages_and_conversation_always_synced(self) -> None:
        provider = _FakeProvider()
        engine = self._make_engine(provider)

        provider.responses = [
            ModelResponse(content=f"R{i}", model="test", usage=ModelUsage())
            for i in range(10)
        ]
        provider._idx = 0
        for i in range(10):
            engine.submit(f"Q{i}")

            expected_conv = len(engine._messages)
            assert len(engine._conversation) == expected_conv

    def test_submit_adds_both_prompt_and_response(self) -> None:
        provider = _FakeProvider([
            ModelResponse(content="my answer", model="test", usage=ModelUsage()),
        ])
        engine = self._make_engine(provider)
        engine.submit("my question")

        assert engine._messages == ["my question", "my answer"]
        assert engine._conversation == [("user", "my question"), ("assistant", "my answer")]
        assert engine._transcript == ["my answer"]

    def test_stream_call_model_updates_conversation(self) -> None:
        provider = _FakeProvider([
            ModelResponse(content="stream answer", model="test", usage=ModelUsage()),
        ])
        engine = self._make_engine(provider)
        list(engine.stream_call_model("stream question"))

        assert engine._conversation == [("user", "stream question"), ("assistant", "stream answer")]

    def test_stats_reflect_actual_turns(self) -> None:
        provider = _FakeProvider()
        engine = self._make_engine(provider)

        provider.responses = [
            ModelResponse(content=f"A{i}", model="test", usage=ModelUsage())
            for i in range(7)
        ]
        provider._idx = 0
        for i in range(7):
            engine.submit(f"Q{i}")

        stats = engine.get_stats()
        assert stats["turns"] == 7
        assert stats["transcript_size"] == 7


class TestStreamingMultiTurnWithTools:
    """复杂问题：流式多轮+工具"""

    @staticmethod
    def _make_engine(provider: _FakeProvider) -> QueryEngine:
        engine = QueryEngine(model_provider=provider)
        engine.L1_MESSAGE_THRESHOLD = 999999
        engine.L2_MESSAGE_THRESHOLD = 999999
        engine._total_messages_sent = 0
        engine._l1_last_triggered_at = -1
        return engine


    def test_stream_two_turns_with_tools(self) -> None:
        recorder = _ToolRecorder()
        registry = _make_registry_with_tools(recorder)
        runtime = _make_runtime(registry)

        provider = _FakeProvider([
            ModelResponse(
                content="", model="test", usage=ModelUsage(),
                tool_calls=(ToolCall(id="tc1", name="read", arguments='{"path": "a.py"}'),),
            ),
            ModelResponse(content="a.py 分析完成", model="test", usage=ModelUsage()),
            ModelResponse(
                content="", model="test", usage=ModelUsage(),
                tool_calls=(ToolCall(id="tc2", name="grep", arguments='{"pattern": "class", "path": "src"}'),),
            ),
            ModelResponse(content="找到3个class", model="test", usage=ModelUsage()),
        ])

        engine = self._make_engine(provider)
        engine.set_runtime(runtime)

        events = list(engine.stream_call_model("分析 a.py"))
        assert events[-1]["type"] == "done"
        assert events[-1]["content"] == "a.py 分析完成"

        events2 = list(engine.stream_call_model("src下有哪些class？"))
        assert events2[-1]["type"] == "done"
        assert "找到3个class" in events2[-1]["content"]

        assert len(engine._conversation) == 4

        last_call = provider.call_log[-1][0]
        conv_in_request = [m for m in last_call if m.role.value in ("user", "assistant")]
        assert len(conv_in_request) >= 2


class TestEdgeCasesComplex:
    """边界条件"""

    @staticmethod
    def _make_engine(provider: _FakeProvider) -> QueryEngine:
        engine = QueryEngine(model_provider=provider)
        engine.L1_MESSAGE_THRESHOLD = 999999
        engine.L2_MESSAGE_THRESHOLD = 999999
        engine._total_messages_sent = 0
        engine._l1_last_triggered_at = -1
        return engine


    def test_empty_prompt(self) -> None:
        provider = _FakeProvider([
            ModelResponse(content="请说点什么", model="test", usage=ModelUsage()),
        ])
        engine = self._make_engine(provider)
        result = engine.submit("")
        assert result.stop_reason == StopReason.COMPLETED
        assert engine.turn_count == 1

    def test_very_long_prompt(self) -> None:
        long_prompt = "x" * 50000
        provider = _FakeProvider([
            ModelResponse(content="ok", model="test", usage=ModelUsage()),
        ])
        engine = self._make_engine(provider)
        result = engine.submit(long_prompt)
        assert result.stop_reason == StopReason.COMPLETED
        assert engine._messages[0] == long_prompt

    def test_unicode_content(self) -> None:
        provider = _FakeProvider([
            ModelResponse(content="你好世界 🌍 テスト", model="test", usage=ModelUsage()),
        ])
        engine = self._make_engine(provider)
        result = engine.submit("你好")
        assert "你好世界" in result.output

        with tempfile.TemporaryDirectory() as tmpdir:
            sm = SessionManager(save_dir=Path(tmpdir))
            engine.session_manager = sm
            assert engine.persist_session().is_ok

            engine2 = QueryEngine(session_manager=sm, model_provider=provider)
            assert engine2.load_session(engine.session_id)
            assert "你好世界" in engine2._messages[1]

    def test_concurrent_sessions_isolation(self) -> None:
        provider_a = _FakeProvider([
            ModelResponse(content="A回答", model="test", usage=ModelUsage()),
        ])
        provider_b = _FakeProvider([
            ModelResponse(content="B回答", model="test", usage=ModelUsage()),
        ])
        engine_a = QueryEngine(model_provider=provider_a)
        engine_b = QueryEngine(model_provider=provider_b)

        engine_a.submit("A问题")
        engine_b.submit("B问题")

        assert engine_a._messages == ["A问题", "A回答"]
        assert engine_b._messages == ["B问题", "B回答"]
        assert engine_a.session_id != engine_b.session_id
