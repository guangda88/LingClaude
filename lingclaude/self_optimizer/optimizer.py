from __future__ import annotations

import time
import traceback
from dataclasses import dataclass
from typing import Any

from lingminopt import (
    MinimalOptimizer,
    SearchSpace,
    ExperimentConfig,
)
from lingminopt.core.models import OptimizationResult as LMOptResult


@dataclass(frozen=True)
class OptimizationRequest:
    target: str
    goal: str
    params: dict[str, Any]
    config: dict[str, Any]


@dataclass(frozen=True)
class OptimizationResult:
    success: bool
    best_params: dict[str, Any]
    best_score: float
    experiments: int
    duration: float
    error: str = ""
    history: tuple[dict[str, Any], ...] = ()


def _build_search_space(goal: str) -> SearchSpace:
    space = SearchSpace()

    if goal == "structure":
        space.add_discrete("max_class_size", [100, 200, 300, 500])
        space.add_discrete("max_method_count", [10, 15, 20, 25])
        space.add_discrete("max_complexity", [5, 10, 15, 20])
        space.add_discrete("max_nesting_depth", [3, 4, 5, 6])
        space.add_continuous("coupling_limit", 5.0, 15.0)
    elif goal == "performance":
        space.add_discrete("cache_size", [10, 50, 100, 500])
        space.add_discrete("parallelism", [1, 2, 4])
        space.add_discrete("timeout", [5, 10, 30, 60])
    elif goal == "simplicity":
        space.add_discrete("complexity_threshold", [5, 10, 15])
        space.add_discrete("duplication_penalty", [0.5, 1.0, 2.0])
        space.add_discrete("max_line_length", [80, 100, 120])

    return space


def _convert_result(lm_result: LMOptResult, duration: float) -> OptimizationResult:
    history: list[dict[str, Any]] = []
    for exp in lm_result.history:
        history.append({
            "experiment_id": exp.experiment_id,
            "params": exp.params,
            "score": exp.score,
        })

    return OptimizationResult(
        success=True,
        best_params=lm_result.best_params,
        best_score=lm_result.best_score,
        experiments=lm_result.total_experiments,
        duration=duration,
        history=tuple(history),
    )


class SynchronousOptimizer:
    def optimize(self, request: OptimizationRequest) -> OptimizationResult:
        start = time.monotonic()
        try:
            from lingclaude.self_optimizer.evaluator import StructureEvaluator

            evaluator = StructureEvaluator(request.target)
            space = _build_search_space(request.goal)

            strategy = request.config.get("strategy", "random")
            max_trials = request.config.get("max_experiments", 20)

            experiment_config = ExperimentConfig(
                max_experiments=max_trials,
                direction="minimize",
                early_stopping_patience=10,
                time_budget=300.0,
            )

            opt = MinimalOptimizer(
                evaluate=evaluator.evaluate,
                search_space=space,
                config=experiment_config,
                search_strategy=strategy,
                seed=42,
            )

            lm_result = opt.run()
            duration = time.monotonic() - start
            return _convert_result(lm_result, duration)

        except Exception as e:
            return OptimizationResult(
                success=False,
                best_params={},
                best_score=0,
                experiments=0,
                duration=time.monotonic() - start,
                error=str(e) + "\n" + traceback.format_exc(),
            )
