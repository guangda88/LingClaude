from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from lingclaude.core.types import Result
from lingclaude.core.config import lingclaudeConfig, load_config
from lingclaude.self_optimizer.advisor import OptimizationAdvisor
from lingclaude.self_optimizer.evaluator import StructureEvaluator
from lingclaude.self_optimizer.optimizer import (
    OptimizationRequest,
    SynchronousOptimizer,
)
from lingclaude.self_optimizer.trigger import OptimizationTrigger
from lingclaude.core.session import SessionManager

logger = logging.getLogger(__name__)

DEFAULT_STATE_DIR = Path(".lingclaude")

# 行为快照滚动窗口上限 — 见 save_behavior_history 内注释（存量/流量修正）
_BEHAVIOR_SNAPSHOT_CAP = 200

# 行为历史默认值 — load/save_behavior_history 两处逐字重复的单源（2026-09-14 收敛）
_DEFAULT_BEHAVIOR_HISTORY: dict[str, Any] = {
    "total_turns": 0,
    "total_frustration": 0,
    "total_corrections": 0,
    "total_tool_errors": 0,
    "snapshots": [],
}


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
    last_trigger_info: dict[str, Any] | None = None  # P1-1: 完整触发上下文,不再只留 type/reason 字符串
    total_cycles: int = 0
    total_improvements: int = 0
    cycles: list[dict[str, Any]] = field(default_factory=list)
    # P1 归档（2026-09-17）：历史最优 cycle 摘要 + 最近基准分基线。
    # 择优回滚的依据：新轮 best_score 劣于 best_ever → 回滚到 best_ever_params。
    best_ever_score: float | None = None
    best_ever_params: dict[str, Any] = field(default_factory=dict)
    best_ever_cycle_id: int | None = None
    benchmark_baseline: float | None = None  # P0 基线持久化（跨进程）

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
                    best_ever_score=raw.get("best_ever_score"),
                    best_ever_params=raw.get("best_ever_params", {}),
                    best_ever_cycle_id=raw.get("best_ever_cycle_id"),
                    benchmark_baseline=raw.get("benchmark_baseline"),
                )
            except (json.JSONDecodeError, KeyError):
                logger.warning("状态文件损坏，使用默认状态")
        return cls()

    def save(self, path: Path) -> None:
        resolved = path.resolve()
        if not resolved.is_relative_to(Path.cwd()):
            resolved = (Path.cwd() / path).resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )


