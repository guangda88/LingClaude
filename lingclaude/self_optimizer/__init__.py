from __future__ import annotations

from lingclaude.self_optimizer.trigger import OptimizationTrigger, TriggerInfo
from lingclaude.self_optimizer.evaluator import StructureEvaluator, StructureMetrics, fallback_evaluate
from lingclaude.self_optimizer.optimizer import (
    SynchronousOptimizer,
    SimpleSearchSpace,
    OptimizationRequest,
    OptimizationResult,
)
from lingclaude.self_optimizer.advisor import OptimizationAdvisor
from lingclaude.self_optimizer.daemon import OptimizationDaemon, DaemonState, OptimizationCycle

__all__ = [
    "OptimizationTrigger",
    "TriggerInfo",
    "StructureEvaluator",
    "StructureMetrics",
    "fallback_evaluate",
    "SynchronousOptimizer",
    "SimpleSearchSpace",
    "OptimizationRequest",
    "OptimizationResult",
    "OptimizationAdvisor",
    "OptimizationDaemon",
    "DaemonState",
    "OptimizationCycle",
]


def quick_optimize(
    target: str = ".", goal: str = "structure", max_trials: int = 20
) -> OptimizationResult:
    request = OptimizationRequest(
        target=target,
        goal=goal,
        params={},
        config={"max_experiments": max_trials},
    )
    optimizer = SynchronousOptimizer()
    return optimizer.optimize(request)


def check_and_optimize(
    context: dict, target: str = ".", goal: str = "structure"
) -> tuple[bool, OptimizationResult | None]:
    trigger = OptimizationTrigger()
    should_trigger, trigger_info = trigger.check_all_conditions(context)

    if not should_trigger:
        return False, None

    result = quick_optimize(target, goal)
    return True, result
