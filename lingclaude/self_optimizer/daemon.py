from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from lingclaude.core.config import LingClaudeConfig, TriggerConfig, load_config
from lingclaude.self_optimizer.advisor import OptimizationAdvisor
from lingclaude.self_optimizer.evaluator import StructureEvaluator
from lingclaude.self_optimizer.optimizer import (
    OptimizationRequest,
    SynchronousOptimizer,
)
from lingclaude.self_optimizer.trigger import OptimizationTrigger

logger = logging.getLogger(__name__)

DEFAULT_STATE_DIR = Path(".lingclaude")


@dataclass(frozen=True)
class OptimizationCycle:
    cycle_id: int
    triggered_at: str
    trigger_reason: str
    trigger_type: str
    trigger_priority: str
    best_score: float
    best_params: dict[str, Any]
    experiments: int
    duration_seconds: float
    violations_before: int
    violations_after: int
    report_path: str | None


@dataclass
class DaemonState:
    last_optimization_time: str | None = None
    last_metrics: dict[str, Any] = field(default_factory=dict)
    total_cycles: int = 0
    total_improvements: int = 0
    cycles: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> DaemonState:
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                return cls(
                    last_optimization_time=raw.get("last_optimization_time"),
                    last_metrics=raw.get("last_metrics", {}),
                    total_cycles=raw.get("total_cycles", 0),
                    total_improvements=raw.get("total_improvements", 0),
                    cycles=raw.get("cycles", []),
                )
            except (json.JSONDecodeError, KeyError):
                logger.warning("状态文件损坏，使用默认状态")
        return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )


