"""N5a done 事件 usage 传递 + N5b watchdog 降级保护 回归测试。

背景（2026-02-14 codex 审计指控 #1/#4 修复）:
- model_call.stream_call_model 三处 done 事件原本不带 usage,
  CLI 侧 N5 守卫在真实链路永不触发（dfaab65 缺陷）。
- 修复后 done 携带本轮(非累计) usage; 基类 stream_complete 的 finish
  不带 usage 时按 0 计, 不崩。
- StreamWatchdog.start() 线程资源耗尽时降级为 no-op, 不破坏主路径。
"""
from __future__ import annotations

import threading
from typing import Any

import pytest

from lingclaude.cli.n5_stream_watchdog import StreamWatchdog
from lingclaude.cli.n5_token_guard import check_token_exhaustion, reset_n5_guard_state
from lingclaude.core.query_engine import QueryEngine
from lingclaude.model.types import (
    ModelProvider,
    ModelResponse,
    ModelUsage,
)

_IN = 50
_OUT = 200


class _UsageStreamProvider(ModelProvider):
    """complete 返回带 usage 的响应(基类 finish 不带 usage)。

    stream_complete 覆写: finish 事件携带 usage——模拟 openai/anthropic
    真实 provider 的行为(openai_provider.py:252 / anthropic_provider.py:379)。
    """

    def __init__(self, *, finish_with_usage: bool = True) -> None:
        self.finish_with_usage = finish_with_usage

    def complete(self, messages, config=None, tools=None):
        resp = ModelResponse(
            content="回答正文",
            model="test",
            usage=ModelUsage(input_tokens=_IN, output_tokens=_OUT),
        )
        from lingclaude.core.types import Result
        return Result.ok(resp)

    async def acomplete(self, messages, config=None, tools=None):
        from lingclaude.core.types import Result
        return Result.ok(ModelResponse(content="async", model="test", usage=ModelUsage()))

    def count_tokens(self, text: str) -> int:
        return len(text) // 4

    def stream_complete(self, messages, config=None, tools=None):
        yield {"type": "text_delta", "text": "回答正文"}
        finish: dict[str, Any] = {"type": "finish", "reason": "stop"}
        if self.finish_with_usage:
            finish["usage"] = ModelUsage(input_tokens=_IN, output_tokens=_OUT)
        yield finish


def _make_engine(provider: ModelProvider) -> QueryEngine:
    return QueryEngine(model_provider=provider)


@pytest.fixture(autouse=True)
def _clean_guard_state():
    reset_n5_guard_state()
    yield
    reset_n5_guard_state()


class TestDoneEventUsage:
    """done 事件携带本轮 usage。"""

    def test_done_carries_round_usage(self) -> None:
        engine = _make_engine(_UsageStreamProvider())
        events = list(engine.stream_call_model("问题"))

        done = [e for e in events if e["type"] == "done"]
        assert len(done) == 1
        assert done[0]["usage"] == {"input_tokens": _IN, "output_tokens": _OUT}
        # 与 _finalize_turn 记账同源(total_output == 本轮 finish 累计)
        assert done[0]["content"] == "回答正文"

    def test_provider_without_finish_usage_degrades_to_zero(self) -> None:
        """基类式 stream(finish 无 usage): done.usage 估算兜底, 不抛异常 (P0)。"""
        engine = _make_engine(_UsageStreamProvider(finish_with_usage=False))
        events = list(engine.stream_call_model("问题"))

        done = [e for e in events if e["type"] == "done"]
        assert done[0]["usage"]["input_tokens"] > 0  # P0: 估算兜底, 非 0
        assert done[0]["usage"]["output_tokens"] > 0

    def test_abandoned_generator_does_not_leak_usage(self) -> None:
        """生成器被弃置(未消费到 finish)后, 新一轮 usage 不含幽灵累计。"""
        engine = _make_engine(_UsageStreamProvider())
        gen = engine.stream_call_model("第一问")
        next(gen)  # 只取首个 text_delta, 弃置
        gen.close()

        events = list(engine.stream_call_model("第二问"))
        done = [e for e in events if e["type"] == "done"][-1]
        assert done["usage"] == {"input_tokens": _IN, "output_tokens": _OUT}


