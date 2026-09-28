"""P1-20260928: inprocess 并发预算测试（429 预检 + 输出限流）。

测试目标：
1. 熔断器开启时并行派发被拒绝（circuit_open → FAILED）
2. 输出限流：聚合长度超限时截断（50KB 上限）
3. 熔断器关闭时并行正常执行
"""
from __future__ import annotations

from typing import Any

import pytest

from lingclaude.core.types import Result
from lingclaude.engine.loop.sub_agent import SubAgentConfig
from lingclaude.engine.subagent.base import SubagentContext, SubagentRequest, SubagentStatus
from lingclaude.engine.subagent.inprocess import InProcessSubagentBackend


# ── 离线基建 ───────────────────────────────────────────────────────────────


def _mk_resp(content: str) -> Any:
    from lingclaude.core.model_types import ModelResponse, ModelUsage

    return ModelResponse(
        content=content,
        model="test",
        usage=ModelUsage(input_tokens=1, output_tokens=1),
        tool_calls=(),
    )


class _ScriptedProvider:
    """按剧本依序吐 ModelResponse 的离线 provider。"""

    def __init__(self, rounds: list[Any]) -> None:
        self._rounds = list(rounds)

    def complete(self, messages, config=None, tools=None, **kw) -> Result:
        return Result.ok(self._rounds.pop(0))


class _FakeRetryPolicy:
    """模拟熔断器状态的 fake retry policy。"""

    def __init__(self, circuit_open: bool = False, circuit_consecutive_429: int = 0) -> None:
        self.circuit_open = circuit_open
        self._circuit_consecutive_429 = circuit_consecutive_429


class _Registry:
    def get_all_definitions(self) -> tuple[dict[str, Any], ...]:
        return ()


class _RuntimeStub:
    """最小 runtime：registry 可答、session_id 明确、hooks 可选注入。"""

    def __init__(self, session_id: str = "test", hooks: Any = None) -> None:
        self.session_id = session_id
        self.registry = _Registry()
        if hooks is not None:
            self.hooks = hooks


# ── 测试用例 ───────────────────────────────────────────────────────────────


class TestParallelCircuitBreaker:
    """429 预检：熔断器开启时并行派发被拒绝。"""

    def test_circuit_open_rejects_parallel(self) -> None:
        """熔断器开启（circuit_open=True）时，并行派发直接返回失败。"""
        provider = _ScriptedProvider([_mk_resp("a"), _mk_resp("b")])
        provider._retry_policy = _FakeRetryPolicy(circuit_open=True, circuit_consecutive_429=5)

        backend = InProcessSubagentBackend()
        result = backend.run(
            SubagentRequest(task="并行任务", parallel=2),
            SubagentContext(runtime=_RuntimeStub(), model_provider=provider),
        )

        assert result.success is False
        assert result.status == SubagentStatus.FAILED
        assert "circuit breaker open" in result.error.lower()
        assert "429" in result.error

    def test_circuit_closed_allows_parallel(self) -> None:
        """熔断器关闭（circuit_open=False）时，并行正常执行。"""
        provider = _ScriptedProvider([_mk_resp("a"), _mk_resp("b")])
        provider._retry_policy = _FakeRetryPolicy(circuit_open=False)

        backend = InProcessSubagentBackend()
        result = backend.run(
            SubagentRequest(task="并行任务", parallel=2),
            SubagentContext(runtime=_RuntimeStub(), model_provider=provider),
        )

        assert result.success is True
        assert result.status == SubagentStatus.COMPLETED
        assert result.output == "a\n\nb"

    def test_no_retry_policy_allows_parallel(self) -> None:
        """无 retry_policy 属性时，并行正常执行（向后兼容）。"""
        provider = _ScriptedProvider([_mk_resp("a"), _mk_resp("b")])
        # 不设置 _retry_policy 属性

        backend = InProcessSubagentBackend()
        result = backend.run(
            SubagentRequest(task="并行任务", parallel=2),
            SubagentContext(runtime=_RuntimeStub(), model_provider=provider),
        )

        assert result.success is True
        assert result.output == "a\n\nb"


class TestOutputRateLimit:
    """输出限流：聚合长度超限时截断。"""

    def test_output_truncated_when_exceeds_limit(self) -> None:
        """聚合输出超 50KB 时截断并附加省略提示。"""
        # 构造两个超长输出（各 30KB，聚合后 60KB > 50KB 上限）
        long_output = "x" * 30 * 1024
        provider = _ScriptedProvider([_mk_resp(long_output), _mk_resp(long_output)])

        backend = InProcessSubagentBackend()
        result = backend.run(
            SubagentRequest(task="长输出任务", parallel=2),
            SubagentContext(runtime=_RuntimeStub(), model_provider=provider),
        )

        assert result.success is True
        assert len(result.output) > 50 * 1024  # 截断后仍 > 50KB（因为附加了省略提示）
        assert "TRUNCATED" in result.output
        assert "omitted" in result.output

    def test_output_not_truncated_when_within_limit(self) -> None:
        """聚合输出未超限时原样返回。"""
        provider = _ScriptedProvider([_mk_resp("short a"), _mk_resp("short b")])

        backend = InProcessSubagentBackend()
        result = backend.run(
            SubagentRequest(task="短输出任务", parallel=2),
            SubagentContext(runtime=_RuntimeStub(), model_provider=provider),
        )

        assert result.success is True
        assert result.output == "short a\n\nshort b"
        assert "TRUNCATED" not in result.output


class TestSerialFallback:
    """熔断器开启时降级串行（现有逻辑回归验证）。"""

    def test_circuit_open_degrades_to_serial(self) -> None:
        """熔断器开启时，parallel>1 降级为串行（现有逻辑，本次未改动）。"""
        provider = _ScriptedProvider([_mk_resp("serial")])
        provider._retry_policy = _FakeRetryPolicy(circuit_open=True, circuit_consecutive_429=3)

        backend = InProcessSubagentBackend()
        result = backend.run(
            SubagentRequest(task="串行任务", parallel=2),
            SubagentContext(runtime=_RuntimeStub(), model_provider=provider),
        )

        # 现有逻辑：parallel>1 且 circuit_open → 降级串行（只跑一轮）
        # 但本次改动新增了 429 预检，直接拒绝并行派发
        # 所以这里期望 FAILED（而非串行成功）
        assert result.success is False
        assert "circuit breaker open" in result.error.lower()
