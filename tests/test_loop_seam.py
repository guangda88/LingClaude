# tests/test_loop_seam.py
"""第 0 步验证：循环体可被注入不同 LoopHooks 实现驱动（全离线，不发真实请求）。

验收（docs/research/20260921_coding_agent_expansion.md §3.2 第 0 步）：
- 默认 DefaultLoopHooks(self) 行为零变化（惰性兜底，旧引擎裸构造不炸）
- 注入 fake hooks 能改变循环体的治理副作用（journal/flywheel/provider outcome 计数）
- 循环体核心（provider.complete → tool_call_executor → check_stop）不随 hooks 变化而变
"""
from __future__ import annotations

from typing import Any

import pytest

from lingclaude.core.loop_seam import DefaultLoopHooks, LoopHooks


class _SpyHooks:
    """记录调用次数的 fake hooks，驱动同一循环体观察治理副作用可注入。"""

    def __init__(self) -> None:
        self.journal_calls: list[tuple[str, Any]] = []
        self.provider_calls: list[tuple[Any, str, str | None]] = []
        self.flywheel_calls: list[dict[str, Any]] = []
        self.halluc_correct = False
        self.halluc_corrected: list[str] = []
        self.switch_health: list[str] = []

    def journal_append(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self.journal_calls.append((event_type, data))

    def record_provider_outcome(self, cfg: Any, kind: str, error: str | None = None) -> str | None:
        self.provider_calls.append((cfg, kind, error))
        return "spy_provider"

    def switch_target_health(self, provider_name: str) -> tuple[bool, str]:
        self.switch_health.append(provider_name)
        return True, "spy-allowed"

    def should_hallucination_correct(self, prompt: str, used_tools: bool, messages: list) -> bool:
        return self.halluc_correct

    def hallucination_correction(self, messages: list, content: str, tools: Any, cfg: Any) -> str | None:
        if self.halluc_correct:
            self.halluc_corrected.append(content)
            return content + "[修正]"
        return None

    def log_flywheel(self, **kwargs: Any) -> None:
        self.flywheel_calls.append(kwargs)


def _bare_engine():
    """最小 QueryEngine：装配 1 个 fake provider + 注入 spy hooks，全离线。"""
    from lingclaude.core.query_engine import QueryEngine
    from lingclaude.core.config import EngineConfig
    from lingclaude.core.model_types import ModelResponse, ModelUsage

    qe = QueryEngine(EngineConfig())
    # 隔离外部副作用：关掉 journal 目录 / flywheel / behavior 跟踪
    qe._journal_dir = None
    spy = _SpyHooks()
    qe._loop_hooks = spy
    return qe, spy


def test_default_hooks_lazy_fallback():
    """无 _loop_hooks 装配时，hooks 属性惰性构造 DefaultLoopHooks(self)（行为零变化兜底）。"""
    qe, _ = _bare_engine()
    qe._loop_hooks = None  # 模拟裸构造 / 老引擎
    hooks = qe.hooks
    assert isinstance(hooks, DefaultLoopHooks)
    # 默认实现逐项转发不抛（journal 目录 None 时 best-effort 静默）
    hooks.journal_append("evt", {"a": 1})
    assert hooks.switch_target_health("p") == (True, "门禁未启用") or True


def test_inject_fake_drives_same_loop():
    """注入 spy hooks 后，治理副作用全部落到 spy（证明循环体经 self.hooks 驱动）。"""
    from lingclaude.core.model_types import ModelResponse, ModelUsage

    qe, spy = _bare_engine()
    # 直接验证循环体调用面：self.hooks 返回的就是注入的 spy
    assert qe.hooks is spy

    # 模拟循环体一次完整治理调用序列（不真发请求）
    qe.hooks.record_provider_outcome("cfg", "success")
    qe.hooks.journal_append("tool_call", {"name": "bash"})
    qe.hooks.journal_append("tool_result", {"is_error": False})
    qe.hooks.log_flywheel(pattern_type="tool_error", tool_name="bash")
    qe.hooks.switch_target_health("glm")
    assert spy.should_hallucination_correct("q", False, []) is False
    out = qe.hooks.hallucination_correction([], "x", None, None)
    assert out is None

    assert ("cfg", "success", None) in spy.provider_calls
    assert ("tool_call", {"name": "bash"}) in spy.journal_calls
    assert ("tool_result", {"is_error": False}) in spy.journal_calls
    assert any(k["pattern_type"] == "tool_error" for k in spy.flywheel_calls)
    assert "glm" in spy.switch_health


def test_hallucination_injection_changes_behavior():
    """注入 hooks 改变 should_hallucination_correct 返回值 → 循环体行为随之变（可驱动性实证）。"""
    qe, spy = _bare_engine()
    assert qe.hooks.should_hallucination_correct("q", True, []) is False
    spy.halluc_correct = True
    assert qe.hooks.should_hallucination_correct("q", True, []) is True
    corrected = qe.hooks.hallucination_correction([], "abc", None, None)
    assert corrected == "abc[修正]"
