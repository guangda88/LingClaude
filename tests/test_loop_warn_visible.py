"""第一次打转的可见化提醒回归测试（2026-09-25）。

背景：打转检测本有两级设计（第 1 次 warn 注入纠偏提示、第 2 次 abort 熔断），
但 warn 只进模型上下文、不外显给用户——用户毫无感知直到熔断。
本次改动：
① tool_loop_detector._LOOP_WARN_HINT 文案强化（换方式/拆小任务/直接作答 ①②③）；
② stream 路径 warn 时同步 yield text_delta 外显（loop_body.py）。

本测试覆盖流式路径：两次完全相同调用 → warn 外显 + 上下文注入；
第三次换参数 → 正常推进、不熔断、最终回答完整。
"""
from __future__ import annotations

from lingclaude.core.query_engine import QueryEngine
from lingclaude.engine.loop.tool_loop_detector import _LOOP_WARN_HINT
from lingclaude.model.types import (
    ModelProvider,
    ModelUsage,
)


class _LoopWarnProvider(ModelProvider):
    """第 1、2 轮产出完全相同的工具调用（触发 warn），第 3 轮换参数（推进），第 4 轮收尾。"""

    def __init__(self) -> None:
        self.calls = 0
        # 每轮 stream_complete 收到的全部消息文本快照（验证 warn 注入上下文）
        self.snapshots: list[str] = []

    def complete(self, messages, config=None, tools=None):
        from lingclaude.core.types import Result
        return Result.ok(ModelResponse(
            content="回答", model="test", usage=ModelUsage(),
        ))

    async def acomplete(self, messages, config=None, tools=None):
        from lingclaude.core.types import Result
        return Result.ok(ModelResponse(content="async", model="test", usage=ModelUsage()))

    def count_tokens(self, text: str) -> int:
        return len(text) // 4

    def stream_complete(self, messages, config=None, tools=None):
        self.calls += 1
        self.snapshots.append("".join(str(getattr(m, "content", "")) for m in messages))
        yield {"type": "text_delta", "text": ""}
        if self.calls in (1, 2):
            # 两轮完全相同的调用（name+arguments 一致）→ 第 2 轮 observe_round 返回 warn
            yield {
                "type": "tool_call_complete",
                "id": f"call_{self.calls}",
                "name": "read",
                "arguments": '{"path": "a.py"}',
            }
            yield {"type": "finish", "reason": "tool_calls", "usage": ModelUsage()}
        elif self.calls == 3:
            # 换参数 = 新调用 → 正常推进
            yield {
                "type": "tool_call_complete",
                "id": "call_3",
                "name": "read",
                "arguments": '{"path": "b.py"}',
            }
            yield {"type": "finish", "reason": "tool_calls", "usage": ModelUsage()}
        else:
            yield {"type": "text_delta", "text": "最终回答"}
            yield {"type": "finish", "reason": "stop", "usage": ModelUsage()}


def _make_engine() -> QueryEngine:
    engine = QueryEngine()
    engine._provider = _LoopWarnProvider()
    engine._execute_tool_with_retry = lambda name, args: "mock"  # type: ignore[method-assign]
    return engine


class TestLoopWarnVisible:
    def test_first_loop_warn_visible_to_user(self) -> None:
        """第 2 轮打转（首次 warn）必须通过 text_delta 外显给用户。"""
        engine = _make_engine()
        events = list(engine.stream_call_model("请读文件"))
        deltas = [e.get("text", "") for e in events if e.get("type") == "text_delta"]
        joined = "".join(deltas)
        assert "首次原地打转" in joined
        assert "熔断" in joined

    def test_warn_hint_injected_into_context(self) -> None:
        """warn 提示仍须注入模型上下文（原有行为，防回归）。用 provider 快照验证。"""
        engine = _make_engine()
        list(engine.stream_call_model("请读文件"))
        provider = engine._provider
        all_content = "\n".join(provider.snapshots)
        assert "原地打转" in all_content
        # 强化后的文案锚点（换方式/拆小任务 ①②③ 指引）
        assert "改变策略" in all_content

    def test_no_abort_when_progressing(self) -> None:
        """第 3 轮换参数推进 → 不触发熔断，最终回答完整。"""
        engine = _make_engine()
        events = list(engine.stream_call_model("请读文件"))
        joined = "".join(
            e.get("text", "") for e in events if e.get("type") == "text_delta"
        )
        assert "最终回答" in joined
        assert "已熔断停止" not in joined
        assert "循环检测] 连续两轮" not in joined
