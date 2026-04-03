from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lingclaude.self_optimizer.daemon import (
    DaemonState,
    OptimizationCycle,
    OptimizationDaemon,
)
from lingclaude.self_optimizer.optimizer import OptimizationResult


class TestDaemonState:
    def test_default_state(self):
        state = DaemonState()
        assert state.total_cycles == 0
        assert state.total_improvements == 0
        assert state.last_optimization_time is None
        assert state.last_metrics == {}
        assert state.cycles == []

    def test_save_and_load(self, tmp_path):
        path = tmp_path / "state.json"
        state = DaemonState(
            last_optimization_time="2026-01-01T00:00:00",
            total_cycles=3,
            total_improvements=1,
            cycles=[{"cycle_id": 1}],
        )
        state.save(path)
        loaded = DaemonState.load(path)
        assert loaded.total_cycles == 3
        assert loaded.total_improvements == 1
        assert loaded.last_optimization_time == "2026-01-01T00:00:00"
        assert loaded.cycles == [{"cycle_id": 1}]

    def test_load_corrupted_file(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("not json {{{")
        state = DaemonState.load(path)
        assert state.total_cycles == 0

    def test_load_nonexistent(self, tmp_path):
        state = DaemonState.load(tmp_path / "nope.json")
        assert state.total_cycles == 0

    def test_cycles_capped_at_100(self, tmp_path):
        state = DaemonState()
        state.cycles = [{"cycle_id": i} for i in range(150)]
        path = tmp_path / "state.json"
        state.save(path)
        loaded = DaemonState.load(path)
        assert len(loaded.cycles) == 150

        state.total_cycles = 150
        cycle = OptimizationCycle(
            cycle_id=151,
            triggered_at="2026-01-01",
            trigger_reason="test",
            trigger_type="test",
            trigger_priority="low",
            best_score=0.0,
            best_params={},
            experiments=1,
            duration_seconds=0.1,
            violations_before=0,
            violations_after=0,
            report_path=None,
        )
        daemon = OptimizationDaemon.__new__(OptimizationDaemon)
        daemon.state = state
        daemon.state_path = path
        daemon._record_cycle(cycle)
        assert len(daemon.state.cycles) == 100


class TestOptimizationDaemon:
    def test_init_default(self, tmp_path):
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        assert daemon.target == "."
        assert daemon.state.total_cycles == 0

    def test_collect_metrics(self, tmp_path):
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        metrics = daemon.collect_metrics()
        assert "structure_violations" in metrics

    def test_build_context(self, tmp_path):
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        daemon.state.last_optimization_time = "2026-01-01T00:00:00"
        ctx = daemon.build_context({"violations": 0})
        assert ctx["last_optimization_time"] == "2026-01-01T00:00:00"

    def test_run_cycle_no_trigger(self, tmp_path):
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        result = daemon.run_cycle()
        assert result is None

    def test_run_cycle_user_trigger(self, tmp_path):
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)

        mock_result = OptimizationResult(
            success=True,
            best_params={"max_complexity": 20},
            best_score=0.0,
            experiments=3,
            duration=0.5,
            error=None,
            history=(),
        )

        with patch.object(daemon.trigger, "check_all_conditions") as mock_trigger:
            from lingclaude.self_optimizer.trigger import TriggerInfo
            mock_trigger.return_value = (
                True,
                TriggerInfo(
                    type="user",
                    reason="test",
                    priority="high",
                    current_value=None,
                    threshold=None,
                    metrics={},
                ),
            )
            with patch.object(daemon.optimizer, "optimize", return_value=mock_result):
                cycle = daemon.run_cycle()

        assert cycle is not None
        assert cycle.cycle_id == 1
        assert cycle.best_score == 0.0
        assert daemon.state.total_cycles == 1

    def test_state_persists_across_instances(self, tmp_path):
        daemon1 = OptimizationDaemon(target=".", state_dir=tmp_path)
        daemon1.state.total_cycles = 5
        daemon1.state.last_optimization_time = "2026-01-01"
        daemon1.state.save(daemon1.state_path)

        daemon2 = OptimizationDaemon(target=".", state_dir=tmp_path)
        assert daemon2.state.total_cycles == 5

    def test_apply_params_no_config(self, tmp_path):
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        daemon._apply_params({"max_complexity": 20})

    def test_apply_params_with_config(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("model:\n  provider: openai\n")
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        original_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp_path)
            daemon._apply_params({"max_complexity": 20})
            import yaml
            raw = yaml.safe_load(config_path.read_text())
            assert raw["self_optimizer"]["triggers"]["max_complexity"] == 20
        finally:
            os.chdir(original_cwd)

    def test_optimization_cycle_frozen(self):
        cycle = OptimizationCycle(
            cycle_id=1,
            triggered_at="2026-01-01",
            trigger_reason="test",
            trigger_type="user",
            trigger_priority="high",
            best_score=0.0,
            best_params={},
            experiments=1,
            duration_seconds=0.1,
            violations_before=0,
            violations_after=0,
            report_path=None,
        )
        with pytest.raises(AttributeError):
            cycle.cycle_id = 2

    def test_run_once_delegates(self, tmp_path):
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        with patch.object(daemon, "run_cycle", return_value=None) as mock:
            result = daemon.run_once()
        mock.assert_called_once()
        assert result is None

    def test_report_generated(self, tmp_path):
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)

        mock_result = OptimizationResult(
            success=True,
            best_params={"max_complexity": 20},
            best_score=0.0,
            experiments=3,
            duration=0.5,
            error=None,
            history=(),
        )

        with patch.object(daemon.trigger, "check_all_conditions") as mock_trigger:
            from lingclaude.self_optimizer.trigger import TriggerInfo
            mock_trigger.return_value = (
                True,
                TriggerInfo(
                    type="user",
                    reason="manual",
                    priority="high",
                    current_value=None,
                    threshold=None,
                    metrics={},
                ),
            )
            with patch.object(daemon.optimizer, "optimize", return_value=mock_result):
                cycle = daemon.run_cycle()

        assert cycle is not None
        assert cycle.report_path is not None
        report_path = Path(cycle.report_path)
        assert report_path.exists()
        content = report_path.read_text()
        assert "Self-Optimization Report" in content
