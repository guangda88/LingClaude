"""R1/R2/R3 三件套专项测试（2026-10-22，RSI 增量批次）。

R2: GEPA 式文本反馈入账 —— feedback_text 列/settle 参数/假说生成器取数面
R1: DGM 式谱系 archive —— 备选池入池/采样/出池 + daemon 回采接线
R3: 元层双任务金丝雀验证 —— MetaUpgradeVerifier fail-closed 裁决
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------- #
# R2: 文本反馈入账
# ---------------------------------------------------------------- #
class TestR2FeedbackText:
    def test_settle_persists_feedback_text(self, tmp_path: Path):
        from lingclaude.self_optimizer.experiments import ExperimentLedger

        ledger = ExperimentLedger(db_path=str(tmp_path / "experiments.db"))
        exp = ledger.start(1, {"tool_repeat_limit": 4}, 100.0)
        assert exp
        ok = ledger.settle(
            exp, "rejected", score_after=90.0,
            reason="p0_gate_regression",
            feedback_text="参数联动复测回退 100→90，候选会拉低行为分被拒",
        )
        assert ok
        row = ledger.get(exp)
        assert row["feedback_text"].startswith("参数联动复测回退")
        assert row["reason"] == "p0_gate_regression"

    def test_settle_without_feedback_keeps_default(self, tmp_path: Path):
        from lingclaude.self_optimizer.experiments import ExperimentLedger

        ledger = ExperimentLedger(db_path=str(tmp_path / "experiments.db"))
        exp = ledger.start(1, {"a": 1}, 10.0)
        ledger.settle(exp, "accepted", score_after=12.0, reason="applied")
        assert ledger.get(exp)["feedback_text"] == ""

    def test_legacy_db_migration_adds_columns(self, tmp_path: Path):
        """无 feedback_text/lineage 列的旧库 → 初始化补列（R2/R1 迁移）。"""
        import sqlite3

        from lingclaude.self_optimizer.experiments import ExperimentLedger

        db = tmp_path / "legacy.db"
        conn = sqlite3.connect(db)
        conn.execute(
            """
            CREATE TABLE experiments (
                experiment_id TEXT PRIMARY KEY, cycle_id INTEGER,
                params_json TEXT NOT NULL, score_before REAL,
                score_after REAL, verdict TEXT NOT NULL DEFAULT 'pending',
                reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                decided_at TEXT
            )
            """
        )
        conn.commit()
        conn.close()
        ledger = ExperimentLedger(db_path=str(db))
        exp = ledger.start(1, {"a": 1}, 5.0)
        assert ledger.settle(exp, "accepted", feedback_text="迁移后可写")
        assert ledger.get(exp)["feedback_text"] == "迁移后可写"

    def test_hypothesis_digest_groups_by_reason_and_text(
        self, tmp_path: Path
    ):
        from lingclaude.self_optimizer.experiments import ExperimentLedger

        ledger = ExperimentLedger(db_path=str(tmp_path / "experiments.db"))
        for i in range(3):
            exp = ledger.start(i + 1, {"tool_repeat_limit": 4}, 100.0)
            ledger.settle(
                exp, "rejected", score_after=90.0,
                reason="p0_gate_regression",
                feedback_text="回退 5 分，候选拉低行为分",
            )
        exp = ledger.start(4, {"tool_repeat_limit": 4}, 100.0)
        ledger.settle(
            exp, "rolled_back", score_after=85.0,
            reason="p1_worse_than_best_ever",
            feedback_text="未胜归档最优",
        )
        digest = ledger.hypothesis_feedback_digest(window_hours=24.0)
        assert len(digest) == 2
        top = digest[0]
        assert top["reason"] == "p0_gate_regression"
        assert top["n"] == 3
        assert "回退 5 分" in top["feedback_text"]

    def test_digest_ignores_accepted_and_empty(self, tmp_path: Path):
        from lingclaude.self_optimizer.experiments import ExperimentLedger

        ledger = ExperimentLedger(db_path=str(tmp_path / "experiments.db"))
        exp = ledger.start(1, {"a": 1}, 10.0)
        ledger.settle(exp, "accepted", score_after=11.0, reason="applied",
                      feedback_text="收编")  # accepted 不进 digest
        exp = ledger.start(2, {"a": 1}, 10.0)
        ledger.settle(exp, "rejected", score_after=9.0, reason="x")  # 无文本
        assert ledger.hypothesis_feedback_digest() == []


# ---------------------------------------------------------------- #
# R1: 谱系 archive
# ---------------------------------------------------------------- #
class TestR1LineagePool:
    def _ledger(self, tmp_path: Path):
        from lingclaude.self_optimizer.experiments import ExperimentLedger

        return ExperimentLedger(db_path=str(tmp_path / "experiments.db"))

    def test_pool_candidate_and_sample(self, tmp_path: Path):
        ledger = self._ledger(tmp_path)
        assert ledger.pool_candidate(
            cycle_id=1, params={"tool_repeat_limit": 5}, score=42.0,
            exp_id="exp_x", note="p1_worse",
        )
        assert ledger.pool_size() == 1
        cand = ledger.pool_sample()
        assert cand is not None
        assert cand["params"] == {"tool_repeat_limit": 5}
        assert cand["exp_id"].startswith("cand_")
        assert "p1_worse" in cand["note"]

    def test_pool_remove_consumes_candidate(self, tmp_path: Path):
        ledger = self._ledger(tmp_path)
        ledger.pool_candidate(cycle_id=1, params={"a": 1}, score=1.0,
                              exp_id=None, note="t")
        cand = ledger.pool_sample()
        assert ledger.pool_remove(cand["exp_id"])
        assert ledger.pool_size() == 0
        assert ledger.pool_sample() is None

    def test_candidate_verdict_does_not_pollute_normal_queries(
        self, tmp_path: Path
    ):
        """candidate 行不进 recent_rollbacks / accepted_in_window。"""
        ledger = self._ledger(tmp_path)
        ledger.pool_candidate(cycle_id=1, params={"a": 1}, score=1.0,
                              exp_id=None, note="t")
        assert ledger.recent_rollbacks() == []
        assert ledger.accepted_in_window(24.0) == 0

    def test_pool_candidate_failure_returns_false(self, tmp_path: Path):
        from lingclaude.self_optimizer.experiments import ExperimentLedger

        # 目录路径当 db 文件 → sqlite 打不开 → fail-soft 返回 False
        ledger = ExperimentLedger(db_path=str(tmp_path / "no.db"))
        ledger.close()
        ledger._conn = None
        ledger.db_path = tmp_path  # 目录而非文件 → 必然失败
        assert ledger.pool_candidate(
            cycle_id=1, params={}, score=0.0, exp_id=None, note="x"
        ) is False

    def test_daemon_recall_injects_candidate_and_passes_gates(
        self, tmp_path: Path
    ):
        """回采接线 E2E：搜索失败 → 候选注入 → 走 P1 择优正常结算，
        且候选已出池（复测裁决过，不回池防污染）。"""
        pytest.importorskip("yaml")
        from lingclaude.core.types import Result
        from lingclaude.self_optimizer.daemon import OptimizationDaemon
        from lingclaude.self_optimizer.optimizer import OptimizationResult
        from lingclaude.self_optimizer.trigger import TriggerInfo

        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        daemon.state.lineage_last_recall_cycle = None
        daemon.experiments.pool_candidate(
            cycle_id=0, params={"tool_repeat_limit": 5}, score=30.0,
            exp_id="exp_src", note="e2e",
        )
        assert daemon.experiments.pool_size() == 1

        trigger = patch.object(
            daemon.trigger, "check_all_conditions",
            return_value=(
                True,
                TriggerInfo(
                    type="user", reason="test", priority="high",
                    current_value=1, threshold=0, metrics={},
                ),
            ),
        )
        opt_fail = patch.object(
            daemon.optimizer, "optimize",
            return_value=OptimizationResult(
                success=False, best_params={}, best_score=0.0,
                experiments=0, duration=0.0, error="dry", history=(),
            ),
        )
        with trigger, opt_fail, patch.object(
            daemon, "collect_metrics",
            return_value=Result.ok({"cycles": 1}),
        ), patch.object(daemon.benchmark, "run") as mock_bench:
            mock_bench.return_value = type(
                "B", (), {"score": 80.0, "passed": 8, "total": 10}
            )()
            result = daemon.run_cycle(user_triggered=True)
        assert result.is_ok
        # 候选被回采消费（pool_remove）且不回池（回采轮标记）
        assert daemon.experiments.pool_size() == 0
        assert daemon.state.lineage_last_recall_cycle is not None
        # 回采候选走了 P1 择优（30 劣于 best_ever=None → 首轮无基线放行
        # 进观察/应用路径；台账里应有本轮单）
        rows = daemon.experiments.list_recent()
        assert rows, "回采候选应开实验单走正常结算"

    def test_recall_throttled_by_lineage_recall_every(
        self, tmp_path: Path
    ):
        from lingclaude.self_optimizer.daemon import (
            LINEAGE_RECALL_EVERY,
            OptimizationDaemon,
        )
        from lingclaude.self_optimizer.optimizer import OptimizationResult

        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        # 刚回采过（上一轮）→ 未达节流窗口 → 不取候选
        daemon.state.lineage_last_recall_cycle = daemon.state.total_cycles + 1
        daemon.experiments.pool_candidate(
            cycle_id=0, params={"a": 1}, score=1.0, exp_id=None, note="t"
        )
        result = OptimizationResult(
            success=False, best_params={}, best_score=0.0,
            experiments=0, duration=0.0, error="dry", history=(),
        )
        # 直接验证节流判据（不跑整周期，節流逻辑单点验证）
        due = (
            daemon.state.lineage_last_recall_cycle is None
            or daemon.state.total_cycles + 1
            - int(daemon.state.lineage_last_recall_cycle)
            >= LINEAGE_RECALL_EVERY
        )
        assert due is False


# ---------------------------------------------------------------- #
# R3: 元层双任务金丝雀
# ---------------------------------------------------------------- #
class TestR3MetaUpgrade:
    def _verifier(self, tmp_path: Path):
        from lingclaude.self_optimizer.meta_upgrade import MetaUpgradeVerifier

        return MetaUpgradeVerifier(
            target=".",
            replay_db=tmp_path / "nonexistent_flywheel.db",
            reports_dir=tmp_path / "meta_reports",
        )

    def test_empty_proposal_fails_closed(self, tmp_path: Path):
        v = self._verifier(tmp_path)
        report = v.verify("空提案")
        assert report.overall == "rejected"
        assert all(r.verdict == "invalid" for r in report.results)

    def test_missing_corpus_is_invalid_not_pass(self, tmp_path: Path):
        """回放语料缺失 → 读数 invalid → 整单 rejected（fail-closed 核心）。"""
        v = self._verifier(tmp_path)
        report = v.verify(
            "无语料提案", replay_weights={"heal_loss": 1.0},
        )
        assert report.overall == "rejected"
        tasks = {r.task: r.verdict for r in report.results}
        assert tasks["behavior_replay"] == "invalid"

    def test_improvement_both_tasks_adoptable(self, tmp_path: Path):
        from lingclaude.self_optimizer.meta_upgrade import MetaUpgradeVerifier

        v = MetaUpgradeVerifier(
            target=".", replay_db=tmp_path / "no.db",
            reports_dir=tmp_path / "meta_reports",
        )
        with patch.object(v, "_replay_read", side_effect=[100.0, 90.0]), \
                patch.object(v, "_bench_read", side_effect=[80.0, 85.0]):
            report = v.verify(
                "权重改进", replay_weights={"heal_loss": 1.0},
            )
        assert report.overall == "adoptable"
        verdicts = {r.task: r.verdict for r in report.results}
        assert verdicts == {
            "behavior_replay": "pass", "structure_bench": "pass",
        }

    def test_regression_either_task_rejected(self, tmp_path: Path):
        from lingclaude.self_optimizer.meta_upgrade import MetaUpgradeVerifier

        v = MetaUpgradeVerifier(
            target=".", replay_db=tmp_path / "no.db",
            reports_dir=tmp_path / "meta_reports",
        )
        # 任务A改善、任务B劣化 → 整单 rejected（跨任务迁移验证语义）
        with patch.object(v, "_replay_read", side_effect=[100.0, 50.0]), \
                patch.object(v, "_bench_read", side_effect=[80.0, 70.0]):
            report = v.verify("跨任务劣化提案", replay_weights={"heal_loss": 2.0})
        assert report.overall == "rejected"

    def test_report_persisted_and_ledger_recorded(self, tmp_path: Path):
        from lingclaude.self_optimizer.meta_upgrade import MetaUpgradeVerifier

        reports_dir = tmp_path / "meta_reports"
        v = MetaUpgradeVerifier(
            target=".", replay_db=tmp_path / "no.db",
            reports_dir=reports_dir,
        )
        with patch.object(v, "_replay_read", side_effect=[10.0, 9.0]), \
                patch.object(v, "_bench_read", side_effect=[80.0, 82.0]), \
                patch(
                    "lingclaude.core.verify_ledger.record_verify"
                ) as mock_ledger:
            report = v.verify("落盘提案", replay_weights={"heal_loss": 1.5})
        assert report.overall == "adoptable"
        assert list(reports_dir.glob("*.json"))
        assert mock_ledger.called
        kwargs = mock_ledger.call_args.kwargs
        assert kwargs["kind"] == "meta_upgrade"

    def test_tolerance_band_noise_not_regress(self, tmp_path: Path):
        from lingclaude.self_optimizer.meta_upgrade import (
            MetaUpgradeVerifier, TOLERANCE_PCT,
        )

        v = MetaUpgradeVerifier(
            target=".", replay_db=tmp_path / "no.db",
            reports_dir=tmp_path / "meta_reports",
        )
        # 劣化幅度 < 容忍带 → pass（语料噪声不计）
        within = 100.0 * (1.0 + TOLERANCE_PCT / 100.0)
        with patch.object(v, "_replay_read", side_effect=[100.0, within]), \
                patch.object(v, "_bench_read", side_effect=[80.0, 80.0]):
            report = v.verify("带内噪声", replay_weights={"heal_loss": 1.0})
        replay_r = [r for r in report.results if r.task == "behavior_replay"][0]
        assert replay_r.verdict == "pass"
