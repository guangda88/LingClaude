"""session_budget_gate — P1② 预算线接线层（2026-10-02）。

session_budget.py（纯核心，4169e65）的生产消费侧三件套：

  1. 记录点（引擎单线程串行区调用，fail-open 绝不反噬回合）
     - tool_calls : query_engine_turn_mixin._execute_tool_with_retry 入口
     - model_calls + input/output_tokens : _finalize_turn（与 D3 落盘同源口径）
  2. 暂停闸（submission.submit / stream_submit 入口）
     - 策略 enabled 且任一维度达 pause → 本轮不再请求模型，返回
       StopReason.BUDGET_PAUSED + 人读报告（含 /budget reset 出口提示）
     - stream 路径合成 message_delta + message_stop（前端 SSE 契约不变）
  3. /budget 斜杠命令（cli/slash_plugins/budget.py）：查看 / reset

语义红线（承接 session_budget.py docstring）：
  - WARN 不阻断（展示层语义），只有 PAUSE 阻断
  - 不静默降档 —— 暂停=暂停，恢复权在用户（/budget reset 或改 yaml 阈值）
  - 生命周期 = 进程内会话；跨重启不续算（reset 语义 = 本进程重新计数）
  - fail-open：本层任何异常都不得炸回合主流程（记录/评估各自 try 包裹）

线程模型：记录全部发生在引擎回合单线程区；/budget 命令经 input pump
在轮间串行消费，理论无并发 —— Lock 仅防 /multi 插队边角，粗粒度即可。
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_PAUSE_HINT = "（恢复：/budget reset 重置计数，或调大 core/policies/session_budget_policy.yaml 阈值 + /policy reload）"

_lock = threading.Lock()
_tracker = None  # 惰性单例（避免 import 即建；测试可 reset_gate_state）


def get_tracker():
    """进程级 BudgetTracker 单例（惰性）。"""
    global _tracker
    if _tracker is None:
        from lingclaude.core.session_budget import BudgetTracker

        _tracker = BudgetTracker()
    return _tracker


def reset_gate_state() -> None:
    """测试钩子：清空单例（生产用 reset()）。"""
    global _tracker
    with _lock:
        _tracker = None


def reset() -> None:
    """用户出口：清零全部计数（换新 tracker，策略在 loader 不受影响）。"""
    global _tracker
    from lingclaude.core.session_budget import BudgetTracker

    with _lock:
        _tracker = BudgetTracker()


def record_tool_call(count: int = 1) -> None:
    """工具调用 +1（fail-open）。记录点：_execute_tool_with_retry 入口。"""
    try:
        from lingclaude.core.session_budget import BudgetDelta

        with _lock:
            get_tracker().record(BudgetDelta("tool_calls", count))
    except Exception:  # noqa: BLE001 — 预算记账绝不反噬工具执行
        logger.debug("budget record_tool_call failed", exc_info=True)


def record_model_call(input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> None:
    """模型请求 +1 + token 分项（fail-open）。

    口径与 D3 record_turn_usage 同源（_finalize_turn 的 total_input/total_output），
    不在此重复估算；cached 不计费入分项（口径注释见 session_budget.yaml）。
    """
    try:
        from lingclaude.core.session_budget import BudgetDelta

        with _lock:
            t = get_tracker()
            t.record(BudgetDelta("model_calls", 1))
            t.record(BudgetDelta("input_tokens", max(0, int(input_tokens))))
            t.record(BudgetDelta("output_tokens", max(0, int(output_tokens))))
    except Exception:  # noqa: BLE001
        logger.debug("budget record_model_call failed", exc_info=True)


def check_pause() -> Optional[str]:
    """暂停闸判定：达 PAUSE 返回人读报告；否则 None（含 disabled/异常路径）。

    消费方：submission.submit / stream_submit —— 只在「即将发起模型请求」前调，
    因此斜杠命令与本地工具路径天然不受闸影响。
    """
    try:
        ev = get_tracker().evaluate_current()
    except Exception:  # noqa: BLE001 — 评估故障放行（fail-open）
        logger.debug("budget check_pause evaluate failed", exc_info=True)
        return None
    if ev.ok or not ev.enabled:
        return None
    return "[预算暂停] 本会话用量达 pause 阈值，模型请求已挂起：\n  " \
        + ev.summary() + "\n  " + _PAUSE_HINT


def warn_lines() -> list:
    """展示层：WARN 段文本行（status / /budget 消费；无 WARN 返回空）。"""
    try:
        ev = get_tracker().evaluate_current()
    except Exception:  # noqa: BLE001
        return []
    if not ev.enabled:
        return []
    return [f"[预算提示] {v.dimension} {v.current}/{v.thresholds.warn}" for v in ev.warns]


def snapshot_for_display() -> dict:
    """/budget 展示：计数 + 裁决一屏打全。"""
    try:
        ev = get_tracker().evaluate_current()
        return {"counters": get_tracker().snapshot(), "summary": ev.summary(),
                "enabled": ev.enabled}
    except Exception as exc:  # noqa: BLE001
        return {"counters": {}, "summary": f"评估失败: {exc}", "enabled": False}
