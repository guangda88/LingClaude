"""R-truncation (2026-09-23): 输出截断四落点修复的回归用例。

- A: max_tokens 截断自动续写（流式 + 非流式对称），护栏 3 次，护栏外打标
- B: 诊断类工具瘦身分型（bash/read/grep/glob 门槛 3200 / 保留 2400）
- C: spill stub 带 read_hint 拼接指引
"""
from __future__ import annotations

from typing import Any

from lingclaude.core.types import Result
from lingclaude.engine.loop import loop_body
from lingclaude.engine.loop.loop_body import (
    _CONTINUATION_HINT,
    _DIAGNOSTIC_TOOLS,
    _INCOMPLETE_TAG,
    _MAX_CONTINUATIONS,
    _TOOL_RESULT_SLIM_KEEP,
    _TOOL_RESULT_SLIM_KEEP_DIAG,
    _slim_tool_output,
    run_stream_call_model_loop,
)


def _mk_resp(content: str, finish_reason: str = "stop") -> Any:
    from lingclaude.core.model_types import ModelResponse, ModelUsage
    return ModelResponse(
        content=content, model="t", usage=ModelUsage(input_tokens=1, output_tokens=1),
        finish_reason=finish_reason,
    )


class _ScriptedStreamProvider:
    """complete() 按剧本出牌；stream_complete 用基类默认（finish 事件透传 reason）。"""

    def __init__(self, rounds: list[Any]) -> None:
        self._rounds = list(rounds)
        self.seen_messages: list[Any] = []

    def complete(self, messages, config=None, tools=None, **kw) -> Result:
        self.seen_messages.append(tuple(messages))
        return Result.ok(self._rounds.pop(0))

    async def acomplete(self, messages, config=None, tools=None, **kw) -> Result:
        return self.complete(messages, config, tools, **kw)

    def count_tokens(self, text: str) -> int:
        return len(text) // 4

    def stream_complete(self, messages, config=None, tools=None):
        result = self.complete(messages, config, tools)
        if result.is_error:
            yield {"type": "error", "error": result.error}
            return
        resp = result.data
        if resp.content:
            yield {"type": "text_delta", "text": resp.content}
        for tc in resp.tool_calls:
            yield {"type": "tool_call_complete", "id": tc.id, "name": tc.name, "arguments": tc.arguments}
        yield {"type": "finish", "reason": resp.finish_reason}


class _Hooks:
    def journal_append(self, *a, **k) -> None: ...
    def record_provider_outcome(self, *a, **k) -> None: ...
    def should_hallucination_correct(self, *a, **k) -> bool:
        return False
    def log_flywheel(self, *a, **k) -> None: ...


class _Engine:
    """最小流式引擎 stub（仅覆盖 run_stream_call_model_loop 触点）。"""

    def __init__(self, provider: Any) -> None:
        self._provider = provider
        self.hooks = _Hooks()
        self.session_id = "sess-t"
        self._messages: list[Any] = []
        self._behavior = None  # A 仅无工具路径，behavior 不触

        class _Cfg:
            consecutive_failure_limit = 3
        self.config = _Cfg()

    def _build_messages(self, prompt: str) -> list[Any]:
        from lingclaude.model.types import ModelMessage, MessageRole
        self._messages = [ModelMessage(role=MessageRole.USER, content=prompt)]
        return list(self._messages)

    def _build_openai_tools(self, query: str = "") -> tuple[dict, ...]:
        return ()

    def _resolve_model_config(self, prompt: str) -> tuple[Any, Any]:
        from lingclaude.core.model_types import ModelConfig
        return ModelConfig(max_tokens=100), None

    def _log_model_request(self, prompt=None, messages=None, tools=None, **k) -> None: ...
    def _assert_model_visible(self, seq, messages) -> None: ...
    def _pre_send_check(self, seq, messages) -> bool:
        return True
    def _log_to_flywheel(self, *a, **k) -> None: ...
    def _learn_from_turn(self, prompt, response) -> None: ...
    def _save_checkpoint(self, *a, **k) -> None: ...
    def _clear_checkpoint(self) -> None: ...

    def _finalize_turn(self, prompt, content, used_tools, ti, to, cfg, tc, ctx_input_tokens=None):
        return content

    def _append_to_session_history(self, *a, **k) -> None: ...


def _collect(engine: Any, prompt: str = "hi") -> list[dict]:
    return list(run_stream_call_model_loop(engine, prompt))


# ── A: 流式续写 ──────────────────────────────────────────────────────


def test_stream_truncation_auto_continues_and_joins() -> None:
    """length 截断 → 自动续写一次 → 终轮聚合前后两段，无打标。"""
    provider = _ScriptedStreamProvider([
        _mk_resp("第一段", finish_reason="length"),
        _mk_resp("第二段", finish_reason="stop"),
    ])
    engine = _Engine(provider)
    events = _collect(engine)

    assert any(e["type"] == "status" and "续写 1/3" in e["message"] for e in events)
    dones = [e for e in events if e["type"] == "done"]
    assert len(dones) == 1
    assert "第一段" in dones[0]["content"] and "第二段" in dones[0]["content"]
    assert "⚠️" not in dones[0]["content"]
    # 续写请求带上了 continue 提示
    second_call = provider.seen_messages[1]
    assert _CONTINUATION_HINT in str(second_call[-1].content)


