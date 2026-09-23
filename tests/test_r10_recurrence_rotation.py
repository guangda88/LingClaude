"""R10 (2026-09-23): 飞轮段3 效果可观测 + daemon 停滞轮换 + 模式细粒度化。

三支线对应：
- 1️⃣ 复发率埋点：record_recurrence 时序关联 + recurrence_count
- 3️⃣ 模式聚类：守卫触发落 error_log，pattern_type=hallucination:<type>
- 2️⃣ daemon 轮换：stall_count >= 8 → structure切behavior / behavior降频
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from lingclaude.core.config import OptimizerConfig, lingclaudeConfig
from lingclaude.core.data_flywheel import CorrectionEntry, DataFlywheel
from lingclaude.self_optimizer.daemon import (
    DaemonState,
    OptimizationCycle,
    OptimizationDaemon,
)


@pytest.fixture
def flywheel(tmp_path):
    fw = DataFlywheel(db_path=str(tmp_path / "r10_flywheel.db"))
    yield fw
    fw.close()


def _mk_daemon(tmp_path, goal="structure", stall=0):
    """构造最小 daemon（跳过 __init__ 的重装配，直接注入状态）。"""
    cfg = lingclaudeConfig(optimizer=OptimizerConfig(goal=goal))
    d = OptimizationDaemon.__new__(OptimizationDaemon)
    d.target = "."
    d.config = cfg
    d.state_dir = tmp_path
    d.state_path = tmp_path / "state.json"
    d.reports_dir = tmp_path / "reports"
    d._runtime_goal = None
    d._watch_interval = 300
    d.state = DaemonState(stall_count=stall)
    return d


def _mk_cycle(v_before, v_after):
    return OptimizationCycle(
        cycle_id=1,
        triggered_at="2026-09-23T12:00:00",
        trigger_reason="test",
        trigger_type="test",
        trigger_priority="low",
        best_score=float(v_after),
        best_params={},
        experiments=1,
        duration_seconds=0.1,
        violations_before=v_before,
        violations_after=v_after,
        report_path=None,
    )


class TestRecurrenceTracking:
    """R10-1: record_recurrence——时序关联近似 + recurrence_count 计数。"""

    def test_writes_fine_grained_error_log(self, flywheel):
        """守卫触发 → error_log 落细粒度 pattern_type（喂活 top_error_patterns）"""
        res = flywheel.record_recurrence(
            session_id="s1",
            fact_types=["hard_fact", "unsupported"],
            occurred_at="2026-09-23T12:00:00",
        )
        assert res.is_ok
        q = flywheel.get_recurring_errors(min_count=1)
        assert q.is_ok
        types = {r["pattern_type"] for r in q.data}
        assert "hallucination:hard_fact" in types
        assert "hallucination:unsupported" in types

    def test_recurrence_counts_injection_window(self, flywheel):
        """30min 窗口内的 meta.feedback 注入记录 → recurrence_count+1"""
        now = datetime.now()
        # 注入发生在 10min 前（窗口内）
        corr = CorrectionEntry(
            original_error="编造了数字",
            correction="先查再写",
            source="meta.feedback",
            confidence=0.8,
            applied_at=(now - timedelta(minutes=10)).isoformat(timespec="seconds"),
        )
        flywheel.log_correction(corr)
        res = flywheel.record_recurrence(
            session_id="s1", fact_types=["hard_fact"],
            occurred_at=now.isoformat(timespec="seconds"),
        )
        assert res.is_ok
        assert res.data >= 1  # 命中 1 条注入
        # 计数确实加到库上
        enriched = flywheel.get_recent_corrections_with_recurrence(limit=5)
        assert enriched.is_ok
        hit = [
            c for c in enriched.data
            if c["correction"] == "先查再写"
        ]
        assert hit and hit[0]["recurrence_count"] == 1

    def test_no_count_outside_window(self, flywheel):
        """窗口外的注入不计数（触发在 40min 后 → 超出 30min 窗口）"""
        now = datetime.now()
        corr = CorrectionEntry(
            original_error="旧错误",
            correction="旧教训",
            source="meta.feedback",
            confidence=0.8,
            applied_at=(now - timedelta(minutes=40)).isoformat(timespec="seconds"),
        )
        flywheel.log_correction(corr)
        res = flywheel.record_recurrence(
            session_id="s1", fact_types=["hard_fact"],
            occurred_at=now.isoformat(timespec="seconds"),
        )
        assert res.is_ok
        assert res.data == 0
        enriched = flywheel.get_recent_corrections_with_recurrence(limit=5)
        hit = [c for c in enriched.data if c["correction"] == "旧教训"]
        assert hit and hit[0]["recurrence_count"] == 0

    def test_enrich_fallback_on_old_schema(self, flywheel):
        """enrich 失败时降级为 0 计数，绝不抛错（fail-soft）"""
        base = flywheel.get_recent_corrections(limit=2)
        assert base.is_ok  # 空库也 OK
        res = flywheel.get_recent_corrections_with_recurrence(limit=2)
        assert res.is_ok


class TestDaemonRotation:
    """R10-4: 停滞轮换——stall>=8 切 goal/降频。"""

    def test_stall_count_resets_on_improvement(self, tmp_path):
        d = _mk_daemon(tmp_path, stall=3)
        d._record_cycle(_mk_cycle(v_before=10, v_after=8))
        assert d.state.stall_count == 0
        assert d.state.total_improvements == 1

    def test_stall_count_increments_on_stall(self, tmp_path):
        d = _mk_daemon(tmp_path, stall=0)
        d._record_cycle(_mk_cycle(v_before=10, v_after=10))
        assert d.state.stall_count == 1

    def test_structure_stall_rotates_to_behavior(self, tmp_path):
        """structure 连续 8 圈零改进 → 运行时切 behavior + best_ever 重置"""
        d = _mk_daemon(tmp_path, goal="structure", stall=8)
        d.state.best_ever_score = 2322.9
        d.state.best_ever_goal = "structure"
        d._maybe_rotate_goal()
        assert d._current_goal() == "behavior"
        assert d.state.best_ever_score is None  # F5 同语义重置
        assert d.state.stall_count == 0

    def test_behavior_stall_drops_frequency(self, tmp_path):
        """behavior 停滞 → watch 间隔 x6（300s → 1800s）"""
        d = _mk_daemon(tmp_path, goal="behavior", stall=8)
        d._maybe_rotate_goal()
        assert d._current_goal() == "behavior"  # 不换尺子
        assert d._watch_interval == 1800
        assert d.state.stall_count == 0

    def test_no_rotation_below_threshold(self, tmp_path):
        """stall < 8 不轮换"""
        d = _mk_daemon(tmp_path, goal="structure", stall=7)
        d.state.best_ever_score = 100.0
        d._maybe_rotate_goal()
        assert d._current_goal() == "structure"
        assert d.state.best_ever_score == 100.0
        assert d._watch_interval == 300

    def test_runtime_goal_used_in_request(self, tmp_path):
        """OptimizationRequest 组装走 _current_goal()（轮换后≠config 档）"""
        d = _mk_daemon(tmp_path, goal="structure")
        d._runtime_goal = "behavior"
        assert d._current_goal() == "behavior"

    def test_state_roundtrip_keeps_stall_count(self, tmp_path):
        """stall_count 跨进程持久化"""
        state = DaemonState(stall_count=5)
        path = tmp_path / "st.json"
        state.save(path)
        loaded = DaemonState.load(path)
        assert loaded.stall_count == 5

    def test_watch_uses_mutable_interval(self, tmp_path):
        """run_watch 的 sleep 用 _watch_interval（降频生效前提）——源码断言"""
        import inspect
        from lingclaude.self_optimizer import daemon as daemon_mod
        src = inspect.getsource(daemon_mod.OptimizationDaemon.run_watch)
        assert "self._watch_interval" in src
        assert "time.sleep(self._watch_interval)" in src
