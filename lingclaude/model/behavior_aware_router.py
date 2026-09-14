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
from dataclasses import dataclass, field
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

# P9: PolicyLoader 顶层导入（G3: 避免函数内 import 增长）
from lingclaude.core.policy_loader import get as _policy_get


class BehaviorRouterStrategy(str, Enum):
    """Behavior-aware routing strategies."""
    STANDARD = "standard"  # Use standard intelligent routing
    CONSERVATIVE = "conservative"  # Prioritize accuracy over efficiency
    AGGRESSIVE = "aggressive"  # Prioritize efficiency over accuracy


def _load_policy() -> dict:
    """从策略文件加载阈值（灵元：策略是 data，不是结构）。

    走 PolicyLoader 统一加载（P1-1）：支持 mtime watch 热更，改
    behavior_router.yaml 后下个 turn 生效，进程不重启。
    读失败回退内置默认（graceful degrade），默认值与 YAML 完全一致。
    """
    return _policy_get("behavior_router")


def _policy_float(policy: dict, section: str, key: str, default: float) -> float:
    """从策略数据取 float，缺失回退默认。"""
    try:
        return float(policy[section][key])
    except (KeyError, TypeError, ValueError):
        return default


@dataclass
class BehaviorRoutingConfig:
    """Configuration for behavior-aware routing.

    阈值来自策略文件（lingclaude/core/policies/behavior_router.yaml）。
    - 未显式传值的字段 → 读策略文件（缺失回退内置默认，graceful degrade）
    - 显式传值的字段 → 保留调用方值，不被 YAML 覆盖
    改阈值只改 YAML，不动代码。
    """

    # 字段默认 None 表示"未显式设置"，由 __post_init__ 从策略文件填充
    high_hallucination_threshold: Optional[float] = None  # Force GLM-5.1 when above
    medium_hallucination_threshold: Optional[float] = None  # Adjust routing when above
    high_frustration_threshold: Optional[float] = None  # Prioritize accuracy when above
    medium_frustration_threshold: Optional[float] = None  # Adjust routing when above
    high_error_threshold: Optional[float] = None  # Reduce complexity when above
    medium_error_threshold: Optional[float] = None  # Adjust routing when above
    hallucination_model_priority: Optional[float] = None  # Weight for hallucination
    frustration_model_priority: Optional[float] = None  # Weight for frustration
    error_model_priority: Optional[float] = None  # Weight for error rate
    default_strategy: Optional[BehaviorRouterStrategy] = None

    # 策略数据（P9: 调用时实时读 —— PolicyLoader 内部缓存 + mtime watch 热更；
    # 不再模块级/实例级一次性加载，改 YAML 后新构造的 config 立即用新值）
    _policy: dict = field(default_factory=_load_policy, repr=False, compare=False)

    # 内置默认（YAML 缺失或未设置时回退，与 YAML 值一致）
    _DEFAULTS: dict = field(
        default_factory=lambda: {
            "high_hallucination_threshold": 0.7,
            "medium_hallucination_threshold": 0.3,
            "high_frustration_threshold": 0.5,
            "medium_frustration_threshold": 0.2,
            "high_error_threshold": 0.4,
            "medium_error_threshold": 0.2,
            "hallucination_model_priority": 1.0,
            "frustration_model_priority": 0.8,
            "error_model_priority": 0.6,
        },
        repr=False,
        compare=False,
    )

    # YAML 路径映射（policy 文件 section/key → 字段名）
    _POLICY_MAP: tuple = (
        ("hallucination", "high_threshold", "high_hallucination_threshold"),
        ("hallucination", "medium_threshold", "medium_hallucination_threshold"),
        ("frustration", "high_threshold", "high_frustration_threshold"),
        ("frustration", "medium_threshold", "medium_frustration_threshold"),
        ("error_rate", "high_threshold", "high_error_threshold"),
        ("error_rate", "medium_threshold", "medium_error_threshold"),
        ("model_priority", "hallucination", "hallucination_model_priority"),
        ("model_priority", "frustration", "frustration_model_priority"),
        ("model_priority", "error", "error_model_priority"),
    )

    def __post_init__(self) -> None:
        """填充未显式设置的字段：策略文件 → 内置默认。

        P9: 记录显式传值的字段（构造时非 None 即显式），
        hot_reload/_apply_policy 只跳过显式字段，策略填充字段可被热更。
        """
        # 构造时非 None 的字段 = 调用方显式设置（default 均为 None）
        self._explicit_fields = {
            attr
            for _, _, attr in self._POLICY_MAP
            if getattr(self, attr) is not None
        }
        if self.default_strategy is not None:
            self._explicit_fields.add("default_strategy")
        self._apply_policy(self._policy or {})

    def _apply_policy(self, p: dict) -> None:
        """按策略数据填充未显式设置的字段（P9: 可被 hot_reload 复用）。"""
        for section, key, attr in self._POLICY_MAP:
            # 显式设置 → 保留（不被 YAML 覆盖）
            if attr in self._explicit_fields:
                continue
            # 策略文件 → 内置默认
            try:
                setattr(self, attr, float(p[section][key]))
            except (KeyError, TypeError, ValueError):
                setattr(self, attr, self._DEFAULTS[attr])
        # default_strategy 特殊处理（枚举）
        if "default_strategy" not in self._explicit_fields:
            try:
                strat = str(p.get("default_strategy", "")).lower()
                self.default_strategy = BehaviorRouterStrategy(strat)
            except (ValueError, TypeError):
                self.default_strategy = BehaviorRouterStrategy.STANDARD

    def hot_reload(self) -> bool:
        """强制重读策略文件并刷新未显式设置字段（P9: 已有实例热更）。

        只重填未显式设置的字段（显式传值的保留调用方意图）。
        返回是否有字段值发生变化（供调用方决定是否需要响应）。
        """
        before = {
            attr: getattr(self, attr)
            for _, _, attr in self._POLICY_MAP
        }
        before["default_strategy"] = self.default_strategy
        self._policy = _policy_get("behavior_router")
        self._apply_policy(self._policy or {})
        after = {
            attr: getattr(self, attr)
            for _, _, attr in self._POLICY_MAP
        }
        after["default_strategy"] = self.default_strategy
        return before != after


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
        # P9 修复: get_config/update_config 用 self._config，但 __init__ 只设了
        # self.config —— 导致 get_config() AttributeError、update_config 不生效。
        # 统一为 self._config，self.config 保留为兼容别名（route 走 _config）。
        self._config = self.config
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
            self._behavior.hallucination_risk > self._config.high_hallucination_threshold
            or self._behavior.frustration_rate > self._config.high_frustration_threshold
            or self._behavior.tool_error_rate > self._config.high_error_threshold
        ):
            self._strategy = BehaviorRouterStrategy.CONSERVATIVE
            logger.info(
                "Router switched to CONSERVATIVE strategy: "
                f"hallucination={self._behavior.hallucination_risk:.2f}, "
                f"frustration={self._behavior.frustration_rate:.2f}, "
                f"errors={self._behavior.tool_error_rate:.2f}"
            )
        elif (
            self._behavior.hallucination_risk < self._config.medium_hallucination_threshold
            and self._behavior.frustration_rate < self._config.medium_frustration_threshold
            and self._behavior.tool_error_rate < self._config.medium_error_threshold
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
        # P9: 每次路由前检查策略热更（PolicyLoader mtime watch 节流 30s，
        # 开销可忽略；改 behavior_router.yaml 后下个 turn 生效，进程不重启）
        self._config.hot_reload()

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
            if self._behavior.hallucination_risk > self._config.high_hallucination_threshold:
                logger.debug("Routing to GLM-5.1 (high hallucination risk)")
                return RoutingDecision(
                    model=GLMModel.GLM_5_1,
                    task_type=base_decision.task_type,
                    complexity=base_decision.complexity,
                    reasoning="CONSERVATIVE: high hallucination risk",
                )

            # Force GLM-5.1 when user is frustrated
            if self._behavior.frustration_rate > self._config.high_frustration_threshold:
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
                * self._config.hallucination_model_priority
            ),
            "frustration_impact": (
                self._behavior.frustration_rate
                * self._config.frustration_model_priority
            ),
            "error_impact": (
                self._behavior.tool_error_rate
                * self._config.error_model_priority
            ),
            "total_impact": (
                self._behavior.hallucination_risk * self._config.hallucination_model_priority
                + self._behavior.frustration_rate * self._config.frustration_model_priority
                + self._behavior.tool_error_rate * self._config.error_model_priority
            ),
        }
