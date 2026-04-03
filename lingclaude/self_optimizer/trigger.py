from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from lingclaude.core.config import TriggerConfig


@dataclass(frozen=True)
class TriggerInfo:
    type: str
    reason: str
    priority: str
    current_value: Any
    threshold: Any
    metrics: dict[str, Any]


class OptimizationTrigger:
    def __init__(self, config: TriggerConfig | None = None):
        self.config = config or TriggerConfig()

    def check_all_conditions(
        self, context: dict[str, Any]
    ) -> tuple[bool, TriggerInfo | None]:
        if not self.config.enabled:
            return False, None

        if context.get("user_triggered"):
            return True, TriggerInfo(
                type="user",
                reason="User manually triggered optimization",
                priority="high",
                current_value=None,
                threshold=None,
                metrics={},
            )

        triggers_found: list[TriggerInfo] = []

        checks = [
            self._check_quality,
            self._check_behavior,
            self._check_structure,
            self._check_performance,
            self._check_scale,
            self._check_tech_debt,
            self._check_time,
        ]

        for check in checks:
            result = check(context)
            if result is not None:
                triggers_found.append(result)

        if triggers_found:
            priority_order = {"high": 3, "medium": 2, "low": 1}
            triggers_found.sort(
                key=lambda x: priority_order.get(x.priority, 0), reverse=True
            )
            return True, triggers_found[0]

        return False, None

    def _check_quality(self, context: dict[str, Any]) -> TriggerInfo | None:
        score = context.get("review_score", 100)
        threshold = self.config.review_score_threshold

        if score < threshold:
            return TriggerInfo(
                type="quality",
                reason=f"Review score ({score}) below threshold ({threshold})",
                priority="high" if score < threshold // 2 else "medium",
                current_value=score,
                threshold=threshold,
                metrics={"review_score": score},
            )

        coverage_drop = context.get("coverage_drop", 0)
        if coverage_drop > 5:
            return TriggerInfo(
                type="quality",
                reason=f"Coverage dropped {coverage_drop}%",
                priority="medium",
                current_value=coverage_drop,
                threshold=5,
                metrics={"coverage_drop": coverage_drop},
            )

        failure_rate = context.get("test_failure_rate", 0)
        if failure_rate > 10:
            return TriggerInfo(
                type="quality",
                reason=f"Test failure rate ({failure_rate}%) exceeds threshold",
                priority="high",
                current_value=failure_rate,
                threshold=10,
                metrics={"test_failure_rate": failure_rate},
            )

        return None

    def _check_structure(self, context: dict[str, Any]) -> TriggerInfo | None:
        complexity = context.get("avg_complexity", 0)
        if complexity > self.config.max_complexity:
            return TriggerInfo(
                type="structure",
                reason=f"Avg complexity ({complexity}) exceeds threshold ({self.config.max_complexity})",
                priority="medium",
                current_value=complexity,
                threshold=self.config.max_complexity,
                metrics={"avg_complexity": complexity},
            )

        large_classes = context.get("large_classes_count", 0)
        if large_classes > 5:
            return TriggerInfo(
                type="structure",
                reason=f"Large class count ({large_classes}) exceeds threshold",
                priority="medium",
                current_value=large_classes,
                threshold=5,
                metrics={"large_classes_count": large_classes},
            )

        duplication = context.get("duplication_rate", 0)
        if duplication > 0.05:
            return TriggerInfo(
                type="structure",
                reason=f"Code duplication rate ({duplication * 100:.1f}%) too high",
                priority="low",
                current_value=duplication,
                threshold=0.05,
                metrics={"duplication_rate": duplication},
            )

        return None

    def _check_performance(self, context: dict[str, Any]) -> TriggerInfo | None:
        exec_time = context.get("execution_time", 0)
        baseline = context.get("baseline_time", exec_time)
        threshold = self.config.max_execution_time

        if baseline > 0 and exec_time > threshold:
            return TriggerInfo(
                type="performance",
                reason=f"Execution time ({exec_time:.2f}s) exceeds threshold ({threshold}s)",
                priority="high",
                current_value=exec_time,
                threshold=threshold,
                metrics={"execution_time": exec_time},
            )

        memory_mb = context.get("memory_usage_mb", 0)
        if memory_mb > 500:
            return TriggerInfo(
                type="performance",
                reason=f"Memory usage ({memory_mb:.1f}MB) exceeds 500MB threshold",
                priority="high",
                current_value=memory_mb,
                threshold=500,
                metrics={"memory_usage_mb": memory_mb},
            )

        return None

    def _check_scale(self, context: dict[str, Any]) -> TriggerInfo | None:
        new_lines = context.get("new_lines", 0)
        threshold = self.config.new_lines_threshold
        if new_lines > threshold:
            return TriggerInfo(
                type="scale",
                reason=f"New code ({new_lines} lines) exceeds threshold ({threshold})",
                priority="low",
                current_value=new_lines,
                threshold=threshold,
                metrics={"new_lines": new_lines},
            )

        new_files = context.get("new_files", 0)
        if new_files > 10:
            return TriggerInfo(
                type="scale",
                reason=f"New files ({new_files}) exceeds threshold (10)",
                priority="low",
                current_value=new_files,
                threshold=10,
                metrics={"new_files": new_files},
            )

        return None

    def _check_tech_debt(self, context: dict[str, Any]) -> TriggerInfo | None:
        todo_count = context.get("todo_count", 0)
        if todo_count > 20:
            return TriggerInfo(
                type="tech_debt",
                reason=f"TODO count ({todo_count}) exceeds threshold (20)",
                priority="low",
                current_value=todo_count,
                threshold=20,
                metrics={"todo_count": todo_count},
            )

        hack_count = context.get("hack_comments", 0)
        if hack_count > 3:
            return TriggerInfo(
                type="tech_debt",
                reason=f"HACK comments ({hack_count}) exceeds threshold (3)",
                priority="medium",
                current_value=hack_count,
                threshold=3,
                metrics={"hack_comments": hack_count},
            )

        return None

    def _check_behavior(self, context: dict[str, Any]) -> TriggerInfo | None:
        hallucination_risk = context.get("hallucination_risk", 0)
        if hallucination_risk > 0.3:
            return TriggerInfo(
                type="behavior",
                reason=f"幻觉风险过高 ({hallucination_risk:.0%}) — 回答代码问题时未使用工具",
                priority="high" if hallucination_risk > 0.5 else "medium",
                current_value=hallucination_risk,
                threshold=0.3,
                metrics={"hallucination_risk": hallucination_risk},
            )

        frustration_rate = context.get("frustration_rate", 0)
        if frustration_rate > 0.2:
            return TriggerInfo(
                type="behavior",
                reason=f"用户沮丧率过高 ({frustration_rate:.0%}) — 可能存在幻觉或不准确回答",
                priority="high",
                current_value=frustration_rate,
                threshold=0.2,
                metrics={"frustration_rate": frustration_rate},
            )

        corrections = context.get("corrections_received", 0)
        if corrections >= 2:
            return TriggerInfo(
                type="behavior",
                reason=f"收到 {corrections} 次用户纠正 — 需要优化回答质量",
                priority="medium",
                current_value=corrections,
                threshold=2,
                metrics={"corrections_received": corrections},
            )

        tool_error_rate = context.get("tool_error_rate", 0)
        if tool_error_rate > 0.3:
            return TriggerInfo(
                type="behavior",
                reason=f"工具调用失败率过高 ({tool_error_rate:.0%})",
                priority="medium",
                current_value=tool_error_rate,
                threshold=0.3,
                metrics={"tool_error_rate": tool_error_rate},
            )

        return None

    def _check_time(self, context: dict[str, Any]) -> TriggerInfo | None:
        last_opt_time = context.get("last_optimization_time")
        if last_opt_time:
            if isinstance(last_opt_time, str):
                last_opt_time = datetime.fromisoformat(last_opt_time)
            days_since = (datetime.now() - last_opt_time).days
            threshold = self.config.min_interval_hours // 24 or 7
            if days_since >= threshold:
                return TriggerInfo(
                    type="time",
                    reason=f"Last optimization was {days_since} days ago (threshold: {threshold}d)",
                    priority="low",
                    current_value=days_since,
                    threshold=threshold,
                    metrics={"days_since_last_optimization": days_since},
                )
        return None
