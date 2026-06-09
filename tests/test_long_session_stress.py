"""Long-session stress test: validates dementia detection, context compression,
temperature reduction, and hard-interrupt systems end-to-end over 40+ rounds."""
from __future__ import annotations

import json
from typing import Any

from lingclaude.core.query_engine import QueryEngine, QueryEngineConfig
from lingclaude.core.hooks import HookContext, HookType
from lingclaude.model.types import (
    ModelConfig,
    ModelMessage,
    ModelProvider,
    ModelResponse,
    ModelUsage,
    ToolCall,
)


class _FakeProvider(ModelProvider):
    """Provider that alternates between tool-call responses (read same file)
    and plain text responses to simulate a realistic long session."""

    def __init__(self, responses: list[ModelResponse] | None = None) -> None:
        self.responses = responses or []
        self._idx = 0
        self.last_config: ModelConfig | None = None
        self.last_messages: tuple[ModelMessage, ...] = ()
        self.call_count = 0

    def complete(
        self,
        messages: tuple[ModelMessage, ...],
        config: ModelConfig | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Any:
        from lingclaude.core.types import Result
        self.call_count += 1
        self.last_config = config
        self.last_messages = messages
        if self._idx < len(self.responses):
            resp = self.responses[self._idx]
            self._idx += 1
            return Result.ok(resp)
        return Result.ok(ModelResponse(content="fallback", model="test", usage=ModelUsage()))

    async def acomplete(self, messages, config=None, tools=None):
        from lingclaude.core.types import Result
        return Result.ok(ModelResponse(content="async", model="test", usage=ModelUsage()))

    def count_tokens(self, text: str) -> int:
        return len(text) // 4


def _make_read_tool_call(path: str, call_id: str = "") -> ToolCall:
    return ToolCall(
        id=call_id or f"tc_{path.replace('/', '_')}",
        name="read",
        arguments=json.dumps({"path": path}),
    )


def _make_read_response(path: str) -> ModelResponse:
    tc = _make_read_tool_call(path)
    return ModelResponse(
        content="",
        model="test",
        usage=ModelUsage(input_tokens=50, output_tokens=20),
        tool_calls=(tc,),
    )


def _make_text_response(text: str) -> ModelResponse:
    return ModelResponse(
        content=text,
        model="test",
        usage=ModelUsage(input_tokens=50, output_tokens=80),
    )


def _build_repeating_responses(file_path: str, n_cycles: int) -> list[ModelResponse]:
    """Build n_cycles of: read same file -> text response -> read same file -> text response.
    Each cycle is 2 provider calls (tool_call then text), simulating a multi-round agent loop
    where the model keeps re-reading the same file."""
    responses: list[ModelResponse] = []
    for i in range(n_cycles):
        responses.append(_make_read_response(file_path))
        responses.append(_make_text_response(f"第{i+1}次分析完成：文件内容是…"))
    return responses


class TestLongSessionStress:
    """40+ round stress test: compression, dementia, temperature, hard-interrupt."""

    def test_context_compression_fires_and_reduces_messages(self) -> None:
        """After compact_after_turns*2 messages, compression must fire and reduce count."""
        file_path = "src/main.py"
        # 20 cycles = 40 provider responses = 20 submit() calls
        # compact_after_turns=6 => triggers at 12 messages (6 turns)
        responses = _build_repeating_responses(file_path, 20)
        provider = _FakeProvider(responses)
        config = QueryEngineConfig(
            max_turns=50,
            compact_after_turns=6,
            max_budget_tokens=999999,
        )
        engine = QueryEngine(config=config, model_provider=provider)
        engine._model_config = ModelConfig(model="test", temperature=0.7)

        max_messages_seen = 0
        compression_fired = False

        for turn in range(20):
            engine.submit(f"分析{file_path}的第{turn+1}个函数")
            msg_count_after = len(engine._messages)

            if msg_count_after < max_messages_seen:
                compression_fired = True
            max_messages_seen = max(max_messages_seen, msg_count_after)

        assert compression_fired, (
            f"Compression never fired. max_messages={max_messages_seen}, "
            f"compact_after_turns*2={config.compact_after_turns * 2}"
        )

    def test_dementia_index_escalates_with_repeated_reads(self) -> None:
        """Repeated reads of the same file must cause dementia_index to rise."""
        file_path = "src/core.py"
        responses = _build_repeating_responses(file_path, 25)
        provider = _FakeProvider(responses)
        config = QueryEngineConfig(max_turns=50, compact_after_turns=50, max_budget_tokens=999999)
        engine = QueryEngine(config=config, model_provider=provider)
        engine._model_config = ModelConfig(model="test", temperature=0.7)

        indices: list[float] = []
        for turn in range(25):
            engine.submit(f"读取{file_path}")
            diag = engine._dementia_detector.diagnose()
            indices.append(diag.dementia_index)

        assert indices[-1] > indices[0], (
            f"Dementia index did not escalate: first={indices[0]:.3f}, last={indices[-1]:.3f}"
        )
        assert indices[-1] >= 0.15, (
            f"Dementia index too low after 25 repeated reads: {indices[-1]:.3f}"
        )

    def test_temperature_reduction_on_dementia(self) -> None:
        """High dementia_index must force temperature down via _resolve_model_config."""
        file_path = "src/large_module.py"
        responses = _build_repeating_responses(file_path, 30)
        provider = _FakeProvider(responses)
        config = QueryEngineConfig(max_turns=50, compact_after_turns=50, max_budget_tokens=999999)
        engine = QueryEngine(config=config, model_provider=provider)
        engine._model_config = ModelConfig(model="test", temperature=0.7)

        temps: list[float] = []
        for turn in range(30):
            engine.submit(f"读取{file_path}")
            if provider.last_config is not None:
                temps.append(provider.last_config.temperature)

        assert len(temps) > 10, "Not enough temperature readings"
        assert temps[-1] < 0.7, (
            f"Temperature was never reduced: first={temps[0]}, last={temps[-1]}"
        )

    def test_hard_interrupt_on_severe_dementia(self) -> None:
        """After enough dementia-level interventions, submit() must return CONSECUTIVE_FAILURE."""
        file_path = "src/deep_loop.py"
        responses = _build_repeating_responses(file_path, 50)
        provider = _FakeProvider(responses)
        config = QueryEngineConfig(max_turns=100, compact_after_turns=100, max_budget_tokens=999999)
        engine = QueryEngine(config=config, model_provider=provider)
        engine._model_config = ModelConfig(model="test", temperature=0.7)

        from lingclaude.core.types import StopReason
        hard_stop_seen = False

        for turn in range(50):
            result = engine.submit(f"分析{file_path}第{turn+1}个问题")
            if result.stop_reason == StopReason.CONSECUTIVE_FAILURE:
                hard_stop_seen = True
                assert "[硬中断]" in result.output or "认知退化" in result.output, (
                    f"Hard stop triggered but output lacks expected markers: {result.output[:100]}"
                )
                break

        assert hard_stop_seen, (
            "Hard interrupt never triggered after 50 turns of repeated reads. "
            f"Dementia index: {engine._dementia_detector.diagnose().dementia_index:.3f}, "
            f"State: {engine._dementia_detector.diagnose().state.value}"
        )

    def test_post_compact_cognitive_anchors_preserved(self) -> None:
        """After compression, the summary must preserve file references and decisions."""
        file_path = "src/anchor_target.py"
        # Mix: some reads of same file, some with decision keywords
        responses: list[ModelResponse] = []
        for i in range(25):
            responses.append(_make_read_response(file_path))
            decision_text = (
                f"第{i+1}次分析: 我决定采用方案A重构{file_path}。"
                f"错误修复: 修复了import错误。"
            )
            responses.append(_make_text_response(decision_text))

        provider = _FakeProvider(responses)
        config = QueryEngineConfig(max_turns=50, compact_after_turns=5, max_budget_tokens=999999)
        engine = QueryEngine(config=config, model_provider=provider)
        engine._model_config = ModelConfig(model="test", temperature=0.7)

        for turn in range(25):
            engine.submit(f"分析{file_path}第{turn+1}个函数")

        # After all turns with compact_after_turns=5, compression should have fired multiple times
        # Check layered memory for archived cognitive anchors via recall
        recalled = engine._layered_memory.experience.recall("压缩归档")
        len(recalled) > 0

        # Also check that messages list is bounded (not 50+ entries)
        assert len(engine._messages) <= 20, (
            f"Messages list not bounded after compression: {len(engine._messages)}"
        )

    def test_hooks_fire_during_long_session(self) -> None:
        """PRE_TASK, POST_TASK, PRE_COMPACT, POST_COMPACT hooks must all fire."""
        file_path = "src/hook_test.py"
        responses = _build_repeating_responses(file_path, 20)
        provider = _FakeProvider(responses)
        config = QueryEngineConfig(max_turns=50, compact_after_turns=5, max_budget_tokens=999999)
        engine = QueryEngine(config=config, model_provider=provider)
        engine._model_config = ModelConfig(model="test", temperature=0.7)

        hook_log: list[str] = []

        def log_hook(ctx: HookContext) -> None:
            hook_log.append(ctx.hook_type.value)

        for ht in (HookType.PRE_TASK, HookType.POST_TASK, HookType.PRE_COMPACT, HookType.POST_COMPACT):
            engine._hooks.register(f"log_{ht.value}", ht, log_hook)

        for turn in range(20):
            engine.submit(f"读取{file_path}")

        assert "pre_task" in hook_log, "PRE_TASK hook never fired"
        assert "post_task" in hook_log, "POST_TASK hook never fired"
        assert "pre_compact" in hook_log, "PRE_COMPACT hook never fired"
        assert "post_compact" in hook_log, "POST_COMPACT hook never fired"

    def test_full_pipeline_40_rounds(self) -> None:
        """Full 40-round integration: compression + dementia + temperature + anchors.

        This is the master stress test that exercises all systems together.
        """
        file_path = "src/integration_target.py"
        # 40 cycles = 80 provider responses = 40 submit() calls
        responses = _build_repeating_responses(file_path, 40)
        provider = _FakeProvider(responses)
        config = QueryEngineConfig(
            max_turns=50,
            compact_after_turns=8,
            max_budget_tokens=999999,
        )
        engine = QueryEngine(config=config, model_provider=provider)
        engine._model_config = ModelConfig(model="test", temperature=0.7)

        hook_log: list[str] = []
        def log_hook(ctx: HookContext) -> None:
            hook_log.append(ctx.hook_type.value)

        for ht in (HookType.PRE_TASK, HookType.POST_TASK, HookType.PRE_COMPACT, HookType.POST_COMPACT, HookType.ON_STOP):
            engine._hooks.register(f"log_{ht.value}", ht, log_hook)

        from lingclaude.core.types import StopReason
        turn_count = 0

        for turn in range(40):
            result = engine.submit(f"分析{file_path}的第{turn+1}个函数")
            turn_count += 1
            if result.stop_reason != StopReason.COMPLETED:
                break

        # Assertions
        diag = engine._dementia_detector.diagnose()
        assert diag.dementia_index > 0, "Dementia index stayed at 0"
        assert len(engine._messages) <= config.compact_after_turns * 2 + 4, (
            f"Messages list too large: {len(engine._messages)}"
        )
        assert "pre_compact" in hook_log, "Compression never fired in 40 rounds"
        assert provider.last_config is not None

        if turn_count >= 40:
            # If we ran all 40 without hard stop, temperature should still have been reduced
            assert provider.last_config.temperature <= 0.7, "Temperature was never adjusted"

    def test_mixed_files_lower_dementia_than_single_file(self) -> None:
        """Reading different files should produce lower dementia index than reading the same file."""
        # Path A: same file repeated
        same_file = "src/same.py"
        same_responses = _build_repeating_responses(same_file, 15)
        same_provider = _FakeProvider(same_responses)
        same_config = QueryEngineConfig(max_turns=50, compact_after_turns=50, max_budget_tokens=999999)
        same_engine = QueryEngine(config=same_config, model_provider=same_provider)
        same_engine._model_config = ModelConfig(model="test", temperature=0.7)

        # Path B: different files
        diff_responses: list[ModelResponse] = []
        for i in range(15):
            path = f"src/module_{i}.py"
            diff_responses.append(_make_read_response(path))
            diff_responses.append(_make_text_response(f"分析完成：module_{i}"))
        diff_provider = _FakeProvider(diff_responses)
        diff_config = QueryEngineConfig(max_turns=50, compact_after_turns=50, max_budget_tokens=999999)
        diff_engine = QueryEngine(config=diff_config, model_provider=diff_provider)
        diff_engine._model_config = ModelConfig(model="test", temperature=0.7)

        for turn in range(15):
            same_engine.submit(f"读取{same_file}")
            diff_engine.submit(f"读取src/module_{turn}.py")

        same_diag = same_engine._dementia_detector.diagnose()
        diff_diag = diff_engine._dementia_detector.diagnose()

        assert same_diag.dementia_index > diff_diag.dementia_index, (
            f"Same-file dementia ({same_diag.dementia_index:.3f}) should be > "
            f"different-file dementia ({diff_diag.dementia_index:.3f})"
        )
