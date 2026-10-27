"""NanoJev rules 层：会话级动作步数硬闸（hard_rules 对应物）。

设计（2026-10-27）:
  - 进程内会话计数器（session_id → int），默认上限 500（表驱动:
    policies/decision_thresholds.yaml limits.max_actions_per_session，
    经 decision_thresholds.get_value 热更）。
  - fail-open 纪律: 阈值读口异常 → 回退缺省 500；本闸任何异常不阻断执行。
  - fail-closed 语义点: 步数烧尽即拒（GUARD_DENIED），这是硬保险丝的本职——
    与文件锁 P2 闸同位（pipeline execute 的 monotonic guards 段）。
  - 注册为 tool_pipeline guard（add_guard），GuardDecision(deny) 在 dispatch
    前拦截；warn 比例处记一条 gray 进决策流（不拦截）。
  - 会话结束由宿主调用 release_session() 释放计数（防长驻进程内存缓涨）。
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

# 进程内计数: session_id → 已执行工具次数
_COUNTERS: dict[str, int] = {}
_LOCK = threading.Lock()

_WARNED: set[str] = set()


class _BudgetDecision:
    """guard 决策的本地纯数据类型（依赖倒置：core 不得 import engine.tool_pipeline）。

    消费端 tool_pipeline 只读 .decision/.reason 两个属性，鸭子类型兼容。
    """

    __slots__ = ("decision", "reason")

    def __init__(self, decision: str, reason: str) -> None:
        self.decision = decision
        self.reason = reason


def _limit() -> int:
    from lingclaude.core.decision_thresholds import get_value

    return int(get_value("limits", "max_actions_per_session"))


def _warn_ratio() -> float:
    from lingclaude.core.decision_thresholds import get_value

    return float(get_value("limits", "warn_ratio"))


def bump(session_id: str) -> tuple[int, int]:
    """计数+1，返回 (当前值, 上限)。异常时返回 (0, 500) fail-open。"""
    try:
        with _LOCK:
            _COUNTERS[session_id] = _COUNTERS.get(session_id, 0) + 1
            return _COUNTERS[session_id], _limit()
    except Exception:  # noqa: BLE001
        return 0, 500


def current(session_id: str) -> int:
    with _LOCK:
        return _COUNTERS.get(session_id, 0)


def release_session(session_id: str) -> None:
    with _LOCK:
        _COUNTERS.pop(session_id, None)
        _WARNED.discard(session_id)


def make_step_budget_guard(session_id_provider):
    """构造 tool_pipeline guard: (ToolDefinition, PipelineContext) → GuardDecision。

    session_id_provider: () → str（宿主注入会话身份，通常 lambda: engine.session_id）。
    计数在 guard 内递增——guard 通过即视为一次动作消费。
    """

    def _guard(tool_def, ctx):  # noqa: ANN001 — 与 add_guard 签名对齐
        try:
            sid = session_id_provider() or "unknown"
            used, limit = bump(sid)
            if used > limit:
                return _BudgetDecision(
                    decision="deny",
                    reason=f"[action-budget] 会话步数硬闸已烧尽 {used-1}/{limit}，"
                    "强制停机（hard_rules 对应物）",
                )
            if used >= max(1, int(limit * _warn_ratio())) and sid not in _WARNED:
                _WARNED.add(sid)
                try:
                    from lingclaude.core.decision_log import record_decision

                    record_decision(
                        domain="rules",
                        action="step_budget_warn",
                        verdict="gray",
                        p=round(used / max(limit, 1), 3),
                        reason=f"步数逼近硬闸 {used}/{limit}",
                        session_id=sid,
                    )
                except Exception:  # noqa: BLE001 — 决策流 fail-open
                    pass
            return _BudgetDecision(decision="abstain", reason="")
        except Exception as e:  # noqa: BLE001 — 硬闸异常绝不炸流水线
            logger.warning("action_budget guard fail-open: %s", e)
            return _BudgetDecision(decision="abstain", reason="")

    return _guard
