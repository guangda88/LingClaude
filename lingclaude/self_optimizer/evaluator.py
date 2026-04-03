from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


@dataclass
class StructureMetrics:
    total_classes: int
    large_classes: int
    total_methods: int
    complex_methods: int
    avg_complexity: float
    avg_class_size: float
    avg_method_count: float
    violations: int


class StructureEvaluator:
    def __init__(self, target_path: str = "."):
        self.target_path = Path(target_path)

    def evaluate(self, params: dict[str, Any]) -> float:
        metrics = self._analyze_structure(params)
        return float(metrics.violations)

    def _analyze_structure(self, params: dict[str, Any]) -> StructureMetrics:
        max_class_size = params.get("max_class_size", 200)
        max_method_count = params.get("max_method_count", 15)
        max_complexity = params.get("max_complexity", 10)

        total_classes = 0
        large_classes = 0
        total_methods = 0
        complex_methods = 0
        complexity_sum = 0
        class_sizes: list[int] = []
        method_counts: list[int] = []
        violations = 0

        for py_file in self.target_path.rglob("*.py"):
            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source)

                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        total_classes += 1
                        class_size = self._count_class_lines(node)
                        class_sizes.append(class_size)

                        if class_size > max_class_size:
                            large_classes += 1
                            violations += 1

                        method_count = len(
                            [n for n in node.body if isinstance(n, ast.FunctionDef)]
                        )
                        method_counts.append(method_count)
                        total_methods += method_count

                        if method_count > max_method_count:
                            violations += 1

                        for item in node.body:
                            if isinstance(item, ast.FunctionDef):
                                complexity = self._calculate_complexity(item)
                                complexity_sum += complexity

                                if complexity > max_complexity:
                                    complex_methods += 1
                                    violations += 1

            except (SyntaxError, UnicodeDecodeError, PermissionError, OSError) as e:
                logger.warning("Cannot parse %s: %s", py_file, e)
                continue

        avg_class_size = sum(class_sizes) / len(class_sizes) if class_sizes else 0
        avg_method_count = (
            sum(method_counts) / len(method_counts) if method_counts else 0
        )
        avg_complexity = complexity_sum / total_methods if total_methods > 0 else 0

        return StructureMetrics(
            total_classes=total_classes,
            large_classes=large_classes,
            total_methods=total_methods,
            complex_methods=complex_methods,
            avg_complexity=avg_complexity,
            avg_class_size=avg_class_size,
            avg_method_count=avg_method_count,
            violations=violations,
        )

    def _count_class_lines(self, class_node: ast.ClassDef) -> int:
        if not class_node.body:
            return 0
        start_line = class_node.lineno
        end_line = class_node.body[-1].end_lineno or start_line
        return end_line - start_line + 1

    def _calculate_complexity(self, func_node: ast.FunctionDef) -> int:
        complexity = 1
        for node in ast.walk(func_node):
            if isinstance(node, (ast.If, ast.While, ast.For, ast.ExceptHandler)):
                complexity += 1
            elif isinstance(node, ast.BoolOp):
                complexity += len(node.values) - 1
        return complexity

    def get_current_metrics(self) -> dict[str, Any]:
        default_params = {
            "max_class_size": 500,
            "max_method_count": 25,
            "max_complexity": 20,
        }
        metrics = self._analyze_structure(default_params)
        return {
            "structure_violations": metrics.violations,
            "large_classes_count": metrics.large_classes,
            "complex_methods_count": metrics.complex_methods,
            "avg_complexity": metrics.avg_complexity,
            "avg_class_size": metrics.avg_class_size,
            "avg_method_count": metrics.avg_method_count,
            "total_classes": metrics.total_classes,
            "total_methods": metrics.total_methods,
        }


def fallback_evaluate(params: dict[str, Any], target_path: str = ".") -> float:
    evaluator = StructureEvaluator(target_path)
    return evaluator.evaluate(params)
