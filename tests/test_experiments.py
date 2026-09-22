"""F2 归因链测试（2026-09-22）：ExperimentLedger + corrections 证据挂钩 +
state_store_ext 实验视图与 F0 指标。

覆盖诊断文档 §M 执行序 F2 的三个验收点：
1. experiment_id 贯穿：daemon 三出口（P0 拒绝 / P1 回滚 / 正常应用）落账；
2. accept/rollback 结算幂等、pending 超时清扫；
3. corrections.experiment_id 挂钩（观察窗语义）。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from lingclaude.core.data_flywheel import CorrectionEntry, DataFlywheel
from lingclaude.core.types import Result
from lingclaude.self_optimizer.experiments import (
    PENDING_TTL_HOURS,
    ExperimentLedger,
)
from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase
from lingclaude.self_optimizer.state_store_ext import (
    FlywheelRecordType,
    FlywheelStateStore,
)


# --------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------- #
@pytest.fixture()
def root(tmp_path: Path) -> Path:
    """模拟项目根：三个库都放 tmp，测试天然与真实库隔离。"""
    return tmp_path


@pytest.fixture()
def ledger(root: Path) -> ExperimentLedger:
    instance = ExperimentLedger(db_path=str(root / ".lingclaude" / "experiments.db"))
    yield instance
    instance.close()


# --------------------------------------------------------------------- #
# ExperimentLedger 基本生命周期
# --------------------------------------------------------------------- #
class TestLedgerLifecycle:
    def test_start_and_settle_accepted(self, ledger: ExperimentLedger):
        exp_id = ledger.start(1, {"tool_repeat_limit": 4}, 10.0)
        assert exp_id and exp_id.startswith("exp_")
        assert ledger.settle(exp_id, "accepted", score_after=8.0, reason="applied")
        rec = ledger.list_recent(1)[0]
        assert rec["verdict"] == "accepted"
        assert rec["score_before"] == 10.0
        assert rec["score_after"] == 8.0
        assert rec["reason"] == "applied"
        assert rec["decided_at"] is not None

    def test_settle_idempotent(self, ledger: ExperimentLedger):
        exp_id = ledger.start(1, {}, 5.0)
        assert ledger.settle(exp_id, "accepted", reason="first")
        # 已终态行二次结算：no-op，不覆盖
        assert not ledger.settle(exp_id, "rolled_back", reason="second")
        assert ledger.list_recent(1)[0]["reason"] == "first"

    def test_settle_rejects_invalid_verdict(self, ledger: ExperimentLedger):
        exp_id = ledger.start(1, {}, 5.0)
        assert not ledger.settle(exp_id, "exploded")  # 拼写漂移防护
        assert ledger.current_pending_id() == exp_id  # 行仍是 pending

    def test_settle_none_id_is_silent_noop(self, ledger: ExperimentLedger):
        assert ledger.settle(None, "accepted") is False

    def test_start_failure_returns_none(self, tmp_path: Path):
        # 指向目录路径，INSERT 必失败 → 降级返回 None 而非抛出
        bad = ExperimentLedger(db_path=str(tmp_path))
        assert bad.start(1, {}, 1.0) is None

    def test_init_failure_degrades(self, tmp_path: Path):
        # db_path 是目录 → 建表失败 → 不抛出、后续方法安全降级
        (tmp_path / "as_dir").mkdir()
        bad = ExperimentLedger(db_path=str(tmp_path / "as_dir"))
        assert bad.current_pending_id() is None
        assert bad.counts()["accepted"] == 0


# --------------------------------------------------------------------- #
# pending 观察窗与超时清扫
# --------------------------------------------------------------------- #
class TestPendingWindow:
    def test_current_pending_returns_latest(self, ledger: ExperimentLedger):
        assert ledger.current_pending_id() is None
        first = ledger.start(1, {}, 1.0)
        second = ledger.start(2, {}, 2.0)
        assert ledger.current_pending_id() == second
        ledger.settle(second, "accepted")
        assert ledger.current_pending_id() == first
        ledger.settle(first, "rejected")
        assert ledger.current_pending_id() is None

    def test_expire_stale_pending(self, ledger: ExperimentLedger):
        stale = ledger.start(1, {}, 1.0)
        fresh = ledger.start(2, {}, 2.0)
        # 把 stale 单的 created_at 回拨到 TTL 之外
        cutoff = datetime.now() - timedelta(hours=PENDING_TTL_HOURS + 1)
        conn = sqlite3.connect(str(ledger.db_path))
        conn.execute(
            "UPDATE experiments SET created_at = ? WHERE experiment_id = ?",
            (cutoff.isoformat(timespec="seconds"), stale),
        )
        conn.commit()
        conn.close()
        assert ledger.expire_stale_pending() == 1
        rows = {r["experiment_id"]: r for r in ledger.list_recent()}
        assert rows[stale]["verdict"] == "rolled_back"
        assert rows[stale]["reason"] == "stale_pending_expired"
        assert rows[fresh]["verdict"] == "pending"  # 新单不受影响


# --------------------------------------------------------------------- #
# corrections 证据挂钩
# --------------------------------------------------------------------- #
class TestCorrectionEvidence:
    def test_correction_carries_experiment_id(self, root: Path, ledger: ExperimentLedger):
        exp_id = ledger.start(1, {"tool_repeat_limit": 3}, 0.0)
        fw = DataFlywheel(db_path=str(root / ".lingclaude" / "data_flywheel.db"))
        try:
            assert fw.log_correction(
                CorrectionEntry(
                    original_error="err",
                    correction="fix",
                    source="test",
                    confidence=0.9,
                    applied_at="2026-09-22T00:00:00",
                    experiment_id=exp_id,
                )
            ).is_ok
            conn = sqlite3.connect(str(root / ".lingclaude" / "data_flywheel.db"))
            row = conn.execute(
                "SELECT experiment_id FROM corrections ORDER BY id DESC LIMIT 1"
            ).fetchone()
            conn.close()
            assert row[0] == exp_id
        finally:
            fw.close()

    def test_correction_without_experiment(self, root: Path):
        fw = DataFlywheel(db_path=str(root / ".lingclaude" / "data_flywheel.db"))
        try:
            assert fw.log_correction(
                CorrectionEntry(
                    original_error="err",
                    correction="fix",
                    source="test",
                    confidence=0.5,
                    applied_at="2026-09-22T00:00:00",
                )
            ).is_ok
            conn = sqlite3.connect(str(root / ".lingclaude" / "data_flywheel.db"))
            row = conn.execute(
                "SELECT experiment_id FROM corrections ORDER BY id DESC LIMIT 1"
            ).fetchone()
            conn.close()
            assert row[0] is None
        finally:
            fw.close()

    def test_unmigrated_legacy_db_gets_column(self, tmp_path: Path):
        """存量库（无 experiment_id 列）打开时自动迁移，原数据不丢。"""
        db = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE corrections (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "original_error TEXT NOT NULL, correction TEXT NOT NULL, "
            "source TEXT NOT NULL, confidence REAL NOT NULL, applied_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO corrections (original_error, correction, source, "
            "confidence, applied_at) VALUES ('o', 'c', 's', 0.5, 't')"
        )
        conn.commit()
        conn.close()

        fw = DataFlywheel(db_path=str(db))
        try:
            conn = sqlite3.connect(str(db))
            cols = {r[1] for r in conn.execute("PRAGMA table_info(corrections)")}
            n = conn.execute("SELECT count(*) FROM corrections").fetchone()[0]
            conn.close()
            assert "experiment_id" in cols  # 迁移完成
            assert n == 1  # 原数据未丢
        finally:
            fw.close()


# --------------------------------------------------------------------- #
# state_store_ext：实验视图 + F0 指标
# --------------------------------------------------------------------- #
class TestStoreOutcomes:
    def test_store_sees_experiments_and_rates(self, root: Path, ledger: ExperimentLedger):
        a = ledger.start(1, {}, 10.0)
        ledger.settle(a, "accepted", score_after=8.0)
        b = ledger.start(2, {}, 9.0)
        ledger.settle(b, "rolled_back", score_after=9.0)
        c = ledger.start(3, {}, 8.0)
        ledger.settle(c, "rejected", score_after=7.0)
        ledger.start(4, {}, 7.0)  # pending 不进结算口径

        # 现实前提：knowledge.db 由 KnowledgeBase 初始化（rules 表存在）
        kb = KnowledgeBase(db_path=str(root / ".lingclaude" / "knowledge.db"))
        kb.close()

        store = FlywheelStateStore(project_root=root)
        try:
            rows = store.query(FlywheelRecordType.EXPERIMENT)
            assert len(rows) == 4

            counts = store.health_counts()
            assert counts["experiments_total"] == 4
            assert counts["experiments_pending"] == 1

            out = store.optimization_outcomes()
            assert out["experiments_settled"] == 3
            assert out["applied"] == 1
            assert out["rolled_back"] == 1
            assert out["rejected"] == 1
            assert out["apply_rate"] == pytest.approx(1 / 3, abs=1e-3)
            assert out["rollback_rate"] == pytest.approx(1 / 3, abs=1e-3)
        finally:
            store.close()

    def test_store_degrades_without_experiments_db(self, tmp_path: Path):
        root = tmp_path / "empty_root"
        root.mkdir()
        store = FlywheelStateStore(project_root=root)
        try:
            assert store.query(FlywheelRecordType.EXPERIMENT) == []
            out = store.optimization_outcomes()
            assert out["experiments_settled"] == 0
            assert out["apply_rate"] == 0.0
        finally:
            store.close()


# --------------------------------------------------------------------- #
# daemon 三出口落账（单元级：打桩优化器，走真实 run_cycle 流程）
# --------------------------------------------------------------------- #
class TestDaemonSettlement:
    def _daemon(self, tmp_path: Path):
        from unittest.mock import patch

        from lingclaude.self_optimizer.daemon import OptimizationDaemon

        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        return daemon

    def _trigger(self, daemon):
        from unittest.mock import patch

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

    def _opt_result(self, daemon, best_score: float):
        from unittest.mock import patch

        from lingclaude.self_optimizer.optimizer import OptimizationResult

        return patch.object(
            daemon.optimizer,
            "optimize",
            return_value=OptimizationResult(
                success=True,
                best_params={"tool_repeat_limit": 4},
                best_score=best_score,
                experiments=2,
                duration=0.1,
                error=None,
                history=(),
            ),
        )

    def test_run_cycle_accepts_and_settles(self, tmp_path: Path):
        pytest.importorskip("yaml")
        daemon = self._daemon(tmp_path)
        with self._trigger(daemon), self._opt_result(daemon, best_score=1.0), \
                patch.object(
                    daemon, "collect_metrics",
                    return_value=Result.ok({"cycles": 1}),
                ), \
                patch.object(daemon.benchmark, "run") as mock_bench, \
                patch.object(daemon, "_apply_params") as mock_apply:
            mock_bench.return_value = type(
                "B", (), {"score": 80.0, "passed": 8, "total": 10}
            )()
            result = daemon.run_cycle(user_triggered=True)
        assert result.is_ok
        # best_score=1.0 无归档最优 → 走 accepted 分支
        assert mock_apply.called
        rows = daemon.experiments.list_recent()
        assert len(rows) == 1
        assert rows[0]["verdict"] == "accepted"
        assert rows[0]["reason"] == "applied"

    def test_run_cycle_rolls_back_and_settles(self, tmp_path: Path):
        pytest.importorskip("yaml")
        daemon = self._daemon(tmp_path)
        # 预置归档最优 0.5 < 本轮 1.0 → P1 择优回滚分支
        daemon.state.best_ever_score = 0.5
        daemon.state.best_ever_params = {"tool_repeat_limit": 3}
        daemon.state.best_ever_cycle_id = 1
        with self._trigger(daemon), self._opt_result(daemon, best_score=1.0), \
                patch.object(
                    daemon, "collect_metrics",
                    return_value=Result.ok({"cycles": 1}),
                ), \
                patch.object(daemon.benchmark, "run") as mock_bench:
            mock_bench.return_value = type(
                "B", (), {"score": 80.0, "passed": 8, "total": 10}
            )()
            result = daemon.run_cycle(user_triggered=True)
        assert result.is_ok
        rows = daemon.experiments.list_recent()
        assert len(rows) == 1
        assert rows[0]["verdict"] == "rolled_back"
        assert rows[0]["reason"] == "p1_worse_than_best_ever"
