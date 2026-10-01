"""会话预算线（灵克 P1②：会话成本透明化与失控防线）。

出处（外部清单对账 2026-10-02）:
  豆包 Personal Agent 评估坑③「成本失控」，灵克修正后采纳：
  阈值动作 = WARN（提示不中断）→ PAUSE（暂停+报告+用户裁决），
  不静默降档。语义分界（勘察 submission.py:126 结论）：
    - max_budget_tokens = 上下文累计 in+out 硬停（MAX_BUDGET_REACHED），
      面向「窗口还能装多少」，归 submission/_compact_if_needed 管，本模块不重复；
    - 本模块 = 会话资源消耗面：工具调用数 / 模型调用数 / in-out 分项，
      双阈值（warn/pause）决策对象，供接线轮挂 turn 循环与埋点。

设计（本文件 = 纯核心，无 I/O）:
  - BudgetTracker.record() → BudgetDelta：纯计数器，按 policy 维度泛化记录，
    不读 yaml、不读盘（接线层喂维度名）；record 后自动 evaluate。
  - SessionBudgetLoader.load() → SessionBudgetPolicy：薄封装 policy_loader.get()
    （mtime watch 热复用），缺文件/坏文件/无 enabled 键 → disabled 缺省（暗发车）。
  - evaluate() → BudgetEvaluation：跨维度聚合，pause 优先于 warn；
    WARN/PAUSE 不抛异常、不中断 —— 决策权在接线层，纯核心只负责说真话。

边界（诚实声明）:
  - 生命周期 = 进程内会话：跨重启不续算、不持久化。理由：编码助手的单会话
    失控场景是「一次运行内打转/爆量」，跨会话配额是另一层（credential 池
    月配额），不做混装。
  - 全链 fail-open：policy 缺失/评估异常 → OK + reason，守卫哲学「操作可重试
    的走 open」（与编辑锁 fail-closed 的区分准则一致）。

接线轮对接清单（本文件不实现，防半成品）:
  1. record 点: query_engine_turn_mixin._finalize_turn（model_calls/input/output）
                + tool_pipeline dispatch 后（tool_calls）
  2. 消费点:   repl 主循环每 turn 后 evaluate → PAUSE 时打印报告并阻下一 turn
                （用户输入新内容即解除——与 loop_interrupted latch 同构）
  3. 展示点:   status.py 状态栏 WARN 段（数据源 _usage 已有，本模块补计数维度）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from lingclaude.core import policy_loader

logger = logging.getLogger(__name__)

# 策略名（policies/session_budget_policy.yaml，PolicyLoader 热更语义继承）
_POLICY_NAME = "session_budget_policy"

# 内置维度白名单：policy 里未声明的维度直接拒收（打错字/恶意膨胀防线）。
# 新增维度 = 白名单加一行 + yaml 加一段（两处同改，防止单边漂移）。
_KNOWN_DIMENSIONS = frozenset({"tool_calls", "model_calls", "input_tokens", "output_tokens"})

# 降级缺省：任何异常 → 全维度不限（fail-open，理由进 Evaluation）


@dataclass(frozen=True)
class BudgetDelta:
    """一次增量记录（接线层每次喂一个维度，量可为 0）。"""

    dimension: str
    amount: int = 1


@dataclass(frozen=True)
class BudgetThresholds:
    """单维度阈值对（0 = 不限，与 yaml 语义对齐）。"""

    warn: int = 0
    pause: int = 0

    def is_unlimited(self) -> bool:
        """双阈值全 0 = 该维度不限。"""
        return self.warn <= 0 and self.pause <= 0


@dataclass(frozen=True)
class BudgetVerdict:
    """单维度裁决。OK / WARN（提示） / PAUSE（暂停建议）。"""

    dimension: str
    level: str  # "ok" | "warn" | "pause"
    current: int
    thresholds: BudgetThresholds

    @property
    def is_pause(self) -> bool:
        return self.level == "pause"

    @property
    def is_warn(self) -> bool:
        return self.level == "warn"


@dataclass
class BudgetEvaluation:
    """跨维度聚合结果（evaluate 返回值）。"""

    enabled: bool
    verdicts: list = field(default_factory=list)
    reason: str = ""

    @property
    def pauses(self) -> list:
        return [v for v in self.verdicts if v.is_pause]

    @property
    def warns(self) -> list:
        return [v for v in self.verdicts if v.is_warn]

    @property
    def ok(self) -> bool:
        """无 pause 即 OK（warn 不阻断，展示层语义）。"""
        return not self.pauses

    def summary(self) -> str:
        """人读汇总：接报告打印/状态栏两用。"""
        if not self.enabled:
            return "预算线未启用"
        parts = []
        for v in self.pauses:
            parts.append(
                f"[暂停建议] {v.dimension} {v.current}/{v.thresholds.pause}"
            )
        for v in self.warns:
            parts.append(
                f"[提示] {v.dimension} {v.current}/{v.thresholds.warn}"
            )
        if self.reason:
            parts.append(f"({self.reason})")
        return "；".join(parts) if parts else "预算内"


class BudgetTracker:
    """会话预算计数器（纯核心，无 I/O）。

    用法（接线轮）:
        tracker.record(BudgetDelta("tool_calls", 1))
        ev = tracker.evaluate(policy)   # 由接线层决定评估时机
        if not ev.ok: ...
    """

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def record(self, delta: BudgetDelta) -> None:
        """按维度累加（amount 可为 0）。未在白名单的维度拒收并告警。"""
        if delta.dimension not in _KNOWN_DIMENSIONS:
            logger.warning("session_budget: unknown dimension %r ignored", delta.dimension)
            return
        self._counters[delta.dimension] = self._counters.get(delta.dimension, 0) + delta.amount

    def snapshot(self) -> dict[str, int]:
        """当前计数（只读副本，供展示/测试断言）。"""
        return dict(self._counters)

    def evaluate(self, policy: "SessionBudgetPolicy") -> BudgetEvaluation:
        """按策略聚合裁决（pause 优先于 warn；跨维度全列）。"""
        try:
            return self._evaluate(policy)
        except Exception as exc:  # noqa: BLE001 —— fail-open，见模块 docstring
            logger.warning("session_budget evaluate failed: %s", exc)
            return BudgetEvaluation(enabled=False, verdicts=[], reason=f"评估异常: {exc}")

    def _evaluate(self, policy: "SessionBudgetPolicy") -> BudgetEvaluation:
        if not policy.enabled:
            return BudgetEvaluation(enabled=False, verdicts=[], reason="策略未启用")
        verdicts: list[BudgetVerdict] = []
        for dim, thr in policy.budgets.items():
            current = self._counters.get(dim, 0)
            verdicts.append(
                BudgetVerdict(
                    dimension=dim,
                    level=_verdict_level(current, thr),
                    current=current,
                    thresholds=thr,
                )
            )
        return BudgetEvaluation(enabled=policy.enabled, verdicts=verdicts)

    def evaluate_current(self) -> BudgetEvaluation:
        """自取策略的便捷入口（loader fail-open 已保证缺省 disabled）。"""
        return self.evaluate(SessionBudgetLoader.load())


class SessionBudgetPolicy:
    """策略数据对象（yaml 投影）。"""

    def __init__(self, enabled: bool, budgets: dict[str, BudgetThresholds]) -> None:
        self.enabled = enabled
        self.budgets = budgets


class SessionBudgetLoader:
    """session_budget_policy.yaml 的薄装载封装（policy_loader 热更复用）。"""

    @staticmethod
    def load() -> SessionBudgetPolicy:
        try:
            raw = policy_loader.get(_POLICY_NAME) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("session_budget policy load failed: %s", exc)
            raw = {}
        if not raw or "enabled" not in raw:
            # 缺文件/坏文件/半成品 → disabled 缺省（暗发车契约）
            return SessionBudgetPolicy(enabled=False, budgets={})
        budgets: dict[str, BudgetThresholds] = {}
        raw_budgets = raw.get("budgets") or {}
        for dim, conf in raw_budgets.items():
            if not isinstance(conf, dict):
                continue
            budgets[dim] = BudgetThresholds(
                warn=_safe_int(conf.get("warn", 0)),
                pause=_safe_int(conf.get("pause", 0)),
            )
        return SessionBudgetPolicy(enabled=bool(raw["enabled"]), budgets=budgets)


def _safe_int(value: Any) -> int:
    """阈值取整（None/坏类型 → 0 = 不限，暗发车友好）。"""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _verdict_level(current: int, thr: BudgetThresholds) -> str:
    """单维度裁决（pause 优先；阈值 0 = 不限恒 OK）。"""
    if thr.pause > 0 and current >= thr.pause:
        return "pause"
    if thr.warn > 0 and current >= thr.warn:
        return "warn"
    return "ok"
