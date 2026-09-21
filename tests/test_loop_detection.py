"""打转检测器 — 区分"原地打转"与"正常推进"（替代纯轮次上限的收敛判定）。"""
from __future__ import annotations

from lingclaude.engine.loop.tool_loop_detector import _ToolLoopDetector


def _read(path: str) -> tuple[str, str]:
    return ("read", "p=" + path)


def _grep(pattern: str) -> tuple[str, str]:
    return ("grep", "pt=" + pattern)


class TestToolLoopDetector:
    def test_first_round_never_aborts(self) -> None:
        d = _ToolLoopDetector()
        assert d.observe_round([_read("a")]) is None

    def test_repeat_round_warns_once(self) -> None:
        d = _ToolLoopDetector()
        d.observe_round([_read("a")])
        assert d.observe_round([_read("a")]) == "warn"

    def test_double_repeat_aborts(self) -> None:
        d = _ToolLoopDetector()
        calls = [_read("a")]
        d.observe_round(calls)
        d.observe_round(calls)  # warn
        assert d.observe_round(calls) == "abort"

    def test_new_calls_reset_streak(self) -> None:
        d = _ToolLoopDetector()
        d.observe_round([_read("a")])
        assert d.observe_round([_read("a")]) == "warn"
        assert d.observe_round([_grep("x")]) is None  # 新产出 → 推进
        assert d.observe_round([_read("a")]) == "warn"  # 重新从 warn 开始

    def test_partial_new_calls_count_as_progress(self) -> None:
        d = _ToolLoopDetector()
        d.observe_round([_read("a"), _grep("x")])
        # 一半新一半旧 → 仍有新产出 → 正常推进
        assert d.observe_round([_read("a"), _grep("y")]) is None

    def test_empty_round_ignored(self) -> None:
        d = _ToolLoopDetector()
        assert d.observe_round([]) is None

    def test_same_tool_different_args_is_progress(self) -> None:
        d = _ToolLoopDetector()
        assert d.observe_round([_read("a")]) is None
        assert d.observe_round([_read("b")]) is None  # 参数不同 = 新调用