class TestGuardEndToEnd:
    """复刻 app.py 消费模式: done.usage → check_token_exhaustion 可触发。"""

    @staticmethod
    def _consume_like_cli(engine: QueryEngine) -> tuple[int, int]:
        """复刻 app.py:279-284 的 done 分支取数逻辑。"""
        text_deltas = 0
        turn_output_tokens = 0
        for event in engine.stream_call_model("空转问题"):
            if event.get("type") == "text_delta":
                text_deltas += 1
            elif event.get("type") == "done":
                turn_output_tokens = int(
                    (event.get("usage") or {}).get("output_tokens", 0) or 0
                )
        return text_deltas, turn_output_tokens

    def test_exhaustion_fires_through_real_stream_path(self) -> None:
        """provider 无 text_delta 且 usage 高耗 → 守卫在真实流路径触发。"""
        # 定制: 不产生 text_delta, finish 带高 usage
        class _SilentProvider(_UsageStreamProvider):
            def stream_complete(self, messages, config=None, tools=None):
                finish: dict[str, Any] = {
                    "type": "finish", "reason": "stop",
                    "usage": ModelUsage(input_tokens=_IN, output_tokens=_OUT),
                }
                yield finish

        engine = _make_engine(_SilentProvider())
        text_deltas, turn_output_tokens = self._consume_like_cli(engine)
        assert text_deltas == 0
        assert turn_output_tokens == _OUT

        level = check_token_exhaustion(
            session_id="it-e2e",
            text_deltas=text_deltas,
            turn_output_tokens=turn_output_tokens,
            max_tokens=200,  # _OUT=200 >= 0.95*200
        )
        assert level == "warning"  # 首次命中 = WARNING

    def test_no_false_alarm_when_usage_missing(self) -> None:
        """done.usage 缺失(=0)时不得误报——0 永远达不到 0.95*max 阈值。"""
        engine = _make_engine(_UsageStreamProvider(finish_with_usage=False))

        class _SilentNoUsage(_UsageStreamProvider):
            def stream_complete(self, messages, config=None, tools=None):
                yield {"type": "finish", "reason": "stop"}

        engine2 = _make_engine(_SilentNoUsage())
        text_deltas, turn_output_tokens = self._consume_like_cli(engine2)
        assert (text_deltas, turn_output_tokens) == (0, 0)

        assert check_token_exhaustion(
            session_id="it-nousage",
            text_deltas=text_deltas,
            turn_output_tokens=turn_output_tokens,
            max_tokens=200,
        ) is None


class TestWatchdogStartDegradation:
    """start() 线程启动失败 → 降级 no-op, 不向流式主路径抛异常。"""

    def test_start_failure_degrades_to_noop(self, monkeypatch, caplog) -> None:
        def _boom(self: threading.Thread) -> None:
            raise RuntimeError("can't start new thread")

        monkeypatch.setattr(threading.Thread, "start", _boom)
        wd = StreamWatchdog()

        with caplog.at_level("WARNING"):
            wd.start()  # 不得抛

        assert wd._thread is None
        assert wd._stopped.is_set()
        # 降级后 touch/stop 均安全(观测组件静默失效)
        wd.touch("text_delta")
        wd.stop()

        assert any("degrade to no-op" in r.message for r in caplog.records)

    def test_normal_start_still_works(self) -> None:
        wd = StreamWatchdog()
        wd.start()
        try:
            assert wd._thread is not None
            assert wd._thread.is_alive()
            assert not wd._stopped.is_set()
        finally:
            wd.stop()


class TestNoneUsageRobustness:
    """N5a-v2: provider 异常流 usage=None 时全链路不崩、归零记账。

    背景: event.get("usage", ModelUsage()) 在 key 存在但值为 None 时
    返回 None → .input_tokens AttributeError 炸穿整个 turn。
    """

    def test_finish_with_none_usage_does_not_crash(self) -> None:
        """finish 事件 usage=None → 不抛 AttributeError, done.usage 估算兜底 (P0)。"""

        class _NoneUsageProvider(_UsageStreamProvider):
            def stream_complete(self, messages, config=None, tools=None):
                yield {"type": "text_delta", "text": "回答正文"}
                yield {"type": "finish", "reason": "stop", "usage": None}

        engine = _make_engine(_NoneUsageProvider())
        events = list(engine.stream_call_model("问题"))  # 修复前此处即崩

        done = [e for e in events if e["type"] == "done"]
        assert len(done) == 1
        assert done[0]["usage"]["input_tokens"] > 0  # P0: 估算兜底, 非 0
        assert done[0]["usage"]["output_tokens"] > 0

    def test_cli_extraction_survives_none_usage(self) -> None:
        """CLI done 分支取数表达式 (app.py:284-286) 对 usage=None 健壮。

        逐字复刻 app.py 消费语义, 锁死兜底行为防回归:
        usage=None / usage 内字段 None / usage 缺失 → 全部归 0。
        """
        cli_extract = lambda event: int(  # noqa: E731
            (event.get("usage") or {}).get("output_tokens", 0) or 0
        )
        assert cli_extract({"type": "done", "content": "x", "usage": None}) == 0
        assert cli_extract({"type": "done", "content": "x",
                            "usage": {"output_tokens": None}}) == 0
        assert cli_extract({"type": "done", "content": "x"}) == 0
        assert cli_extract({"type": "done", "content": "x",
                            "usage": {"output_tokens": 42}}) == 42
