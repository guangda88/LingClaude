"""Behavior-Aware Model Router for GLM Token Optimization.

This module integrates with lingclaude's behavior metrics to dynamically
adjust model selection based on hallucination risk, user frustration,
and error rates.

Key Features:
1. Hallucination-aware routing: Force GLM-5.1 when risk is high
2. Frustration-aware routing: Prioritize accuracy when user is frustrated
3. Error-aware routing: Reduce complexity when errors are frequent
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from lingclaude.core.behavior import BehaviorMetrics
from lingclaude.model.intelligent_router import (
    GLMModel,
    IntelligentRouter,
    RoutingDecision,
    RoutingStats,
    TaskComplexity,
)

logger = logging.getLogger(__name__)


class BehaviorRouterStrategy(str, Enum):
    """Behavior-aware routing strategies."""
    STANDARD = "standard"  # Use standard intelligent routing
    CONSERVATIVE = "conservative"  # Prioritize accuracy over efficiency
    AGGRESSIVE = "aggressive"  # Prioritize efficiency over accuracy


@dataclass
class BehaviorRoutingConfig:
    """Configuration for behavior-aware routing."""

    # Hallucination thresholds
    high_hallucination_threshold: float = 0.7  # Force GLM-5.1 when above
    medium_hallucination_threshold: float = 0.3  # Adjust routing when above

    # Frustration thresholds
    high_frustration_threshold: float = 0.5  # Prioritize accuracy when above
    medium_frustration_threshold: float = 0.2  # Adjust routing when above

    # Error rate thresholds
    high_error_threshold: float = 0.4  # Reduce complexity when above
    medium_error_threshold: float = 0.2  # Adjust routing when above

    # Model selection priorities
    hallucination_model_priority: float = 1.0  # Weight for hallucination risk
    frustration_model_priority: float = 0.8  # Weight for frustration
    error_model_priority: float = 0.6  # Weight for error rate

    # Default strategy
    default_strategy: BehaviorRouterStrategy = BehaviorRouterStrategy.STANDARD


class BehaviorAwareRouter:
    """Router that adjusts model selection based on behavior metrics."""

    def __init__(
        self,
        config: Optional[BehaviorRoutingConfig] = None,
        base_router: Optional[IntelligentRouter] = None,
    ) -> None:
        """Initialize behavior-aware router.

        Args:
            config: Routing configuration
            base_router: Base intelligent router instance
        """
        self.config = config or BehaviorRoutingConfig()
        self.base_router = base_router or IntelligentRouter()
        self._behavior: Optional[BehaviorMetrics] = None
        self._strategy = BehaviorRouterStrategy.STANDARD
        self._stats = RoutingStats()

    def set_behavior(self, behavior: BehaviorMetrics) -> None:
        """Set current behavior metrics.

        Args:
            behavior: Current behavior metrics
        """
        self._behavior = behavior
        self._update_strategy()

    def _update_strategy(self) -> None:
        """Update routing strategy based on behavior metrics."""
        if self._behavior is None:
            self._strategy = BehaviorRouterStrategy.STANDARD
            return

        # Check for high risk conditions
        if (
            self._behavior.hallucination_risk > self.config.high_hallucination_threshold
            or self._behavior.frustration_rate > self.config.high_frustration_threshold
            or self._behavior.tool_error_rate > self.config.high_error_threshold
        ):
            self._strategy = BehaviorRouterStrategy.CONSERVATIVE
            logger.info(
                "Router switched to CONSERVATIVE strategy: "
                f"hallucination={self._behavior.hallucination_risk:.2f}, "
                f"frustration={self._behavior.frustration_rate:.2f}, "
                f"errors={self._behavior.tool_error_rate:.2f}"
            )
        elif (
            self._behavior.hallucination_risk < self.config.medium_hallucination_threshold
            and self._behavior.frustration_rate < self.config.medium_frustration_threshold
            and self._behavior.tool_error_rate < self.config.medium_error_threshold
        ):
            self._strategy = BehaviorRouterStrategy.AGGRESSIVE
            logger.info(
                "Router switched to AGGRESSIVE strategy: "
                "all metrics below medium thresholds"
            )
        else:
            self._strategy = BehaviorRouterStrategy.STANDARD
            logger.debug("Router using STANDARD strategy")

    def route(self, query: str) -> RoutingDecision:
        """Route query to appropriate model based on behavior.

        Args:
            query: User query

        Returns:
            Routing decision with selected model and complexity
        """
        # Get base routing decision
        base_decision = self.base_router.route(query)

        # Apply behavior-aware adjustments
        adjusted_decision = self._apply_behavior_adjustments(base_decision, query)

        # Update stats (need to recreate since it's frozen)
        new_stats = RoutingStats(
            total_routed=self._stats.total_routed + 1,
            glm_4_7_count=self._stats.glm_4_7_count + (1 if adjusted_decision.model == GLMModel.GLM_4_7 else 0),
            glm_5_1_count=self._stats.glm_5_1_count + (1 if adjusted_decision.model == GLMModel.GLM_5_1 else 0),
            glm_5_count=self._stats.glm_5_count,
            simple_count=self._stats.simple_count + (1 if adjusted_decision.complexity == TaskComplexity.SIMPLE else 0),
            medium_count=self._stats.medium_count + (1 if adjusted_decision.complexity == TaskComplexity.MEDIUM else 0),
            complex_count=self._stats.complex_count + (1 if adjusted_decision.complexity == TaskComplexity.COMPLEX else 0),
        )
        self._stats = new_stats

        return adjusted_decision

    def _apply_behavior_adjustments(
        self,
        base_decision: RoutingDecision,
        query: str,
    ) -> RoutingDecision:
        """Apply behavior-aware adjustments to base decision.

        Args:
            base_decision: Base routing decision
            query: User query

        Returns:
            Adjusted routing decision
        """
        if self._behavior is None or self._strategy == BehaviorRouterStrategy.STANDARD:
            return base_decision

        # CONSERVATIVE strategy: prioritize accuracy
        if self._strategy == BehaviorRouterStrategy.CONSERVATIVE:
            # Force GLM-5.1 for complex tasks
            if base_decision.complexity != TaskComplexity.SIMPLE:
                logger.debug("Routing to GLM-5.1 for complex task (CONSERVATIVE mode)")
                return RoutingDecision(
                    model=GLMModel.GLM_5_1,
                    task_type=base_decision.task_type,
                    complexity=base_decision.complexity,
                    reasoning="CONSERVATIVE: prioritize accuracy for complex task",
                )

            # Force GLM-5.1 when hallucination risk is high
            if self._behavior.hallucination_risk > self.config.high_hallucination_threshold:
                logger.debug("Routing to GLM-5.1 (high hallucination risk)")
                return RoutingDecision(
                    model=GLMModel.GLM_5_1,
                    task_type=base_decision.task_type,
                    complexity=base_decision.complexity,
                    reasoning="CONSERVATIVE: high hallucination risk",
                )

            # Force GLM-5.1 when user is frustrated
            if self._behavior.frustration_rate > self.config.high_frustration_threshold:
                logger.debug("Routing to GLM-5.1 (high frustration rate)")
                return RoutingDecision(
                    model=GLMModel.GLM_5_1,
                    task_type=base_decision.task_type,
                    complexity=base_decision.complexity,
                    reasoning="CONSERVATIVE: high frustration rate",
                )

        # AGGRESSIVE strategy: prioritize efficiency
        elif self._strategy == BehaviorRouterStrategy.AGGRESSIVE:
            # Force GLM-4.7 even for medium complexity tasks
            if base_decision.complexity == TaskComplexity.MEDIUM:
                logger.debug("Routing to GLM-4.7 for medium task (AGGRESSIVE mode)")
                return RoutingDecision(
                    model=GLMModel.GLM_4_7,
                    task_type=base_decision.task_type,
                    complexity=TaskComplexity.SIMPLE,
                    reasoning="AGGRESSIVE: optimize efficiency",
                )

        return base_decision

    def get_strategy(self) -> BehaviorRouterStrategy:
        """Get current routing strategy.

        Returns:
            Current routing strategy
        """
        return self._strategy

    def get_stats(self) -> RoutingStats:
        """Get routing statistics.

        Returns:
            Current routing statistics
        """
        return self._stats

    def reset_stats(self) -> None:
        """Reset routing statistics."""
        self._stats = RoutingStats()

    def get_config(self) -> BehaviorRoutingConfig:
        """Get routing configuration.

        Returns:
            Current routing configuration
        """
        return self._config

    def update_config(self, config: BehaviorRoutingConfig) -> None:
        """Update routing configuration.

        Args:
            config: New configuration
        """
        self._config = config
        self._update_strategy()
        logger.info("Behavior router configuration updated")

    def get_behavior_impact(self) -> dict[str, float]:
        """Get the impact of behavior metrics on routing.

        Returns:
            Dictionary of behavior metric impacts
        """
        if self._behavior is None:
            return {}

        return {
            "hallucination_impact": (
                self._behavior.hallucination_risk
                * self.config.hallucination_model_priority
            ),
            "frustration_impact": (
                self._behavior.frustration_rate
                * self.config.frustration_model_priority
            ),
            "error_impact": (
                self._behavior.tool_error_rate
                * self.config.error_model_priority
            ),
            "total_impact": (
                self._behavior.hallucination_risk * self.config.hallucination_model_priority
                + self._behavior.frustration_rate * self.config.frustration_model_priority
                + self._behavior.tool_error_rate * self.config.error_model_priority
            ),
        }
