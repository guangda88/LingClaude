"""Tests for behavior trigger integration with daemon context."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lingclaude.core.config import TriggerConfig
from lingclaude.self_optimizer.daemon import DaemonState, OptimizationDaemon
from lingclaude.self_optimizer.trigger import OptimizationTrigger, TriggerInfo


class TestCheckBehavior:
    """Direct tests for OptimizationTrigger._check_behavior()."""

    def test_no_behavior_issues(self):
        trigger = OptimizationTrigger(TriggerConfig())
        ctx = {
            "hallucination_risk": 0,
            "frustration_rate": 0,
            "corrections_received": 0,
            "tool_error_rate": 0,
        }
        result = trigger._check_behavior(ctx)
        assert result is None

    def test_hallucination_risk_high(self):
        trigger = OptimizationTrigger(TriggerConfig())
        ctx = {"hallucination_risk": 0.6}
        result = trigger._check_behavior(ctx)
        assert result is not None
        assert result.type == "behavior"
        assert "幻觉风险过高" in result.reason
        assert result.priority == "high"
        assert result.current_value == 0.6
        assert result.threshold == 0.3

    def test_hallucination_risk_medium(self):
        trigger = OptimizationTrigger(TriggerConfig())
        ctx = {"hallucination_risk": 0.35}
        result = trigger._check_behavior(ctx)
        assert result is not None
        assert result.priority == "medium"

    def test_hallucination_risk_below_threshold(self):
        trigger = OptimizationTrigger(TriggerConfig())
        ctx = {"hallucination_risk": 0.2}
        result = trigger._check_behavior(ctx)
        assert result is None

    def test_frustration_rate_triggers(self):
        trigger = OptimizationTrigger(TriggerConfig())
        ctx = {"frustration_rate": 0.3}
        result = trigger._check_behavior(ctx)
        assert result is not None
        assert result.type == "behavior"
        assert "用户沮丧率" in result.reason
        assert result.priority == "high"
        assert result.current_value == 0.3

    def test_frustration_rate_below_threshold(self):
        trigger = OptimizationTrigger(TriggerConfig())
        ctx = {"frustration_rate": 0.1}
        result = trigger._check_behavior(ctx)
        assert result is None

    def test_corrections_received_triggers(self):
        trigger = OptimizationTrigger(TriggerConfig())
        ctx = {"corrections_received": 2}
        result = trigger._check_behavior(ctx)
        assert result is not None
        assert result.type == "behavior"
        assert "纠正" in result.reason
        assert result.priority == "medium"
        assert result.current_value == 2
        assert result.threshold == 2

    def test_corrections_received_one_does_not_trigger(self):
        trigger = OptimizationTrigger(TriggerConfig())
        ctx = {"corrections_received": 1}
        result = trigger._check_behavior(ctx)
        assert result is None

    def test_tool_error_rate_triggers(self):
        trigger = OptimizationTrigger(TriggerConfig())
        ctx = {"tool_error_rate": 0.5}
        result = trigger._check_behavior(ctx)
        assert result is not None
        assert result.type == "behavior"
        assert "工具调用失败率" in result.reason
        assert result.priority == "medium"
        assert result.current_value == 0.5

    def test_tool_error_rate_below_threshold(self):
        trigger = OptimizationTrigger(TriggerConfig())
        ctx = {"tool_error_rate": 0.2}
        result = trigger._check_behavior(ctx)
        assert result is None

    def test_hallucination_takes_priority_over_corrections(self):
        trigger = OptimizationTrigger(TriggerConfig())
        ctx = {
            "hallucination_risk": 0.4,
            "corrections_received": 3,
        }
        result = trigger._check_behavior(ctx)
        assert result is not None
        assert "幻觉" in result.reason


class TestBehaviorViaCheckAll:
    """Test that _check_behavior is wired into check_all_conditions."""

    def test_behavior_trigger_fires_via_check_all(self):
        trigger = OptimizationTrigger(TriggerConfig())
        ctx = {
            "hallucination_risk": 0.5,
            "frustration_rate": 0,
            "corrections_received": 0,
            "tool_error_rate": 0,
        }
        should_trigger, info = trigger.check_all_conditions(ctx)
        assert should_trigger is True
        assert info is not None
        assert info.type == "behavior"

    def test_no_trigger_when_all_clear(self):
        trigger = OptimizationTrigger(TriggerConfig())
        ctx = {
            "hallucination_risk": 0,
            "frustration_rate": 0,
            "corrections_received": 0,
            "tool_error_rate": 0,
        }
        should_trigger, info = trigger.check_all_conditions(ctx)
        assert should_trigger is False
        assert info is None

    def test_user_triggered_overrides_behavior(self):
        trigger = OptimizationTrigger(TriggerConfig())
        ctx = {
            "user_triggered": True,
            "hallucination_risk": 0.8,
        }
        should_trigger, info = trigger.check_all_conditions(ctx)
        assert should_trigger is True
        assert info is not None
        assert info.type == "user"


class TestDaemonBehaviorIntegration:
    """Test daemon.update_behavior() flows into build_context() for triggers."""

    @pytest.fixture
    def daemon(self, tmp_path):
        with patch.object(OptimizationDaemon, "__init__", lambda self, *a, **kw: None):
            d = OptimizationDaemon.__new__(OptimizationDaemon)
            d.config = MagicMock()
            d.config.triggers = TriggerConfig()
            d.state = DaemonState()
            d.state_dir = tmp_path
            d.state_path = tmp_path / "daemon_state.json"
            d.reports_dir = tmp_path / "reports"
            d.target = "."
            d._behavior_snapshot = {}
            d.trigger = OptimizationTrigger(TriggerConfig())
            d.evaluator = MagicMock()
            d.optimizer = MagicMock()
            d.advisor = MagicMock()
            return d

    def test_update_behavior_stores_snapshot(self, daemon):
        behavior = {
            "hallucination_risk": 0.6,
            "frustration_rate": 0.2,
            "corrections_received": 3,
            "tool_error_rate": 0.1,
        }
        daemon.update_behavior(behavior)
        assert daemon._behavior_snapshot == behavior

    def test_build_context_includes_behavior(self, daemon):
        daemon.update_behavior({
            "hallucination_risk": 0.5,
            "frustration_rate": 0.3,
            "corrections_received": 2,
            "tool_error_rate": 0.1,
        })
        ctx = daemon.build_context({}, user_triggered=False)
        assert ctx["hallucination_risk"] == 0.5
        assert ctx["frustration_rate"] == 0.3
        assert ctx["corrections_received"] == 2
        assert ctx["tool_error_rate"] == 0.1

    def test_build_context_defaults_to_zero(self, daemon):
        ctx = daemon.build_context({}, user_triggered=False)
        assert ctx["hallucination_risk"] == 0
        assert ctx["frustration_rate"] == 0
        assert ctx["corrections_received"] == 0
        assert ctx["tool_error_rate"] == 0

    def test_end_to_end_behavior_triggers_optimization(self, daemon):
        daemon.update_behavior({
            "hallucination_risk": 0.6,
            "frustration_rate": 0,
            "corrections_received": 0,
            "tool_error_rate": 0,
        })
        daemon.evaluator.get_current_metrics.return_value = {"structure_violations": 5}
        ctx = daemon.build_context(
            daemon.evaluator.get_current_metrics.return_value,
            user_triggered=False,
        )
        should_trigger, info = daemon.trigger.check_all_conditions(ctx)
        assert should_trigger is True
        assert info is not None
        assert info.type == "behavior"
        assert "幻觉" in info.reason
