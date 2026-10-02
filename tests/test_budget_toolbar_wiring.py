"""2026-10-02: 预算 WARN 段状态栏接线测试（P1② 展示层补全）。

覆盖链路：set_budget_warn（Model 层）→ snapshot 透传 →
_toolbar_snapshot 喂入块（真实 repl 源码，monkeypatch warn_lines 模拟
gate 各形态）→ toolbar_fragments 渲染段（黄字 + │ 段界 + 防腐兜空）。
此前 warn 只有 /budget 手动消费，常驻状态栏断接（本会话 1341 次调用
> warn 800 实证漏报）——本文件防回归。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lingclaude.cli.status import StatusModel, toolbar_fragments


# ------------------------------------------------------- Model 层契约

class TestBudgetWarnModel:
    def test_set_budget_warn_replaces_tuple(self) -> None:
        s = StatusModel()
        s.set_budget_warn(("[预算提示] tool_calls 900/800",))
        assert s.snapshot().budget_warn == ("[预算提示] tool_calls 900/800",)
        # 整体替换（warn_lines 每秒全量重算语义）
        s.set_budget_warn(())
        assert s.snapshot().budget_warn == ()

    def test_set_budget_warn_accepts_list(self) -> None:
        s = StatusModel()
        s.set_budget_warn(["a", "b"])  # list 入参 → tuple 落库
        assert s.snapshot().budget_warn == ("a", "b")

    def test_budget_warn_survives_snapshot(self) -> None:
        s = StatusModel()
        s.set_budget_warn(("x", "y"))
        snap = s.snapshot()
        assert snap.budget_warn == ("x", "y")
        # 浅拷贝隔离：改原对象不影响已取快照（元组不可变天然满足）
        s.set_budget_warn(())
        assert snap.budget_warn == ("x", "y")

    def test_default_empty(self) -> None:
        assert StatusModel().snapshot().budget_warn == ()


# --------------------------------------------------- 喂入块（真实 repl 源码）

def _feed_via_repl(monkeypatch, warn_ret=None, warn_raises=False):
    """走 _toolbar_snapshot 真实喂入块：mock warn_lines 产源，断言落进 Model。"""
    import lingclaude.cli.repl as repl_mod
    from lingclaude.cli import status as status_mod

    s = StatusModel()
    ctx = mock.Mock()
    ctx.status = s
    ctx.engine = None  # model/todo/plan 等源全部静默降级，不干扰
    ctx.session = mock.Mock()
    ctx.session.interrupt_event = lambda: None
    ctx.session._streaming = False
    ctx.input_queue = None

    target = "lingclaude.core.session_budget_gate.warn_lines"
    if warn_raises:
        monkeypatch.setattr(
            target, mock.Mock(side_effect=RuntimeError("gate boom"))
        )
    else:
        monkeypatch.setattr(target, mock.Mock(return_value=warn_ret or []))
    repl_mod._toolbar_snapshot(ctx)
    return s


class TestBudgetWarnFeed:
    def test_feed_warn_lines_via_repl_snapshot(self, monkeypatch) -> None:
        s = _feed_via_repl(
            monkeypatch, warn_ret=["[预算提示] tool_calls 900/800"]
        )
        assert s.snapshot().budget_warn == ("[预算提示] tool_calls 900/800",)
        assert "budget" not in s.snapshot().degraded

    def test_feed_empty_clears(self, monkeypatch) -> None:
        s = _feed_via_repl(monkeypatch, warn_ret=[])
        assert s.snapshot().budget_warn == ()
        assert "budget" not in s.snapshot().degraded

    def test_feed_crash_marks_degraded_and_clears_stale(self, monkeypatch) -> None:
        s = _feed_via_repl(monkeypatch, warn_ret=["stale"])  # 先有值
        assert s.snapshot().budget_warn == ("stale",)
        s2 = _feed_via_repl(monkeypatch, warn_raises=True)  # 新快照周期炸
        assert "budget" in s2.snapshot().degraded
        assert s2.snapshot().budget_warn == ()  # stale 清空，不留矛盾画面


# ------------------------------------------------------- 渲染层契约

class TestBudgetWarnRender:
    def test_warn_lines_render_yellow_segment(self) -> None:
        s = StatusModel()
        s.set_model("m1")
        s.cwd = "/tmp"
        s.set_budget_warn(("[预算提示] tool_calls 900/800",))
        frag = toolbar_fragments(s.snapshot())
        joined = "".join(txt for _, txt in frag)
        assert "[预算提示] tool_calls 900/800" in joined
        yellow = [st for st, txt in frag if "[预算提示]" in txt]
        assert yellow and all(st == "class:yellow" for st in yellow)

    def test_no_warn_no_segment(self) -> None:
        s = StatusModel()
        s.set_model("m1")
        s.cwd = "/tmp"
        frag = toolbar_fragments(s.snapshot())
        assert not any("[预算提示]" in txt for _, txt in frag)

    def test_max_two_dims_on_statusline(self) -> None:
        """3+ 维同爆只显示前 2（极端场景全量走 /budget）。"""
        s = StatusModel()
        s.set_model("m1")
        s.cwd = "/tmp"
        s.set_budget_warn(("d1", "d2", "d3", "d4"))
        joined = "".join(txt for _, txt in toolbar_fragments(s.snapshot()))
        assert "d1" in joined and "d2" in joined
        assert "d3" not in joined and "d4" not in joined

    def test_dirty_snapshot_missing_field_never_crashes(self) -> None:
        """旧快照/测试桩缺 budget_warn 字段 → getattr 兜空静默跳过（防腐铁律）。"""
        from lingclaude.cli.status import StatusModel as _SM

        bare = _SM.__new__(_SM)  # 绕 __init__，零字段脏对象
        frag = toolbar_fragments(bare)
        assert isinstance(frag, list)  # 不炸即过

    def test_wrap_keeps_warn_segment_intact(self) -> None:
        """窄终端折行按 │ 段界，WARN 段不从中间剪断。"""
        s = StatusModel()
        s.set_model("m1")
        s.cwd = "/tmp/very/long/path/that/forces/wrapping/behavior"
        s.set_budget_warn(("[预算提示] tool_calls 900/800",))
        frag = toolbar_fragments(s.snapshot())
        joined = "".join(txt for _, txt in frag)
        assert "[预算提示] tool_calls 900/800" in joined  # 全文完整（段不腰斩）
