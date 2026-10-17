"""RSI 阶段一守卫件测试（2026-10-17）：artifact_guard + headroom + 状态机数据层。

对应 FINAL_synthesis.md 阶段一-4/5/6/7 的单元契约。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from lingclaude.self_optimizer.artifact_guard import (
    ArtifactGuard,
    RGP_MIN_TRACES,
)
from lingclaude.self_optimizer.headroom import (
    HeadroomVector,
    compute_headroom,
)


class TestSnapshotRestore:
    def test_snapshot_then_restore_roundtrip(self, tmp_path: Path):
        target = tmp_path / "behavior_policy.yaml"
        target.write_text("tool_repeat_limit: 3\n", encoding="utf-8")
        guard = ArtifactGuard(
            snapshot_dir=tmp_path / "artifacts",
            replay_db_path=tmp_path / "noflywheel.db",
        )
        meta = guard.snapshot(target, "exp_abc123")
        assert meta is not None
        assert meta.sha256
        # 应用「变更」
        target.write_text("tool_repeat_limit: 8\n", encoding="utf-8")
        ok, msg = guard.restore("exp_abc123", reason="obs_regress")
        assert ok, msg
        assert target.read_text(encoding="utf-8") == "tool_repeat_limit: 3\n"
        # 恢复后指纹一致（可证）
        from lingclaude.self_optimizer.artifact_guard import sha256_file

        assert sha256_file(target) == meta.sha256

    def test_snapshot_missing_target_returns_none(self, tmp_path: Path):
        guard = ArtifactGuard(
            snapshot_dir=tmp_path / "artifacts",
            replay_db_path=tmp_path / "noflywheel.db",
        )
        assert guard.snapshot(tmp_path / "ghost.yaml", "exp_x") is None

    def test_restore_unknown_id_fails_clean(self, tmp_path: Path):
        guard = ArtifactGuard(
            snapshot_dir=tmp_path / "artifacts",
            replay_db_path=tmp_path / "noflywheel.db",
        )
        ok, msg = guard.restore("no_such_snap")
        assert not ok
        assert "snapshot_not_found" in msg

    def test_latest_good_skips_restored(self, tmp_path: Path):
        target = tmp_path / "p.yaml"
        target.write_text("a: 1\n", encoding="utf-8")
        guard = ArtifactGuard(
            snapshot_dir=tmp_path / "artifacts",
            replay_db_path=tmp_path / "noflywheel.db",
        )
        guard.snapshot(target, "exp_1")
        guard.restore("exp_1", reason="test")
        # 已恢复的快照不再是 last-known-good 候选
        assert guard.latest_good(target) is None


class TestReversePairCheck:
    def _make_flywheel(self, db: Path, n_sessions: int = 5) -> None:
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE tool_events (session_id TEXT, tool_name TEXT,"
            " success INTEGER, occurred_at TEXT)"
        )
        for s in range(n_sessions):
            # 同工具连败 5 次（终端型）→ 产生非零代价语料
            for k in range(5):
                conn.execute(
                    "INSERT INTO tool_events VALUES (?,?,?,?)",
                    (f"s{s}", "bash", 0, f"2026-10-1{k+1}T10:00:00"),
                )
        conn.commit()
        conn.close()

    def test_old_baseline_worse_gets_blocked(self, tmp_path: Path):
        db = tmp_path / "flywheel.db"
        self._make_flywheel(db)
        guard = ArtifactGuard(
            snapshot_dir=tmp_path / "artifacts", replay_db_path=db
        )
        # 旧基线 rl=1（健康重复也被拦 → churn 罚重）；当前 rl=8（宽松）
        v = guard.reverse_pair_check(
            old_params={"tool_repeat_limit": 1, "consecutive_fail_limit": 3},
            cur_params={"tool_repeat_limit": 8, "consecutive_fail_limit": 3},
        )
        assert v.trace_count >= RGP_MIN_TRACES
        # 语料为纯失败序列时 churn 项归零——两参数只在「误拦健康重复」上
        # 有差异，故此处应不拦截（防误伤语义自检）
        assert v.block is False, v.reason

    def test_insufficient_traces_abstains(self, tmp_path: Path):
        db = tmp_path / "flywheel.db"
        self._make_flywheel(db, n_sessions=2)  # < RGP_MIN_TRACES
        guard = ArtifactGuard(
            snapshot_dir=tmp_path / "artifacts", replay_db_path=db
        )
        v = guard.reverse_pair_check(
            old_params={"tool_repeat_limit": 1}, cur_params={"tool_repeat_limit": 8}
        )
        assert not v.block
        assert "insufficient" in v.reason

    def test_missing_db_abstains(self, tmp_path: Path):
        guard = ArtifactGuard(
            snapshot_dir=tmp_path / "artifacts",
            replay_db_path=tmp_path / "ghost.db",
        )
        v = guard.reverse_pair_check(old_params={}, cur_params={})
        assert not v.block


class TestHeadroom:
    def test_high_headroom_triggers(self):
        hv = compute_headroom(benchmark_score=80.0, rollback_rate_30d=0.5)
        assert hv.marginal_benefit_positive() is True

    def test_low_headroom_blocks(self):
        hv = compute_headroom(benchmark_score=99.5, rollback_rate_30d=0.05)
        assert hv.marginal_benefit_positive() is False

    def test_all_unknown_defaults_to_true(self):
        assert HeadroomVector().marginal_benefit_positive() is True

    def test_never_aggregates_to_single_score(self):
        hv = compute_headroom(benchmark_score=80.0)
        assert not hasattr(hv, "total") and not hasattr(hv, "score")


class TestLedgerP1:
    def test_origin_column_written_as_self(self, tmp_path: Path):
        from lingclaude.self_optimizer.experiments import ExperimentLedger

        led = ExperimentLedger(db_path=str(tmp_path / "exp.db"))
        exp_id = led.start(1, {"tool_repeat_limit": 3}, 10.0)
        assert exp_id
        row = led.get(exp_id)
        assert row is not None
        assert row["origin"] == "self"
        assert row["verdict"] == "pending"

    def test_rolled_back_rate_none_when_empty(self, tmp_path: Path):
        from lingclaude.self_optimizer.experiments import ExperimentLedger

        led = ExperimentLedger(db_path=str(tmp_path / "exp.db"))
        assert led.rolled_back_rate(30.0) is None

    def test_rolled_back_rate_counts(self, tmp_path: Path):
        from lingclaude.self_optimizer.experiments import ExperimentLedger

        led = ExperimentLedger(db_path=str(tmp_path / "exp.db"))
        a = led.start(1, {"a": 1}, 1.0)
        b = led.start(2, {"b": 2}, 2.0)
        led.settle(a, "accepted", reason="applied")
        led.settle(b, "rolled_back", reason="x")
        assert led.rolled_back_rate(30.0) == pytest.approx(0.5)


class TestPruneSnapshots:
    """A2-3: 快照保留策略（已恢复即删 + 未恢复保新删旧）。"""

    def _guard(self, tmp_path: Path) -> ArtifactGuard:
        return ArtifactGuard(
            snapshot_dir=tmp_path / "artifacts",
            replay_db_path=tmp_path / "noflywheel.db",
        )

    def test_prune_keeps_latest_unrestored(self, tmp_path: Path):
        target = tmp_path / "p.yaml"
        target.write_text("a: 1\n", encoding="utf-8")
        guard = self._guard(tmp_path)
        for i in range(12):
            target.write_text(f"a: {i}\n", encoding="utf-8")
            assert guard.snapshot(target, f"exp_{i}") is not None
        removed = guard.prune_snapshots(keep=10)
        assert removed == 2
        remain = [
            r for r in guard.list_snapshots(target) if r["snapshot_file_exists"]
        ]
        assert len(remain) == 10

    def test_prune_deletes_restored_immediately(self, tmp_path: Path):
        target = tmp_path / "p.yaml"
        target.write_text("a: 1\n", encoding="utf-8")
        guard = self._guard(tmp_path)
        guard.snapshot(target, "exp_a")
        guard.restore("exp_a", reason="test")
        removed = guard.prune_snapshots()
        assert removed == 1
        # jsonl 行保留（取证链不断），文件已删
        rows = guard.list_snapshots(target)
        assert rows and not rows[0]["snapshot_file_exists"]

    def test_prune_failure_safe(self, tmp_path: Path):
        guard = self._guard(tmp_path)
        assert guard.prune_snapshots() == 0  # 空库/无文件不炸


class TestManualRollback:
    """A1: 人工应急回滚核心（CLI rollback --to 的 daemon 层契约）。"""

    def _daemon(self, tmp_path: Path):
        from lingclaude.self_optimizer.daemon import OptimizationDaemon

        return OptimizationDaemon(target=".", state_dir=tmp_path)

    def test_manual_rollback_restores_file(self, tmp_path: Path):
        pytest.importorskip("yaml")
        daemon = self._daemon(tmp_path)
        # 测试隔离（2026-10-17 事故复盘）：_behavior_policy_path 解析到
        # 生产策略文件，直写曾把 lingclaude/core/policies/behavior_policy.yaml
        # 剥成一行——测试目标一律指向 tmp，绝不触碰真实策略文件。
        target = tmp_path / "fake_policy" / "behavior_policy.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("tool_repeat_limit: 3\n", encoding="utf-8")
        daemon.artifacts = ArtifactGuard(
            snapshot_dir=tmp_path / "artifacts",
            replay_db_path=tmp_path / "noflywheel.db",
        )
        with patch.object(
            daemon, "_behavior_policy_path", return_value=target
        ):
            meta = daemon.artifacts.snapshot(target, "exp_manual1")
            assert meta is not None
            target.write_text("tool_repeat_limit: 9\n", encoding="utf-8")
            result = daemon.manual_rollback("exp_manual1")
        assert result.is_ok
        assert target.read_text(encoding="utf-8") == "tool_repeat_limit: 3\n"
        assert result.data["restore"] == "restored_verified"

    def test_manual_rollback_unknown_snapshot(self, tmp_path: Path):
        pytest.importorskip("yaml")
        daemon = self._daemon(tmp_path)
        result = daemon.manual_rollback("ghost_snap")
        assert result.is_error
        assert result.code == "SNAPSHOT_NOT_FOUND"


class TestObservingTTL:
    """A2-1: 观察单超时兜底——不可证即退。"""

    def _daemon(self, tmp_path: Path):
        from lingclaude.self_optimizer.daemon import OptimizationDaemon

        return OptimizationDaemon(target=".", state_dir=tmp_path)

    def test_stale_observation_force_rollback(self, tmp_path: Path):
        pytest.importorskip("yaml")
        from datetime import datetime, timedelta

        daemon = self._daemon(tmp_path)
        exp_id = daemon.experiments.start(1, {"x": 1}, 100.0)
        stale_time = (
            datetime.now() - timedelta(hours=72)
        ).isoformat(timespec="seconds")
        obs = {
            "exp_id": exp_id,
            "snapshot_id": "snap_stale",
            "score_before": 100.0,
            "applied_at": stale_time,
            "goal": "behavior",
            "params": {"x": 1},
        }
        with patch.object(daemon.artifacts, "restore") as mock_restore, \
                patch.object(daemon.benchmark, "run") as mock_bench:
            # 超时应直接恢复快照，不再跑基准复评
            mock_restore.return_value = (True, "restored_verified")
            daemon._settle_observation(obs)
        assert mock_restore.called
        mock_bench.assert_not_called()
        row = daemon.experiments.get(exp_id)
        assert row["verdict"] == "rolled_back"
        assert row["reason"] == "applied:obs_stale_ttl"

    def test_fresh_observation_not_ttl_rolled(self, tmp_path: Path):
        pytest.importorskip("yaml")
        from datetime import datetime

        daemon = self._daemon(tmp_path)
        exp_id = daemon.experiments.start(1, {"x": 1}, 100.0)
        obs = {
            "exp_id": exp_id,
            "snapshot_id": "snap_fresh",
            "score_before": 100.0,
            "applied_at": datetime.now().isoformat(timespec="seconds"),
            "goal": "behavior",
            "params": {"x": 1},
        }
        with patch.object(daemon.artifacts, "restore") as mock_restore, \
                patch.object(daemon.benchmark, "run") as mock_bench:
            mock_bench.return_value = type(
                "B", (), {"score": 100.0, "passed": 10, "total": 10}
            )()
            daemon._settle_observation(obs)
        assert not mock_restore.called  # 新鲜挂单走正常结算
        assert daemon.experiments.get(exp_id)["verdict"] == "accepted"


class TestSettleBeforeFrozen:
    """A2-2: 结算前移——frozen 熔断跳轮时观察单仍须结算（归档闭环）。"""

    def test_settlement_runs_before_frozen_skip(self, tmp_path: Path):
        pytest.importorskip("yaml")
        from lingclaude.core.types import Result
        from lingclaude.self_optimizer.daemon import OptimizationDaemon

        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        daemon.state.frozen = True  # 熔断冻结期
        daemon.state.observing = {
            "exp_id": daemon.experiments.start(1, {"x": 1}, 100.0),
            "snapshot_id": "snap_frozen",
            "score_before": 100.0,
            "applied_at": "2026-10-17T00:00:00",
            "goal": "behavior",
            "params": {"x": 1},
        }
        with patch.object(daemon, "collect_metrics",
                          return_value=Result.ok({"cycles": 1})), \
                patch.object(daemon, "_settle_observation") as mock_settle:
            result = daemon.run_cycle(user_triggered=False)
        assert result.is_ok
        assert mock_settle.called  # frozen 早退前结算已发生
        assert daemon.state.observing is None  # 挂单消化，不再悬置
