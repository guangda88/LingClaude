from __future__ import annotations

import random
import time
import traceback
from dataclasses import dataclass, field
from typing import Any


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


class SimpleSearchSpace:
    def __init__(self) -> None:
        self._rng = random.Random(42)
        self.parameters: dict[str, tuple[str, Any, ...]] = {}

    def add_discrete(self, name: str, choices: list) -> None:
        self.parameters[name] = ("discrete", choices)

    def add_continuous(self, name: str, min_val: float, max_val: float) -> None:
        self.parameters[name] = ("continuous", min_val, max_val)

    def sample(self) -> dict[str, Any]:
        sampled: dict[str, Any] = {}
        for name, param in self.parameters.items():
            if param[0] == "discrete":
                sampled[name] = self._rng.choice(param[1])
            else:
                sampled[name] = self._rng.uniform(param[1], param[2])
        return sampled


def _create_search_space(goal: str) -> SimpleSearchSpace:
    search_space = SimpleSearchSpace()

    if goal == "structure":
        search_space.add_discrete("max_class_size", [100, 200, 300, 500])
        search_space.add_discrete("max_method_count", [10, 15, 20, 25])
        search_space.add_discrete("max_complexity", [5, 10, 15, 20])
        search_space.add_discrete("max_nesting_depth", [3, 4, 5, 6])
        search_space.add_continuous("coupling_limit", 5.0, 15.0)
    elif goal == "performance":
        search_space.add_discrete("cache_size", [10, 50, 100, 500])
        search_space.add_discrete("parallelism", [1, 2, 4])
        search_space.add_discrete("timeout", [5, 10, 30, 60])
    elif goal == "simplicity":
        search_space.add_discrete("complexity_threshold", [5, 10, 15])
        search_space.add_discrete("duplication_penalty", [0.5, 1.0, 2.0])
        search_space.add_discrete("max_line_length", [80, 100, 120])

    return search_space


def _grid_search(
    search_space: SimpleSearchSpace,
    target_path: str,
    max_experiments: int,
) -> OptimizationResult:
    from lingclaude.self_optimizer.evaluator import StructureEvaluator

    evaluator = StructureEvaluator(target_path)
    best_score = float("inf")
    best_params: dict[str, Any] = {}
    history: list[dict[str, Any]] = []

    for i in range(max_experiments):
        params = search_space.sample()
        score = evaluator.evaluate(params)
        history.append({"experiment_id": i, "params": params, "score": score})

        if score < best_score:
            best_score = score
            best_params = params

    return OptimizationResult(
        success=True,
        best_params=best_params,
        best_score=best_score,
        experiments=max_experiments,
        duration=0,
        history=history,
    )


class SynchronousOptimizer:
    def optimize(self, request: OptimizationRequest) -> OptimizationResult:
        start = time.monotonic()
        try:
            search_space = _create_search_space(request.goal)
            max_experiments = request.config.get("max_experiments", 20)

            try:
                import optuna

                direction = "minimize"
                study = optuna.create_study(direction=direction)

                def objective(trial: Any) -> float:
                    params: dict[str, Any] = {}
                    for name, spec in search_space.parameters.items():
                        if spec[0] == "discrete":
                            params[name] = trial.suggest_categorical(name, spec[1])
                        else:
                            params[name] = trial.suggest_float(name, spec[1], spec[2])

                    from lingclaude.self_optimizer.evaluator import StructureEvaluator

                    evaluator = StructureEvaluator(request.target)
                    return evaluator.evaluate(params)

                study.optimize(objective, n_trials=max_experiments)

                duration = time.monotonic() - start
                return OptimizationResult(
                    success=True,
                    best_params=study.best_params,
                    best_score=study.best_value,
                    experiments=len(study.trials),
                    duration=duration,
                )

            except ImportError:
                result = _grid_search(
                    search_space, request.target, max_experiments
                )
                result.duration = time.monotonic() - start
                return result

        except Exception as e:
            return OptimizationResult(
                success=False,
                best_params={},
                best_score=0,
                experiments=0,
                duration=time.monotonic() - start,
                error=str(e) + "\n" + traceback.format_exc(),
            )
