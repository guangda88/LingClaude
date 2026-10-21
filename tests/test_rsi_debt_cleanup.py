"""技术债清理批次测试（2026-10-21）：F6 尺度漂移卫兵 + activity_report 消费方。

F6 背景：10-17 首航实证 best_ever=27.6（旧语料尺度）vs 当前 ~775 →
6297 轮假回滚空转+永久冻结。本卫兵在同 goal 尺子内检测语料漂移。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


# --------------------------------------------------------------------- #
# 工具：构造 daemon + 打桩触发器/优化器/回放评分
# --------------------------------------------------------------------- #
def _make_daemon(tmp_path: Path):
    from lingclaude.self_optimizer.daemon import OptimizationDaemon

    return OptimizationDaemon(target=".", state_dir=tmp_path)


def _trigger(daemon):
    from lingclaude.self_optimizer.trigger import TriggerInfo

    return patch.object(
        daemon.trigger,
        "check_all_conditions",
        return_value=(
            True,
            TriggerInfo(
                type="user", reason="test", priority="high",
                current_value=1, threshold=0, metrics={},
            ),
        ),
    )


def _opt_result(daemon, best_score: float):
    from lingclaude.self_optimizer.optimizer import OptimizationResult

    return patch.object(
        daemon.optimizer,
        "optimize",
        return_value=OptimizationResult(
            success=True,
            best_params={"tool_repeat_limit": 4},
            best_score=best_score,
            experiments=1,
            duration=0.1,
            error=None,
            history=(),
        ),
    )


def _replay(score: float):
    """打桩 ReplayObjective.evaluate（daemon 内部延迟导入，patch 类方法生效）。"""
    import lingclaude.self_optimizer.replay_objective as ro

    return patch.object(ro.ReplayObjective, "evaluate", return_value=score)


# --------------------------------------------------------------------- #
# F6 尺度漂移卫兵
# --------------------------------------------------------------------- #
class TestBaselineDriftGuard:
    def test_drift_streak_resets_old_scale_baseline(self, tmp_path: Path):
        """连占 best_ever 达 M 倍×N 轮 → 归档重置（27.6 vs 775 型死锁自动化解锁）。"""
        daemon = _make_daemon(tmp_path)
        daemon.state.best_ever_score = 27.6
        daemon.state.best_ever_params = {"tool_repeat_limit": 3}
        daemon.state.best_ever_cycle_id = 820
        daemon.state.best_ever_goal = "behavior"
        # 前置：本轮 best_score 也劣于 best_ever（漂移场景的实际形态）
        with _trigger(daemon), _opt_result(daemon, 780.0), _replay(775.0):
            for _ in range(3):
                r = daemon.run_cycle(user_triggered=True)
                assert r and r.success

        # 连续 3 轮命中 → 第 3 轮重置（当轮即放行进 apply 支路）
        assert daemon.state.best_ever_score is None
        assert daemon.state.best_ever_params == {}
        assert daemon.state.baseline_drift_streak == 0
        reasons = [
            r.get("reason")
            for r in daemon.experiments.list_recent(3)
        ]
        # 前 2 轮=streak 积累期，择优门照旧拒绝（p1_worse）；第 3 轮卫兵
        # 触发归档重置→放行进 apply（测试环境无 APPLY → no_effective_change），
        # 故 p1_worse 恰好 2 次而非 3 次——死锁在第 3 轮被打破。
        assert reasons.count("p1_worse_than_best_ever") == 2
        assert "no_effective_change" in reasons

    def test_healthy_baseline_not_reset(self, tmp_path: Path):
        """score_before 未达 M 倍 → 基线原样保留，择优门照常工作。"""
        daemon = _make_daemon(tmp_path)
        daemon.state.best_ever_score = 700.0
        daemon.state.best_ever_params = {"tool_repeat_limit": 3}
        daemon.state.best_ever_cycle_id = 100
        daemon.state.best_ever_goal = "behavior"
        with _trigger(daemon), _opt_result(daemon, 780.0), _replay(775.0):
            daemon.run_cycle(user_triggered=True)

        assert daemon.state.best_ever_score == 700.0
        assert daemon.state.baseline_drift_streak == 0

    def test_noise_does_not_accumulate(self, tmp_path: Path):
        """2 轮漂移后 1 轮正常 → 计数清零，基线存活（防噪声设计）。"""
        daemon = _make_daemon(tmp_path)
        daemon.state.best_ever_score = 27.6
        daemon.state.best_ever_params = {"tool_repeat_limit": 3}
        daemon.state.best_ever_cycle_id = 820
        daemon.state.best_ever_goal = "behavior"
        with _trigger(daemon), _opt_result(daemon, 780.0):
            with _replay(775.0):
                daemon.run_cycle(user_triggered=True)
                daemon.run_cycle(user_triggered=True)
            with _replay(30.0):  # 正常量级：未达 2×27.6
                daemon.run_cycle(user_triggered=True)

        assert daemon.state.baseline_drift_streak == 0
        assert daemon.state.best_ever_score == 27.6

    def test_streak_persisted_across_state_roundtrip(self, tmp_path: Path):
        """streak 字段经反射往返持久化（跨进程计数不丢）。"""
        from lingclaude.self_optimizer.daemon import DaemonState

        s = DaemonState()
        s.baseline_drift_streak = 2
        ok = s.save(tmp_path / "st.json")
        assert ok
        s2 = DaemonState.load(tmp_path / "st.json")
        assert s2.baseline_drift_streak == 2


# --------------------------------------------------------------------- #
# activity_report 消费方（activity × 台账联表）
# --------------------------------------------------------------------- #
class TestActivityReport:
    def test_join_with_ledger_and_window_filter(self, tmp_path: Path):
        from datetime import datetime, timedelta

        daemon = _make_daemon(tmp_path)
        now = datetime.now()
        old_ts = (now - timedelta(days=30)).isoformat(timespec="seconds")
        new_ts = now.isoformat(timespec="seconds")
        exp_keep = daemon.experiments.start(1, {}, 100.0)
        daemon.experiments.settle(exp_keep, "accepted", reason="applied")
        lines = [
            json.dumps({
                "ts": old_ts, "cycle_id": 1, "experiment_id": "exp_gone",
                "trigger_type": "structure", "goal": "behavior",
                "origin": "self", "strategy": "tpe", "best_score": 1.0,
                "v_before": 1, "v_after": 1, "report": "",
                "chain_broken": False,
            }),
            json.dumps({
                "ts": new_ts, "cycle_id": 2, "experiment_id": exp_keep,
                "trigger_type": "user", "goal": "behavior",
                "origin": "self", "strategy": "annealing", "best_score": 2.0,
                "v_before": 2, "v_after": 2, "report": "",
                "chain_broken": False,
            }),
        ]
        (daemon.state_dir / "activity.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        rep = daemon.activity_report(days=7.0)
        assert rep["total_lines"] == 2
        assert rep["window_cycles"] == 1  # 30 天前行被窗口滤除
        assert rep["verdicts"] == {"accepted": 1}  # exp_gone 不在窗口
        assert rep["strategies"] == {"annealing": 1}
        assert rep["recent"][-1]["verdict"] == "accepted"

    def test_missing_experiment_marks_no_experiment(self, tmp_path: Path):
        daemon = _make_daemon(tmp_path)
        (daemon.state_dir / "activity.jsonl").write_text(
            json.dumps({
                "ts": "2026-10-21T00:00:00", "cycle_id": 9,
                "experiment_id": None, "trigger_type": "user",
                "goal": "behavior", "origin": "self", "strategy": "grid",
                "best_score": 3.0, "v_before": 3, "v_after": 3,
                "report": "", "chain_broken": False,
            }) + "\n",
            encoding="utf-8",
        )
        rep = daemon.activity_report(days=7.0)
        assert rep["verdicts"] == {"no_experiment": 1}
        assert rep["cycle_span"] == [9, 9]

    def test_missing_file_returns_empty_shape(self, tmp_path: Path):
        daemon = _make_daemon(tmp_path)
        rep = daemon.activity_report(days=7.0)
        assert rep["total_lines"] == 0
        assert rep["window_cycles"] == 0
        assert rep["recent"] == []
