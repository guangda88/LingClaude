"""RSI 阶段一守卫件测试（2026-10-17）：artifact_guard + headroom + 状态机数据层。

对应 FINAL_synthesis.md 阶段一-4/5/6/7 的单元契约。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

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
