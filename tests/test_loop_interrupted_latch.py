"""熔断/循环中断红点（latch 型状态栏标记）单测。

链路契约（2026-10-01）：
  engine 检测层置位（_mark_loop_interrupted 双写 latch）
    → 快照每秒轮询 engine._loop_interrupted 喂入 status（只置不清）
    → toolbar_fragments 渲染 🔴中断 红点段
    → 用户提交新输入时 reset_loop_interrupted 双 latch 清除。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lingclaude.cli.status import StatusModel, toolbar_fragments
from lingclaude.cli.repl import reset_loop_interrupted


def _frag_text(frags):
    return "".join(t for _, t in frags)


class TestLoopInterruptedLatch(unittest.TestCase):
    def test_status_latch_set_and_clear(self):
        s = StatusModel()
        self.assertFalse(s.loop_interrupted)
        s.set_loop_interrupted()
        self.assertTrue(s.loop_interrupted)
        s.clear_loop_interrupted()
        self.assertFalse(s.loop_interrupted)

    def test_snapshot_passthrough(self):
        s = StatusModel()
        s.set_loop_interrupted()
        self.assertTrue(s.snapshot().loop_interrupted)
        s.clear_loop_interrupted()
        self.assertFalse(s.snapshot().loop_interrupted)

    def test_render_red_dot_segment(self):
        s = StatusModel()
        s.set_loop_interrupted()
        text = _frag_text(toolbar_fragments(s))
        self.assertIn("🔴中断", text)

    def test_render_absent_when_clear(self):
        s = StatusModel()
        self.assertNotIn("🔴中断", _frag_text(toolbar_fragments(s)))

    def test_render_tolerates_dirty_snapshot(self):
        # 渲染防腐契约：旧快照对象缺 loop_interrupted 字段不炸渲染。
        s = StatusModel()
        snap = s.snapshot()
        del snap.__dict__["loop_interrupted"]
        text = _frag_text(toolbar_fragments(snap))
        self.assertNotIn("🔴中断", text)

    def test_mark_sets_engine_latch_without_status(self):
        class _E:
            pass

        e = _E()
        _E._mark_loop_interrupted = (
            __import__("lingclaude.core.model_call", fromlist=["ModelCallMixin"])
            .ModelCallMixin._mark_loop_interrupted
        )
        e._mark_loop_interrupted()
        self.assertTrue(e._loop_interrupted)
        self.assertIsNone(getattr(e, "_status", None))

    def test_mark_pushes_status_too(self):
        from lingclaude.core.model_call import ModelCallMixin

        class _E:
            pass

        e = _E()
        e._status = StatusModel()
        e._mark_loop_interrupted = ModelCallMixin._mark_loop_interrupted.__get__(e)
        e._mark_loop_interrupted()
        self.assertTrue(e._loop_interrupted)
        self.assertTrue(e._status.loop_interrupted)

    def test_mark_swallows_status_failure(self):
        from lingclaude.core.model_call import ModelCallMixin

        class _Bad:
            def set_loop_interrupted(self):
                raise RuntimeError("boom")

        class _E:
            pass

        e = _E()
        e._status = _Bad()
        e._mark_loop_interrupted = ModelCallMixin._mark_loop_interrupted.__get__(e)
        e._mark_loop_interrupted()  # 不抛 = 契约成立
        self.assertTrue(e._loop_interrupted)

    def test_reset_clears_both_latches(self):
        from types import SimpleNamespace

        s = StatusModel()
        s.set_loop_interrupted()
        e = SimpleNamespace(_loop_interrupted=True)
        reset_loop_interrupted(e, s)
        self.assertFalse(e._loop_interrupted)
        self.assertFalse(s.loop_interrupted)

    def test_reset_tolerates_missing_attrs(self):
        from types import SimpleNamespace

        e = SimpleNamespace()  # 无 _loop_interrupted
        s = StatusModel()
        reset_loop_interrupted(e, s)  # 不抛 = 契约成立
        self.assertFalse(s.loop_interrupted)


if __name__ == "__main__":
    unittest.main()
