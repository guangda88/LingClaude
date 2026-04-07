"""Tests for behavior-aware model router."""
from __future__ import annotations

import pytest

from lingclaude.core.behavior import BehaviorMetrics, Emotion
from lingclaude.core.behavior_aware_router import (
    BehaviorAwareRouter,
    BehaviorRouterStrategy,
    BehaviorRoutingConfig,
)


class TestBehaviorAwareRouter:
    """Test behavior-aware routing functionality."""

    def test_initializes_with_base_router(self):
        """Verify router initializes with base intelligent router."""
        router = BehaviorAwareRouter()
        assert router.base_router is not None
        assert router._behavior is None
        assert router._strategy == BehaviorRouterStrategy.STANDARD

    def test_standard_strategy_when_no_behavior(self):
        """Verify standard strategy is used when no behavior is set."""
        router = BehaviorAwareRouter()
        decision = router.route("写一个函数")

        # Should use base router's decision
        assert decision.model.value in ["GLM-4.7", "GLM-5.1"]
        assert decision.task_type is not None

    def test_conservative_strategy_on_high_hallucination(self):
        """Verify conservative strategy on high hallucination risk."""
        router = BehaviorAwareRouter()

        # Simulate high hallucination risk by recording many turns without tools when needed
        behavior = BehaviorMetrics(total_turns=10, turns_with_tools=0, turns_without_tools_but_needed=10)
        router.set_behavior(behavior)

        # Should switch to conservative strategy
        assert router._strategy == BehaviorRouterStrategy.CONSERVATIVE

    def test_conservative_strategy_on_high_frustration(self):
        """Verify conservative strategy on high user frustration."""
        router = BehaviorAwareRouter()

        # Simulate high frustration
        behavior = BehaviorMetrics(total_turns=10, turns_with_tools=5, frustration_count=8)
        router.set_behavior(behavior)

        # Should switch to conservative strategy
        assert router._strategy == BehaviorRouterStrategy.CONSERVATIVE

    def test_conservative_strategy_on_high_error_rate(self):
        """Verify conservative strategy on high error rate."""
        router = BehaviorAwareRouter()

        # Simulate high error rate
        behavior = BehaviorMetrics(
            total_turns=10,
            turns_with_tools=5,
            tool_call_count=10,
            tool_error_count=5,  # 50% error rate (above 40% threshold)
        )
        router.set_behavior(behavior)

        # Should switch to conservative strategy
        assert router._strategy == BehaviorRouterStrategy.CONSERVATIVE

    def test_aggressive_strategy_on_low_metrics(self):
        """Verify aggressive strategy when all metrics are low."""
        router = BehaviorAwareRouter()

        # Simulate low metrics (satisfied user, low errors, high tool usage)
        behavior = BehaviorMetrics(
            total_turns=10,
            turns_with_tools=8,
            turns_without_tools_but_needed=0,
            tool_call_count=8,
            tool_error_count=0,
            frustration_count=0,
        )
        router.set_behavior(behavior)

        # Should switch to aggressive strategy
        assert router._strategy == BehaviorRouterStrategy.AGGRESSIVE

    def test_custom_config(self):
        """Verify router uses custom configuration."""
        custom_config = BehaviorRoutingConfig(
            high_hallucination_threshold=0.8,
            high_frustration_threshold=0.6,
        )
        router = BehaviorAwareRouter(config=custom_config)

        # Set behavior that would trigger conservative strategy with default config
        behavior = BehaviorMetrics(
            total_turns=10,
            turns_with_tools=5,
            turns_without_tools_but_needed=7,  # 70% without tools when needed
        )

        router.set_behavior(behavior)

        # Should not trigger conservative with custom threshold (0.8)
        assert router._strategy == BehaviorRouterStrategy.STANDARD

    def test_behavior_impact_calculation(self):
        """Verify behavior impact calculation."""
        router = BehaviorAwareRouter()

        # Create behavior with known metrics
        behavior = BehaviorMetrics(
            total_turns=10,
            turns_with_tools=5,
            turns_without_tools_but_needed=5,  # 50% risk
            tool_call_count=5,
            tool_error_count=1,  # 20% error rate
            frustration_count=3,  # 30% frustration rate
        )

        router.set_behavior(behavior)

        impact = router.get_behavior_impact()
        assert "hallucination_impact" in impact
        assert "frustration_impact" in impact
        assert "error_impact" in impact
        assert "total_impact" in impact

        # Verify impact calculation
        assert impact["total_impact"] > 0
        assert impact["frustration_impact"] > 0  # 30% * 0.8 = 24%
        assert impact["error_impact"] > 0  # 20% * 0.6 = 12%

    def test_reset_stats(self):
        """Verify stats can be reset."""
        router = BehaviorAwareRouter()

        # Route some queries
        router.route("写一个函数")
        router.route("分析代码")

        stats_before = router.get_stats()
        assert stats_before.total_routed == 2

        # Reset stats
        router.reset_stats()

        stats_after = router.get_stats()
        assert stats_after.total_routed == 0

    def test_config_update(self):
        """Verify configuration can be updated."""
        router = BehaviorAwareRouter()

        new_config = BehaviorRoutingConfig(
            high_hallucination_threshold=0.9,
            default_strategy=BehaviorRouterStrategy.AGGRESSIVE,
        )

        router.update_config(new_config)

        config = router.get_config()
        assert config.high_hallucination_threshold == 0.9
        assert config.default_strategy == BehaviorRouterStrategy.AGGRESSIVE


class TestBehaviorRouterStrategy:
    """Test behavior router strategy enumeration."""

    def test_strategy_values(self):
        """Verify strategy enum values."""
        assert BehaviorRouterStrategy.STANDARD.value == "standard"
        assert BehaviorRouterStrategy.CONSERVATIVE.value == "conservative"
        assert BehaviorRouterStrategy.AGGRESSIVE.value == "aggressive"


class TestBehaviorRoutingConfig:
    """Test behavior routing configuration."""

    def test_default_config(self):
        """Verify default configuration values."""
        config = BehaviorRoutingConfig()

        assert config.high_hallucination_threshold == 0.7
        assert config.medium_hallucination_threshold == 0.3
        assert config.high_frustration_threshold == 0.5
        assert config.medium_frustration_threshold == 0.2
        assert config.high_error_threshold == 0.4
        assert config.medium_error_threshold == 0.2
        assert config.hallucination_model_priority == 1.0
        assert config.frustration_model_priority == 0.8
        assert config.error_model_priority == 0.6
        assert config.default_strategy == BehaviorRouterStrategy.STANDARD

    def test_custom_config_values(self):
        """Verify custom configuration values."""
        config = BehaviorRoutingConfig(
            high_hallucination_threshold=0.8,
            high_frustration_threshold=0.6,
            high_error_threshold=0.5,
        )

        assert config.high_hallucination_threshold == 0.8
        assert config.high_frustration_threshold == 0.6
        assert config.high_error_threshold == 0.5
