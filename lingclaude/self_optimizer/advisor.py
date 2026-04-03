from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from lingclaude.self_optimizer.optimizer import OptimizationResult


class OptimizationAdvisor:
    def __init__(self) -> None:
        self.goal_names = {
            "structure": "Structure Optimization",
            "performance": "Performance Optimization",
            "simplicity": "Simplicity Optimization",
        }

    def generate_report(
        self,
        goal: str,
        target: str,
        current_metrics: dict[str, Any],
        optimization_result: OptimizationResult,
    ) -> str:
        lines = [
            "# LingClaude Self-Optimization Report",
            "",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Goal: {self._goal_name(goal)}",
            f"Target: {target}",
            "",
            "---",
            "",
            "## Current State",
            "",
        ]

        lines.extend(self._format_metrics(current_metrics, goal))
        lines.extend(self._format_issues(current_metrics, goal))
        lines.extend(
            self._format_recommendations(current_metrics, optimization_result, goal)
        )
        lines.extend(self._format_comparison(current_metrics, optimization_result))
        lines.extend(self._format_steps(optimization_result.best_params))

        if optimization_result.history:
            lines.extend(self._format_history(optimization_result))

        return "\n".join(lines)

    def _goal_name(self, goal: str) -> str:
        return self.goal_names.get(goal, goal)

    def _format_metrics(
        self, metrics: dict[str, Any], goal: str
    ) -> list[str]:
        lines = ["### Metrics", ""]

        if "review_score" in metrics:
            lines.append(f"- Overall score: {metrics['review_score']}/100")

        if goal == "structure":
            for key, label in [
                ("structure_violations", "Structure violations"),
                ("avg_class_size", "Avg class size (lines)"),
                ("avg_method_count", "Avg method count"),
                ("avg_complexity", "Cyclomatic complexity"),
                ("large_classes_count", "Large classes"),
            ]:
                if key in metrics:
                    val = metrics[key]
                    if isinstance(val, float):
                        lines.append(f"- {label}: {val:.1f}")
                    else:
                        lines.append(f"- {label}: {val}")

        lines.append("")
        return lines

    def _format_issues(
        self, metrics: dict[str, Any], goal: str
    ) -> list[str]:
        lines = ["### Issues Found", ""]
        issues: list[str] = []

        if goal == "structure":
            if metrics.get("large_classes_count", 0) > 0:
                issues.append(
                    f"- {metrics['large_classes_count']} large classes detected"
                )
            if metrics.get("complex_methods_count", 0) > 0:
                issues.append(
                    f"- {metrics['complex_methods_count']} complex methods detected"
                )
            if metrics.get("structure_violations", 0) > 0:
                issues.append(f"- {metrics['structure_violations']} structure violations")

        if not issues:
            issues.append("- No critical issues found")

        lines.extend(issues)
        lines.append("")
        return lines

    def _format_recommendations(
        self,
        current_metrics: dict[str, Any],
        result: OptimizationResult,
        goal: str,
    ) -> list[str]:
        lines = [
            "## Recommendations",
            "",
            "### Optimal Parameters",
            "",
            "```yaml",
        ]

        for key, value in sorted(result.best_params.items()):
            if isinstance(value, float):
                lines.append(f"{key}: {value:.2f}")
            else:
                lines.append(f"{key}: {value}")

        lines.extend(["```", ""])
        lines.extend(
            [
                f"**Experiments**: {result.experiments}",
                f"**Duration**: {result.duration:.1f}s",
                "",
            ]
        )
        return lines

    def _format_comparison(
        self, current_metrics: dict[str, Any], result: OptimizationResult
    ) -> list[str]:
        lines = [
            "### Parameter Comparison",
            "",
            "| Parameter | Recommended |",
            "|-----------|-------------|",
        ]

        for key in sorted(result.best_params.keys()):
            val = result.best_params[key]
            if isinstance(val, float):
                lines.append(f"| {key} | {val:.2f} |")
            else:
                lines.append(f"| {key} | {val} |")

        lines.append("")
        return lines

    def _format_steps(self, best_params: dict[str, Any]) -> list[str]:
        lines = [
            "## Implementation Steps",
            "",
            "1. Update `config.yaml` with the recommended parameters above",
            "2. Run `lingclaude optimize --target <path>` to verify",
            "3. Commit the configuration changes",
            "",
            "---",
            "",
        ]
        return lines

    def _format_history(self, result: OptimizationResult) -> list[str]:
        lines = [
            "## Optimization History",
            "",
            "| # | Score |",
            "|---|-------|",
        ]

        for i, entry in enumerate(result.history[:10]):
            score = entry.get("score", 0)
            lines.append(f"| {i + 1} | {score:.2f} |")

        if len(result.history) > 10:
            lines.append(f"| ... | ({len(result.history)} total experiments) |")

        lines.extend(["", "---", "", "*Report generated by LingClaude*", ""])
        return lines

    def save_report(self, report: str, output_path: str | None = None) -> str:
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = f"lingclaude_optimization_report_{timestamp}.md"

        Path(output_path).write_text(report, encoding="utf-8")
        return output_path