class OptimizationDaemon:
    def __init__(
        self,
        target: str = ".",
        config: LingClaudeConfig | None = None,
        state_dir: Path | None = None,
    ) -> None:
        self.target = target
        self.config = config or load_config()
        self.state_dir = state_dir or DEFAULT_STATE_DIR
        self.state_path = self.state_dir / "daemon_state.json"
        self.reports_dir = self.state_dir / "reports"

        self.trigger = OptimizationTrigger(self.config.triggers)
        self.evaluator = StructureEvaluator(target)
        self.optimizer = SynchronousOptimizer()
        self.advisor = OptimizationAdvisor()
        self.state = DaemonState.load(self.state_path)
        self._behavior_snapshot: dict[str, Any] = {}

    def collect_metrics(self) -> dict[str, Any]:
        metrics = self.evaluator.get_current_metrics()
        metrics["last_optimization_time"] = self.state.last_optimization_time
        return metrics

    def build_context(self, metrics: dict[str, Any], user_triggered: bool = False) -> dict[str, Any]:
        ctx: dict[str, Any] = dict(metrics)
        ctx["last_optimization_time"] = self.state.last_optimization_time
        ctx["user_triggered"] = user_triggered
        ctx["hallucination_risk"] = self._behavior_snapshot.get("hallucination_risk", 0)
        ctx["frustration_rate"] = self._behavior_snapshot.get("frustration_rate", 0)
        ctx["tool_error_rate"] = self._behavior_snapshot.get("tool_error_rate", 0)
        ctx["corrections_received"] = self._behavior_snapshot.get("corrections_received", 0)
        history = self.load_behavior_history()
        ctx["cumulative_frustration"] = history.get("total_frustration", 0)
        ctx["cumulative_corrections"] = history.get("total_corrections", 0)
        return ctx

    def update_behavior(self, behavior: dict[str, Any]) -> None:
        self._behavior_snapshot = behavior

    def run_cycle(self, user_triggered: bool = False) -> OptimizationCycle | None:
        metrics = self.collect_metrics()
        context = self.build_context(metrics, user_triggered=user_triggered)

        should_trigger, trigger_info = self.trigger.check_all_conditions(context)
        if not should_trigger:
            logger.info("无触发条件，跳过本轮")
            return None

        logger.info(
            "触发优化: type=%s reason=%s priority=%s",
            trigger_info.type,
            trigger_info.reason,
            trigger_info.priority,
        )

        violations_before = metrics.get("structure_violations", 0)
        start = time.monotonic()

        request = OptimizationRequest(
            target=self.target,
            goal=self.config.optimizer.goal,
            params={},
            config={"max_experiments": self.config.optimizer.max_trials},
        )
        result = self.optimizer.optimize(request)
        duration = time.monotonic() - start

        if not result.success:
            logger.error("优化失败: %s", result.error)
            return None

        self.reports_dir.mkdir(parents=True, exist_ok=True)
        report_name = f"cycle_{self.state.total_cycles + 1:04d}.md"
        report_path = self.reports_dir / report_name

        report = self.advisor.generate_report(
            goal=self.config.optimizer.goal,
            target=self.target,
            current_metrics=metrics,
            optimization_result=result,
        )
        self.advisor.save_report(report, str(report_path))

        violations_after = int(result.best_score)

        self._apply_params(result.best_params)

        cycle = OptimizationCycle(
            cycle_id=self.state.total_cycles + 1,
            triggered_at=datetime.now().isoformat(),
            trigger_reason=trigger_info.reason,
            trigger_type=trigger_info.type,
            trigger_priority=trigger_info.priority,
            best_score=result.best_score,
            best_params=result.best_params,
            experiments=result.experiments,
            duration_seconds=round(duration, 2),
            violations_before=violations_before,
            violations_after=violations_after,
            report_path=str(report_path),
        )

        self._record_cycle(cycle)
        logger.info(
            "优化完成: score=%.2f experiments=%d duration=%.1fs report=%s",
            cycle.best_score,
            cycle.experiments,
            cycle.duration_seconds,
            report_path,
        )
        return cycle

    def run_watch(self, interval_seconds: int = 300) -> None:
        logger.info(
            "自由化框架启动 (watch 模式, interval=%ds, target=%s)",
            interval_seconds,
            self.target,
        )
        logger.info("按 Ctrl+C 停止")
        try:
            while True:
                cycle = self.run_cycle()
                if cycle:
                    self._write_cycle_to_knowledge(cycle)
                    print(
                        f"[{cycle.triggered_at}] Cycle #{cycle.cycle_id}: "
                        f"score={cycle.best_score:.2f} "
                        f"violations={cycle.violations_before}→{cycle.violations_after} "
                        f"({cycle.duration_seconds}s)"
                    )
                time.sleep(interval_seconds)
        except KeyboardInterrupt:
            logger.info("自由化框架已停止")
            print("\n自由化框架已停止")

    def run_once(self) -> OptimizationCycle | None:
        logger.info("自由化框架单次运行 (target=%s)", self.target)
        cycle = self.run_cycle(user_triggered=True)
        if cycle:
            self._write_cycle_to_knowledge(cycle)
        return cycle

    def _apply_params(self, params: dict[str, Any]) -> None:
        config_path = Path("config.yaml")
        if not config_path.exists():
            logger.debug("无 config.yaml，跳过参数应用")
            return

        try:
            import yaml

            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if raw is None:
                raw = {}

            opt_section = raw.setdefault("self_optimizer", {}).setdefault(
                "optimization", {}
            )
            trigger_section = raw.setdefault("self_optimizer", {}).setdefault(
                "triggers", {}
            )

            param_map = {
                "max_class_size": ("triggers", "max_class_lines"),
                "max_method_count": ("triggers", "max_method_count"),
                "max_complexity": ("triggers", "max_complexity"),
                "max_nesting_depth": ("optimization", "max_nesting_depth"),
                "coupling_limit": ("optimization", "coupling_limit"),
            }

            changed = False
            for param_key, (section, yaml_key) in param_map.items():
                if param_key in params:
                    target_section = (
                        trigger_section if section == "triggers" else opt_section
                    )
                    if isinstance(params[param_key], float):
                        target_section[yaml_key] = round(params[param_key], 2)
                    else:
                        target_section[yaml_key] = params[param_key]
                    changed = True

            if changed:
                config_path.write_text(
                    yaml.dump(raw, default_flow_style=False, allow_unicode=True),
                    encoding="utf-8",
                )
                logger.info("已应用优化参数到 config.yaml")
        except Exception:
            logger.warning("应用参数失败", exc_info=True)

    def _record_cycle(self, cycle: OptimizationCycle) -> None:
        self.state.last_optimization_time = cycle.triggered_at
        self.state.total_cycles += 1
        if cycle.violations_after < cycle.violations_before:
            self.state.total_improvements += 1
        self.state.cycles.append(
            {
                "cycle_id": cycle.cycle_id,
                "triggered_at": cycle.triggered_at,
                "trigger_type": cycle.trigger_type,
                "trigger_reason": cycle.trigger_reason,
                "best_score": cycle.best_score,
                "experiments": cycle.experiments,
                "duration_seconds": cycle.duration_seconds,
                "violations_before": cycle.violations_before,
                "violations_after": cycle.violations_after,
                "report_path": cycle.report_path,
            }
        )
        if len(self.state.cycles) > 100:
            self.state.cycles = self.state.cycles[-100:]
        self.state.save(self.state_path)

    def _write_cycle_to_knowledge(self, cycle: OptimizationCycle) -> None:
        try:
            from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase
            from lingclaude.self_optimizer.learner.models import (
                FeedbackCategory,
                LearnedRule,
                Pattern,
            )

            kb = KnowledgeBase()
            rule_id = f"opt_cycle_{cycle.cycle_id:04d}"
            improved = cycle.violations_after < cycle.violations_before
            rule = LearnedRule(
                id=rule_id,
                name=f"自优化周期 #{cycle.cycle_id}",
                description=f"触发: {cycle.trigger_reason} | 结果: score={cycle.best_score:.2f} violations={cycle.violations_before}→{cycle.violations_after}",
                category=FeedbackCategory.BEST_PRACTICE,
                pattern=Pattern(
                    context_keywords=(cycle.trigger_type, cycle.trigger_priority),
                    severity_distribution={"before": cycle.violations_before, "after": cycle.violations_after},
                ),
                tools=("optimizer", "evaluator"),
                frequency=1,
                confidence=0.8 if improved else 0.4,
                quality_score=cycle.best_score / 100 if cycle.best_score > 0 else 0.5,
                status="active" if improved else "draft",
            )
            kb.add_rule(rule)
            logger.info("已写入知识库: %s", rule_id)
            kb.close()
        except Exception:
            logger.warning("写入知识库失败", exc_info=True)

    def load_behavior_history(self) -> dict[str, Any]:
        behavior_path = self.state_dir / "behavior_history.json"
        if behavior_path.exists():
            try:
                return json.loads(behavior_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, KeyError):
                pass
        return {"total_turns": 0, "total_frustration": 0, "total_corrections": 0, "total_tool_errors": 0}

    def save_behavior_history(self, behavior: dict[str, Any]) -> None:
        history = self.load_behavior_history()
        history["total_turns"] = history.get("total_turns", 0) + behavior.get("total_turns", 0)
        history["total_frustration"] = history.get("total_frustration", 0) + behavior.get("frustration_count", 0)
        history["total_corrections"] = history.get("total_corrections", 0) + behavior.get("corrections_received", 0)
        history["total_tool_errors"] = history.get("total_tool_errors", 0) + behavior.get("tool_error_count", 0)
        history["last_updated"] = datetime.now().isoformat()
        behavior_path = self.state_dir / "behavior_history.json"
        behavior_path.parent.mkdir(parents=True, exist_ok=True)
        behavior_path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