class OptimizationDaemon:
    def __init__(
        self,
        target: str = ".",
        config: lingclaudeConfig | None = None,
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
        # P0 实证门禁（2026-09-17）：行为基准评测器——best_params 须过
        # "基准分不回退"门禁才允许应用；代理指标（violations）降为 tiebreaker。
        from lingclaude.self_optimizer.benchmark import BenchmarkEvaluator

        self.benchmark = BenchmarkEvaluator(target)
        self._last_benchmark_score: float | None = None
        self.state = DaemonState.load(self.state_path)
        # 遗留项1（2026-09-17）：基线跨进程恢复——重启后从归档态取回
        # 上次通过的基准分，避免"重启即丢基线、门禁首轮失效"。
        if self.state.benchmark_baseline is not None:
            self._last_benchmark_score = self.state.benchmark_baseline
            logger.info(
                "[P0基线] 恢复上次基准分 %.1f（来自 daemon_state）",
                self._last_benchmark_score,
            )
        self._behavior_snapshot: dict[str, Any] = {}
        # P0-4: session snapshot/rewind
        from pathlib import Path as P
        self._session_mgr = SessionManager(save_dir=P(".lingclaude/sessions"))
        self._current_session = self._session_mgr.create(
            project_path=str(P(target).resolve()),
            project_name=P(target).name,
        )
        # P0-4: on startup, rewind to last snapshot if any
        sessions = self._session_mgr.list_sessions(project_path=str(P(target).resolve()))
        if sessions:
            latest = sorted(sessions, key=lambda s: s.get("created_at", ""))[-1]
            sid = latest["session_id"]
            snap_dir = self._session_mgr.save_dir
            snap_candidates = list(snap_dir.glob(f"{self._session_mgr.SNAPSHOT_PREFIX}{sid}_*.json")) if snap_dir.exists() else []
            if snap_candidates:
                latest_snap = sorted(snap_candidates)[-1]
                restored = self._session_mgr.rewind(sid, latest_snap)
                if restored.is_ok:
                    self._current_session = restored.data
                    logger.info("已从快照恢复 session: %s", latest_snap.name)
        self._snap_interval = 5  # 每 N 轮优化做一次 snapshot

    def collect_metrics(self) -> Result[dict[str, Any]]:
        try:
            metrics = self.evaluator.get_current_metrics()
            metrics["last_optimization_time"] = self.state.last_optimization_time
            return Result.ok(metrics)
        except Exception as e:
            return Result.fail(f"Failed to collect metrics: {e}", code="METRIC_ERROR")

    def build_context(self, metrics: dict[str, Any], user_triggered: bool = False) -> dict[str, Any]:
        ctx: dict[str, Any] = dict(metrics)
        ctx["last_optimization_time"] = self.state.last_optimization_time
        ctx["user_triggered"] = user_triggered
        ctx["hallucination_risk"] = self._behavior_snapshot.get("hallucination_risk", 0)
        ctx["frustration_rate"] = self._behavior_snapshot.get("frustration_rate", 0)
        ctx["tool_error_rate"] = self._behavior_snapshot.get("tool_error_rate", 0)
        ctx["corrections_received"] = self._behavior_snapshot.get("corrections_received", 0)
        history_result = self.load_behavior_history()
        history = history_result.data if history_result.is_ok else {}
        ctx["cumulative_frustration"] = history.get("total_frustration", 0)
        ctx["cumulative_corrections"] = history.get("total_corrections", 0)
        # P1.5（2026-09-17）：失败实验上下文——最近失败原因与连续同因计数，
        # 供触发器/优化器避开已踩过的坑（"同一失败不重试超 2 次"代码化）。
        failed = self.state.last_metrics.get("failed_attempts", [])
        if failed:
            last = failed[-1]
            same_streak = 0
            for f in reversed(failed):
                if f.get("error_key") == last.get("error_key"):
                    same_streak += 1
                else:
                    break
            ctx["last_failure_error"] = last.get("error_key")
            ctx["same_failure_streak"] = same_streak
        # 遗留项2（2026-09-17）：高置信经验反哺——从知识库取高置信自优化
        # 规则（conf>=0.7 且 active），把"哪类触发→历史上是否有效"喂进
        # 优化上下文，供优化器/触发器决策参考（AgentEvolver Self-Navigating）。
        try:
            from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase

            kb = KnowledgeBase()
            rules_res = kb.get_all_rules(limit=100)
            if rules_res.is_ok:
                exp_rules = [
                    r for r in rules_res.data
                    if r.id.startswith("opt_cycle_") and r.confidence >= 0.7
                    and r.status == "active"
                ]
                # 置信度降序取前 5 条，注入触发类型 + 累积频次
                ctx["experience_hints"] = [
                    {
                        "trigger_type": r.pattern.context_keywords[0] if r.pattern.context_keywords else "?",
                        "frequency": r.frequency,
                        "confidence": r.confidence,
                        "improvement": r.pattern.severity_distribution.get("before", 0)
                        - r.pattern.severity_distribution.get("after", 0),
                    }
                    for r in sorted(exp_rules, key=lambda x: -x.confidence)[:5]
                ]
            kb.close()
        except Exception:  # noqa: BLE001 — 经验反哺失败不影响主循环
            logger.debug("经验反哺 build_context 失败", exc_info=True)
        return ctx

    def update_behavior(self, behavior: dict[str, Any]) -> None:
        self._behavior_snapshot = behavior

    def _record_failed_attempt(self, error: str) -> None:
        """P1.5（2026-09-17）：失败实验入档。

        error_key 取错误首行（同因归并），连续同因 ≥2 时告警——
        组织纪律"同一失败不重试超 2 次"的代码化。上限 20 条滚动。
        """
        error_key = (error or "unknown").strip().splitlines()[0][:200]
        failed: list[dict[str, Any]] = self.state.last_metrics.setdefault("failed_attempts", [])
        failed.append({
            "error_key": error_key,
            "at": datetime.now().isoformat(),
        })
        if len(failed) > 20:
            del failed[:-20]
        same_streak = 0
        for f in reversed(failed):
            if f.get("error_key") == error_key:
                same_streak += 1
            else:
                break
        if same_streak >= 2:
            logger.warning(
                "[P1.5] 同因失败连续 %d 次（%s…）——下轮应换实验方向",
                same_streak, error_key[:80],
            )
        self.state.save(self.state_path)

    def run_cycle(self, user_triggered: bool = False) -> Result[OptimizationCycle | None]:
        metrics_result = self.collect_metrics()
        if metrics_result.is_error:
            return metrics_result  # type: ignore[return-value]
        metrics = metrics_result.data
        context = self.build_context(metrics, user_triggered=user_triggered)

        should_trigger, trigger_info = self.trigger.check_all_conditions(context)
        if not should_trigger:
            logger.info("无触发条件，跳过本轮")
            return Result.ok(None)

        # P1-1: TriggerInfo 全量保留（含 metrics/current_value/threshold），
        # 随 DaemonState 持久化——此前只落 type/reason/priority 三个字符串。
        if trigger_info is not None:
            self.state.last_trigger_info = asdict(trigger_info)

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
            # ---- P1.5 失败入档（2026-09-17，借鉴 OpenEvolve artifact side-channel）----
            # 失败方案入档（含原因与当次参数），下轮 build_context 引用——
            # "同一失败不重试超 2 次"纪律的代码化：连续同因失败 2 次即冷却，
            # 本轮不再重复同一实验方向。
            self._record_failed_attempt(str(result.error))
            logger.error("优化失败: %s", result.error)
            return Result.ok(None)

        # ---- P0 实证门禁（2026-09-17）：基准分不回退 ----
        # best_params 不得只凭代理指标（violations）收账：跑行为基准，
        # 分数低于上一轮基线 → 拒绝应用（report-only 落日志），防止
        # "参数把结构指标调好看但行为变差"的优化漂移。
        bench_before = self.benchmark.run()
        if self._last_benchmark_score is not None:
            if bench_before.score < self._last_benchmark_score:
                logger.warning(
                    "[P0门禁] 基准分回退 %.1f → %.1f，本轮 best_params 拒绝应用"
                    "（violations=%s 仅作参考）",
                    self._last_benchmark_score, bench_before.score,
                    result.best_score,
                )
                return Result.ok(None)
        self._last_benchmark_score = bench_before.score
        # 遗留项1（2026-09-17）：基线同步持久化——供 daemon 重启后恢复，
        # 否则恢复读线永远读到 None（门禁跨进程失效）。
        self.state.benchmark_baseline = bench_before.score
        self.state.save(self.state_path)
        logger.info(
            "[P0门禁] 基准分 %.1f/%d 通过（passed=%d/%d）",
            bench_before.score, 100, bench_before.passed, bench_before.total,
        )

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

        # ---- P1 择优回滚（2026-09-17）：以归档历史最优为 baseline ----
        # best_score（violations，越低越好）劣于归档历史最优 → 不应用本轮
        # 参数，回滚应用 best_ever_params（若历史最优存在）——防止优化漂移。
        best_ever = self.state.best_ever_score
        if best_ever is not None and result.best_score > best_ever:
            logger.warning(
                "[P1回滚] 本轮 best_score=%.2f 劣于归档最优 %.2f（cycle #%s），"
                "回滚应用历史最优参数",
                result.best_score, best_ever, self.state.best_ever_cycle_id,
            )
            if self.state.best_ever_params:
                self._apply_params(dict(self.state.best_ever_params))
        else:
            self._apply_params(result.best_params)
            # 更新归档最优（violations 越低越好）
            if best_ever is None or result.best_score < best_ever:
                self.state.best_ever_score = float(result.best_score)
                self.state.best_ever_params = dict(result.best_params)
                self.state.best_ever_cycle_id = self.state.total_cycles + 1

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
        # P0-4: snapshot every _snap_interval cycles
        if (cycle.cycle_id % self._snap_interval) == 0:
            snap_path = self._session_mgr.snapshot(self._current_session)
            if snap_path.is_ok:
                logger.debug("Session snapshot saved: %s", snap_path.data)
        logger.info(
            "优化完成: score=%.2f experiments=%d duration=%.1fs report=%s",
            cycle.best_score,
            cycle.experiments,
            cycle.duration_seconds,
            report_path,
        )
        return Result.ok(cycle)

    def run_watch(self, interval_seconds: int = 300) -> None:
        logger.info(
            "自由化框架启动 (watch 模式, interval=%ds, target=%s)",
            interval_seconds,
            self.target,
        )
        logger.info("按 Ctrl+C 停止")
        try:
            while True:
                cycle_result = self.run_cycle()
                if cycle_result.is_ok and cycle_result.data is not None:
                    cycle = cycle_result.data
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

    def run_once(self) -> Result[OptimizationCycle | None]:
        logger.info("自由化框架单次运行 (target=%s)", self.target)
        cycle_result = self.run_cycle(user_triggered=True)
        if cycle_result.is_ok and cycle_result.data is not None:
            self._write_cycle_to_knowledge(cycle_result.data)
        return cycle_result

    def _apply_params(self, params: dict[str, Any]) -> None:
        """把优化参数写入 config.yaml — 执行器限幅版（R2，系统论融入）。

        三重护栏：
        1. **默认 report-only**：不设 LINGCLAUDE_DAEMON_APPLY=1 时只记日志不动配置
           （钱学森增益纪律：纠错执行器必须有外部许可才能作用到被控对象）。
        2. **单参数限幅**：开启应用后每周期最多写 1 个参数（纠错幅度×对象敏感度>1
           即发散——一次调多参出问题时无法归因，也没有复测窗口）。
        3. 失败只告警不抛出（本函数在优化循环尾部调用，不应炸掉整个循环）。
        """
        import os

        if not params:
            return
        if os.environ.get("LINGCLAUDE_DAEMON_APPLY") != "1":
            logger.info(
                "[report-only] 建议参数（未应用；设 LINGCLAUDE_DAEMON_APPLY=1 开启）: %s",
                params,
            )
            return

        # P0-1 审批闸门：写 config.yaml 前必过 guard（第四重护栏）。
        # 2026-09-05 事故（会话执行 rm -rf .lingclaude）证明缺少此闸的风险。
        # 2026-09-06 fix：消除上游混合缩进造成的 SyntaxError，确保 daemon 可启动。
        # H1 (2026-09-15): 统一动作闸门 — 由 ApprovalGuard 改为 PermissionContext.check_action。
        from lingclaude.core.permissions import PermissionContext, load_approval_mode

        lock_cm = None  # 提前 return 路径（未持锁）时 finally 需判空
        guard_mode = load_approval_mode(Path("config.yaml"))
        guard = PermissionContext(mode=guard_mode)
        allowed, reason = guard.check_action(
            "optimize_write",
            params={"proposed": params, "deferred_hint": "见 daemon 日志"},
        )
        if not allowed:
            if guard_mode == "strict":
                raise PermissionError(
                    f"ApprovalGuard(strict): 写配置被拒绝（{reason}）— 动作 optimize_write"
                )
            logger.warning(
                "[guard] 写配置待审批（mode=%s, reason=%s），本轮参数未应用: %s",
                guard.mode, reason, params,
            )
            return

        config_path = Path("config.yaml")
        if not config_path.exists():
            logger.debug("无 config.yaml，跳过参数应用")
            return

        # 多 agent 并行编辑锁（P3 补强）：config.yaml 读-改-写全程持锁，
        # 防止 lingclaude 会话/Claude Code/监督者三方顺序踩踏（2026-09-06 事故）。
        from lingclaude.core.file_lock import file_edit_lock

        try:
            lock_ctx = file_edit_lock(config_path, owner="self_optimizer")
            lock_ctx.__enter__()
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

            # 单参数限幅：按 param_map 顺序取第一个可应用项，其余记为待复测建议
            pending = [
                (k, v) for k, v in param_map.items() if k in params
            ]
            if not pending:
                return
            param_key, (section, yaml_key) = pending[0]
            deferred = {k: params[k] for k, _ in pending[1:]}

            target_section = trigger_section if section == "triggers" else opt_section
            value = params[param_key]
            old_value = target_section.get(yaml_key)

            # P1-3: 写前留档（回滚保障）
            from lingclaude.core.file_history import record_change

            record_change(config_path, source="self_optimizer")

            # P1-2: patch 审计记录 — 每次变更先落 patch,写配置只是应用 patch
            from datetime import datetime, timezone

            patches_dir = Path(".lingclaude") / "patches"
            patches_dir.mkdir(parents=True, exist_ok=True)
            patch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            patch = {
                "patch_id": patch_id,
                "ts": patch_id,
                "source": "self_optimizer",
                "applied": True,
                "changes": [{
                    "file": str(config_path),
                    "section": section,
                    "key": yaml_key,
                    "old": old_value,
                    "new": round(value, 2) if isinstance(value, float) else value,
                }],
                "deferred": deferred,
            }
            (patches_dir / f"patch_{patch_id}.json").write_text(
                json.dumps(patch, ensure_ascii=False, indent=2), encoding="utf-8",
            )

            target_section[yaml_key] = round(value, 2) if isinstance(value, float) else value
            config_path.write_text(
                yaml.dump(raw, default_flow_style=False, allow_unicode=True),
                encoding="utf-8",
            )
            logger.info(
                "已应用优化参数 1/%d 到 config.yaml: %s=%s（复测通过前不再应用其余参数）",
                len(pending), param_key, value,
            )
            # P0-N5 (2026-09-15): 写配置成功路径补审计留痕 — 与灰区 escalate
            # (state=pending) 形成「申请→审批→执行→留痕」闭环。审批人可依据
            # params 核对入口(pending)与出口(approved_by_daemon)的动作域一致。
            from lingclaude.core.permissions import log_pending_action

            log_pending_action(
                "optimize_write",
                params={
                    "applied_key": yaml_key,
                    "applied_value": round(value, 2) if isinstance(value, float) else value,
                    "patch_id": patch_id,
                    "deferred": deferred,
                },
                mode=guard_mode,
                state="approved_by_daemon",
            )
            if deferred:
                logger.info("[待复测] 暂缓参数建议: %s", deferred)
        except Exception:
            logger.warning("应用参数失败", exc_info=True)
        finally:
            # 读-改-写结束（含提前 return），释放编辑锁
            if lock_cm is not None:
                lock_cm.__exit__(None, None, None)

    def should_run_cycle(self, min_interval_hours: float = 24.0) -> bool:
        """节流判断：距上次优化循环是否超过 min_interval_hours。

        last_optimization_time 缺失/不可解析 → True（从未跑过就该跑，诚实优先）。
        """
        raw = getattr(self.state, "last_optimization_time", None)
        if not raw:
            return True
        try:
            last = datetime.fromisoformat(str(raw))
        except ValueError:
            return True
        return (datetime.now() - last).total_seconds() >= min_interval_hours * 3600

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
        self._record_quality_to_metrics(cycle)

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

            # ---- P2 经验复用（2026-09-17，借鉴 AgentEvolver Self-Navigating）----
            # 同触发类型的经验累积：已有同 context 规则 → 频次+1，置信度按
            # 本轮是否有效强化/衰减（有效 +0.05 封顶 0.95，无效 -0.05 下限 0.1）。
            # 下轮优化可直接引用"哪类触发→哪类参数历史上有效"。
            existing = kb.search_rules(cycle.trigger_type, limit=5)
            if existing.is_ok:
                for prior in existing.data:
                    if cycle.trigger_type in prior.pattern.context_keywords:
                        new_conf = min(0.95, prior.confidence + 0.05) if improved \
                            else max(0.1, prior.confidence - 0.05)
                        kb.update_rule_status(prior.id, prior.status)
                        # 频次/置信度经 add_rule 幂等覆盖（同 id upsert 语义见
                        # knowledge.py PRIMARY KEY 冲突处理）——直接构造更新版。
                        updated = LearnedRule(
                            id=prior.id,
                            name=prior.name,
                            description=prior.description,
                            category=prior.category,
                            pattern=prior.pattern,
                            tools=prior.tools,
                            frequency=prior.frequency + 1,
                            confidence=round(new_conf, 2),
                            quality_score=prior.quality_score,
                            status=prior.status,
                            created_at=prior.created_at,
                        )
                        kb.add_rule(updated)
                        logger.info(
                            "[P2经验] 同类触发规则 %s 强化: freq=%d conf=%.2f (%s)",
                            prior.id, updated.frequency, new_conf,
                            "有效" if improved else "无效衰减",
                        )

            rule = LearnedRule(
                id=rule_id,
                name=f"自优化周期 #{cycle.cycle_id}",
                # P2: 描述必须含触发类型——search_rules 按 name/description
                # LIKE 匹配，同类触发经验累积依赖此字段可命中。
                description=f"触发类型: {cycle.trigger_type} | 触发: {cycle.trigger_reason} | 结果: score={cycle.best_score:.2f} violations={cycle.violations_before}→{cycle.violations_after}",
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

    def load_behavior_history(self) -> Result[dict[str, Any]]:
        behavior_path = self.state_dir / "behavior_history.json"
        if behavior_path.exists():
            try:
                return Result.ok(json.loads(behavior_path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, KeyError):
                pass
        return Result.ok(
            dict(_DEFAULT_BEHAVIOR_HISTORY, snapshots=[])
        )

    def save_behavior_history(self, behavior: dict[str, Any]) -> Result[None]:
        try:
            history_result = self.load_behavior_history()
            history = history_result.data if history_result.is_ok else dict(_DEFAULT_BEHAVIOR_HISTORY, snapshots=[])
            history["total_turns"] = history.get("total_turns", 0) + behavior.get("total_turns", 0)
            history["total_frustration"] = history.get("total_frustration", 0) + behavior.get("frustration_count", 0)
            history["total_corrections"] = history.get("total_corrections", 0) + behavior.get("corrections_received", 0)
            history["total_tool_errors"] = history.get("total_tool_errors", 0) + behavior.get("tool_error_count", 0)
            history["last_updated"] = datetime.now().isoformat()
            # 系统论修正（存量/流量，Meadows）：此前只累计 total_* 存量，没有
            # 变化率序列，无法回答「行为是否在变好」。滚动快照提供流量时间线，
            # 供回路计算趋势/基线；上限 200 条防止无限膨胀。
            snapshots: list[dict[str, Any]] = history.setdefault("snapshots", [])
            snapshots.append(
                {
                    "ts": history["last_updated"],
                    "turns": behavior.get("total_turns", 0),
                    "frustration": behavior.get("frustration_count", 0),
                    "corrections": behavior.get("corrections_received", 0),
                    "tool_errors": behavior.get("tool_error_count", 0),
                }
            )
            history["snapshots"] = snapshots[-_BEHAVIOR_SNAPSHOT_CAP:]
            behavior_path = self.state_dir / "behavior_history.json"
            behavior_path.parent.mkdir(parents=True, exist_ok=True)
            behavior_path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
            return Result.ok(None)
        except Exception as e:
            return Result.fail(f"Failed to save behavior history: {e}", code="IO_ERROR")

    def behavior_trend(self, window: int = 20) -> Result[dict[str, float]]:
        """计算最近 window 条快照的每回合摩擦率趋势。

        返回 frustration/corrections/tool_errors 的「次/回合」比率（窗口内合计
        ÷ 窗口内回合合计），配合更早窗口的同类值即可回答「是否在变好」。
        快照不足（<2 条或 0 回合）时返回空 dict — 不伪造趋势。
        """
        history_result = self.load_behavior_history()
        if history_result.is_error:
            return Result.fail(history_result.error)
        snapshots = history_result.data.get("snapshots", [])[-window:]
        total_turns = sum(s.get("turns", 0) for s in snapshots)
        if len(snapshots) < 2 or total_turns <= 0:
            return Result.ok({})
        rates = {
            key: round(sum(s.get(key, 0) for s in snapshots) / total_turns, 4)
            for key in ("frustration", "corrections", "tool_errors")
        }
        return Result.ok(rates)

    def _record_quality_to_metrics(self, cycle: OptimizationCycle) -> None:
        try:
            from lingclaude.core.metrics import MetricsStore, QualityScorer

            store = MetricsStore(self.state_dir / "metrics.db")
            scorer = QualityScorer(store)
            structure_metrics = {
                "violations": cycle.violations_after,
                "avg_complexity": self.state.last_metrics.get("avg_complexity", 5.0),
                "large_classes": self.state.last_metrics.get("large_classes", 0),
            }
            behavior_metrics = {
                "hallucination_risk": self._behavior_snapshot.get("hallucination_risk", 0),
                "frustration_rate": self._behavior_snapshot.get("frustration_rate", 0),
                "tool_error_rate": self._behavior_snapshot.get("tool_error_rate", 0),
            }
            safety_metrics = {
                "hard_stops": 0,
                "verification_passes": 1,
                "verification_total": 1,
            }
            scorer.compute_overall(
                structure=structure_metrics,
                behavior=behavior_metrics,
                safety=safety_metrics,
            )
            store.record("optimization", "cycle_score", cycle.best_score, cycle_id=str(cycle.cycle_id))
            store.close()
            logger.info("已记录质量指标 cycle=#%d", cycle.cycle_id)
        except Exception:
            logger.debug("质量指标记录失败", exc_info=True)
