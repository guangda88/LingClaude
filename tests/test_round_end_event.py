"""P1-1（会话问题重构 2026-09-15）: round_end 事件验收测试。

背景：会话问题重构的 Q3 —— 挂起命令此前要等整个 turn 完成才被消费。
新增 round_end 事件（model_call.stream_call_model 每轮 tool 轮结束、
下轮开始前 yield），CLI 层在此消费挂起队列（斜杠命令立即执行、普通文本
插队）。

本测试聚焦引擎侧：round_end 事件的存在性、字段、时序（在 tool 轮之后、
done 之前）。CLI 侧消费由 test_cli_* 系列覆盖（round 级消费接线）。
"""
from __future__ import annotations

from typing import Any

from lingclaude.core.query_engine import QueryEngine
from lingclaude.model.types import (
    ModelProvider,
    ModelResponse,
    ModelUsage,
)


class _MultiRoundToolProvider(ModelProvider):
    """模拟多轮 tool call：第一轮产出工具调用，第二轮产出最终文本。"""

    def __init__(self) -> None:
        self.calls = 0

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
        if self.calls == 1:
            # 第一轮：工具调用（read 一个文件）
            yield {"type": "text_delta", "text": ""}
            yield {
                "type": "tool_call_complete",
                "id": "call_1",
                "name": "read",
                "arguments": '{"path": "a.py"}',
            }
            yield {"type": "finish", "reason": "tool_calls", "usage": ModelUsage()}
        else:
            # 第二轮：最终文本
            yield {"type": "text_delta", "text": "最终回答"}
            yield {"type": "finish", "reason": "stop", "usage": ModelUsage()}


def _make_engine_with_tools() -> QueryEngine:
    engine = QueryEngine()
    engine._provider = _MultiRoundToolProvider()
    # 提供一个 stub executor（不真执行文件读，返回假结果）
    # 注意: 工具轮 for tc in round_tool_calls 总会执行 _execute_tool_with_retry,
    # 与 _build_openai_tools() 返回值无关（round_tool_calls 由 provider 产出）。
    engine._execute_tool_with_retry = lambda name, args: "文件内容 mock"  # type: ignore[method-assign]
    return engine


def test_round_end_emitted_after_tool_round() -> None:
    """多轮 tool 场景：round_end 在每个 tool 轮后发出，带 round_idx/has_tool_calls。"""
    engine = _make_engine_with_tools()
    events = list(engine.stream_call_model("请读文件"))
    types = [e.get("type") for e in events]

    # round_end 事件必须存在
    assert "round_end" in types
    round_ends = [e for e in events if e.get("type") == "round_end"]
    assert len(round_ends) >= 1

    # 第一个 round_end：round_idx=0、has_tool_calls=True（该轮执行了工具）
    first = round_ends[0]
    assert first["round_idx"] == 0
    assert first["has_tool_calls"] is True
    assert first["error_count"] == 0

    # 时序：round_end 在 tool_call_end 之后、done 之前
    first_end_idx = types.index("round_end")
    assert "tool_call_end" in types[:first_end_idx]
    assert "done" in types[first_end_idx:]


def test_round_end_not_emitted_on_single_round_no_tools() -> None:
    """单轮无工具场景：不发出 round_end（设计边界）。

    stream_call_model 在「无 tool_call」时走提前 return（finalize 直接 done），
    不经 round 循环尾部 —— 无工具轮 = 单轮即结束，无需 round 级插队
    （挂起命令等 turn 完即可，_consume_queue 处理）。round_end 只在
    工具轮（has_tool_calls=True 的多轮 tool 任务）之间发出，这正是
    「一个 round 完成后即可读挂起命令」的语义锚点。
    """
    class _NoToolProvider(ModelProvider):
        def complete(self, messages, config=None, tools=None):
            from lingclaude.core.types import Result
            return Result.ok(ModelResponse(content="x", model="t", usage=ModelUsage()))

        async def acomplete(self, messages, config=None, tools=None):
            from lingclaude.core.types import Result
            return Result.ok(ModelResponse(content="x", model="t", usage=ModelUsage()))

        def count_tokens(self, text: str) -> int:
            return 1

        def stream_complete(self, messages, config=None, tools=None):
            yield {"type": "text_delta", "text": "你好"}
            yield {"type": "finish", "reason": "stop", "usage": ModelUsage()}

    engine = QueryEngine()
    engine._provider = _NoToolProvider()
    events = list(engine.stream_call_model("你好"))
    types = [e.get("type") for e in events]

    # 无工具轮：不发出 round_end（提前 return），done 直接收尾
    assert "round_end" not in types
    assert "done" in types
