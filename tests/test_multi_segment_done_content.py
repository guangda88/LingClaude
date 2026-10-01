"""M-A 回归（2026-10-01）：多段正文轮次 done.content 完整性。

事故：tui_style_debug.log 16:34/18:28/19:13 FP_MISMATCH 实证——工具轮的
前段文本只进 response_content（loop-abort/超轮次才消费），正常收尾
done.content 恒缺开头段 → 上色指纹必败（用户长期只见素字），且
_finalize_turn 落库的会话历史同残缺。

修复：终轮收尾三源 join（response_content + continuation_buffer +
round_content），本文件锁定三场景不回归。
"""
from __future__ import annotations

from typing import Any

from lingclaude.core.types import Result
from lingclaude.engine.loop.loop_body import run_stream_call_model_loop


def _mk_resp(content: str, tools: tuple = (), finish_reason: str = "stop") -> Any:
    from lingclaude.core.model_types import ModelResponse, ModelUsage

    return ModelResponse(
        content=content,
        model="t",
        usage=ModelUsage(input_tokens=1, output_tokens=1),
        finish_reason=finish_reason,
        tool_calls=tools,
    )


def _tc() -> tuple:
    from lingclaude.core.model_types import ToolCall

    return (ToolCall(id="c1", name="fake", arguments="{}"),)


class _Behavior:
    """engine._behavior 链式打点桩（record_tool_calls 返回自身）。"""

    def record_tool_calls(self, count: int = 0, errors: int = 0) -> "_Behavior":
        return self


class _Hooks:
    def journal_append(self, *a: Any, **k: Any) -> None: ...
    def record_provider_outcome(self, *a: Any, **k: Any) -> None: ...
    def should_hallucination_correct(self, *a: Any, **k: Any) -> bool:
        return False
    def log_flywheel(self, *a: Any, **k: Any) -> None: ...


class _Engine:
    """最小流式引擎 stub（结构对齐 tests/test_truncation_guard.py）。"""

    def __init__(self, rounds: list[Any]) -> None:
        self._rounds = list(rounds)
        self._provider = self  # loop_body 经 engine._provider.stream_complete 调用
        self._behavior = _Behavior()
        self.hooks = _Hooks()
        self.session_id = "sess-ma"
        self._messages: list[Any] = []
        self.finalized_content: str | None = None

        class _Cfg:
            consecutive_failure_limit = 3
        self.config = _Cfg()

    def _build_messages(self, prompt: str, image_content: Any = None) -> list[Any]:
        from lingclaude.model.types import ModelMessage, MessageRole
        self._messages = [ModelMessage(role=MessageRole.USER, content=prompt)]
        return list(self._messages)

    def _build_openai_tools(self, query: str = "") -> tuple[dict, ...]:
        return ()

    def _resolve_model_config(self, prompt: str) -> tuple[Any, Any]:
        from lingclaude.core.model_types import ModelConfig
        return ModelConfig(max_tokens=1000), None

    def _log_model_request(self, prompt=None, messages=None, tools=None, **k) -> None: ...
    def _assert_model_visible(self, seq, messages) -> None: ...
    def _pre_send_check(self, seq, messages) -> bool:
        return True
    def _log_to_flywheel(self, *a: Any, **k: Any) -> None: ...
    def _learn_from_turn(self, prompt, response) -> None: ...
    def _save_checkpoint(self, *a: Any, **k: Any) -> None: ...
    def _clear_checkpoint(self) -> None: ...
    def _append_to_session_history(self, *a: Any, **k: Any) -> None: ...

    def _execute_tool_with_retry(self, name: str, arguments: str) -> str:
        return "ok"

    def _finalize_turn(self, prompt, content, used_tools, ti, to, cfg, tc,
                       ctx_input_tokens=None) -> str:
        self.finalized_content = content  # 落库口径可观测
        return content

    def complete(self, messages, config=None, tools=None, **kw) -> Result:
        return Result.ok(self._rounds.pop(0))

    def stream_complete(self, messages, config=None, tools=None):
        result = self.complete(messages, config, tools)
        if result.is_error:
            yield {"type": "error", "error": result.error}
            return
        resp = result.data
        if resp.content:
            yield {"type": "text_delta", "text": resp.content}
        for tc in resp.tool_calls:
            yield {"type": "tool_call_complete", "id": tc.id,
                   "name": tc.name, "arguments": tc.arguments}
        yield {"type": "finish", "reason": resp.finish_reason}


def _collect(engine: _Engine) -> list[dict]:
    return list(run_stream_call_model_loop(engine, "hi"))


def _done_content(events: list[dict]) -> str:
    dones = [e for e in events if e["type"] == "done"]
    assert len(dones) == 1, f"done 事件数异常: {len(dones)}"
    return dones[0]["content"]


class TestMultiSegmentDoneContent:
    def test_tool_turn_head_and_tail_both_present(self) -> None:
        """主场景：text→工具→text。done.content 必含前段与终段。"""
        engine = _Engine([
            _mk_resp("先说一下思路，干活前交底。", tools=_tc()),
            _mk_resp("收尾总结，本轮完工。"),
        ])
        content = _done_content(_collect(engine))
        assert "先说一下思路" in content, f"前段丢失: {content!r}"
        assert "收尾总结" in content, f"终段丢失: {content!r}"
        # 落库口径与 done.content 一致（历史残缺同源修复验证）
        assert engine.finalized_content == content

    def test_truncation_continuation_no_dup_no_loss(self) -> None:
        """截断续写：buffer 轮 + 终轮各出现一次（旧行为等价，不回归）。"""
        engine = _Engine([
            _mk_resp("第一段", finish_reason="length"),
            _mk_resp("第二段"),
        ])
        content = _done_content(_collect(engine))
        assert content.count("第一段") == 1, f"重复/丢失: {content!r}"
        assert content.count("第二段") == 1
        assert "第一段" in content and "第二段" in content

    def test_pure_single_round_unchanged(self) -> None:
        """纯单轮（无工具）：content == 本轮文本，零变化。"""
        engine = _Engine([_mk_resp("唯一一段")])
        content = _done_content(_collect(engine))
        assert content == "唯一一段"

    def test_tool_turn_pure_no_head_text(self) -> None:
        """工具轮无前段文本：content == 终轮文本（不产生多余空段）。"""
        engine = _Engine([
            _mk_resp("", tools=_tc()),
            _mk_resp("直接给结论。"),
        ])
        content = _done_content(_collect(engine))
        assert content == "直接给结论。"