def test_stream_truncation_guard_rail_then_tag() -> None:
    """连续截断 3 次 → 第 4 次不再续写，聚合全文 + ⚠️ 打标落稿。"""
    provider = _ScriptedStreamProvider([
        _mk_resp(f"段{i}", finish_reason="length") for i in range(4)
    ] + [_mk_resp("终", finish_reason="stop")])
    engine = _Engine(provider)
    events = _collect(engine)

    conts = [e for e in events if e["type"] == "status" and "续写" in e.get("message", "")]
    assert len(conts) == _MAX_CONTINUATIONS
    done = [e for e in events if e["type"] == "done"][-1]
    for i in range(4):
        assert f"段{i}" in done["content"]
    assert "⚠️[输出不完整" in done["content"]
    # 打标后即终稿，第 5 轮不应发生
    assert len(provider.seen_messages) == 4


def test_stream_abnormal_finish_reason_tagged() -> None:
    """未知 reason（content_filter）→ 打标透传，不静默。"""
    provider = _ScriptedStreamProvider([
        _mk_resp("内容", finish_reason="content_filter"),
    ])
    engine = _Engine(provider)
    events = _collect(engine)

    done = [e for e in events if e["type"] == "done"][-1]
    assert "内容" in done["content"]
    assert _INCOMPLETE_TAG.strip() in done["content"]


def test_stream_stop_reason_no_interference() -> None:
    """正常 stop / tool_calls reason → 零扰动（不打标不续写）。"""
    provider = _ScriptedStreamProvider([_mk_resp("正常", finish_reason="stop")])
    engine = _Engine(provider)
    events = _collect(engine)
    done = [e for e in events if e["type"] == "done"][-1]
    assert done["content"] == "正常"


# ── A: 非流式对称 ────────────────────────────────────────────────────


def test_nostream_truncation_continues_and_joins() -> None:
    from lingclaude.engine.loop.loop_body import run_call_model_loop

    provider = _ScriptedStreamProvider([
        _mk_resp("前半", finish_reason="length"),
        _mk_resp("后半", finish_reason="stop"),
    ])
    engine = _Engine(provider)
    content = run_call_model_loop(engine, "hi")
    assert "前半" in content and "后半" in content
    assert "⚠️" not in content


def test_nostream_truncation_guard_rail_tag() -> None:
    from lingclaude.engine.loop.loop_body import run_call_model_loop

    provider = _ScriptedStreamProvider([
        _mk_resp(f"N{i}", finish_reason="length") for i in range(4)
    ])
    engine = _Engine(provider)
    content = run_call_model_loop(engine, "hi")
    for i in range(4):
        assert f"N{i}" in content
    assert "⚠️[输出不完整" in content


# ── B: 瘦身分型 ──────────────────────────────────────────────────────


def test_diagnostic_tools_keep_more_prefix() -> None:
    body = "行" * 5000  # 5000 字符 > 3200 门槛
    slim_bash = _slim_tool_output("bash", body, task_hint="")
    slim_fetch = _slim_tool_output("web_fetch", body, task_hint="")

    assert "行" * _TOOL_RESULT_SLIM_KEEP_DIAG in slim_bash
    assert "行" * _TOOL_RESULT_SLIM_KEEP in slim_fetch
    assert "分段读取" in slim_bash  # 指引升级
    assert len(slim_bash) > len(slim_fetch)


def test_small_diagnostic_output_untouched() -> None:
    out = "短输出"
    assert _slim_tool_output("bash", out, task_hint="") == out
    assert _slim_tool_output("web_fetch", out, task_hint="") == out


def test_diagnostic_tool_set_content() -> None:
    assert _DIAGNOSTIC_TOOLS == frozenset({"bash", "read", "grep", "glob"})


# ── C: spill read_hint ───────────────────────────────────────────────


def test_spill_stub_has_read_hint(tmp_path, monkeypatch) -> None:
    from lingclaude.engine.tool_pipeline import ToolPipeline

    class _Registry:
        def get_all_definitions(self):
            return ()

    pipeline = ToolPipeline(_Registry())
    monkeypatch.setattr(ToolPipeline, "_SPILL_DIR", str(tmp_path))
    big = "x" * (ToolPipeline._OUTPUT_TOKEN_LIMIT * 4 + 10)

    stub = pipeline._prune_output("bash", big)
    assert stub.get("_spilled") is True
    hint = stub.get("read_hint", "")
    assert stub["locator"] in hint
    assert "head -c" in hint and "grep -n" in hint
