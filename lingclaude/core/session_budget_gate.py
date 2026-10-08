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
    """用户出口：清零全部计数（换新 tracker，策略在 loader 不受影响）。

    R1/R2 闩锁同步重置（panel_20261008_budget FINAL_synthesis §定案，codex 修正）：
    /budget reset 的存在正是为了让第二次 PAUSE 可被交接——闩锁不重置则
    第二轮退化为零交接裸挂起。用户滥用 reset 消耗的是自己的预算，非系统风险。
    """
    global _tracker
    from lingclaude.core.session_budget import BudgetTracker

    with _lock:
        _tracker = BudgetTracker()
        _relay_state["warn_stage"] = 0
        _relay_state["handover_used"] = False


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


# R1/R2 接力状态（panel_20261008_budget FINAL_synthesis 定案）：
#   warn_stage    = 已注入的预警档数（0/1/2，每档一次性，防同档重复注入）
#   handover_used = 遗言轮是否已用（用后即焚；reset() 时随计数一起重置）
# 均为进程内状态，与 tracker 同生命周期（跨重启不续算，诚实边界一致）。
_relay_state = {"warn_stage": 0, "handover_used": False}

# 预警档位（对 pause 阈值的比率；阈值样本量=1，待长任务 trace 复算后调优）
_WARN_STAGE_RATIOS = (0.75, 0.90)

_WARN_TEXTS = (
    "[budget-warn, session-scoped] 预算检查点档（75%）：现在无条件将任务状态落盘"
    "（todo 清单/handover），这是可对账的检查点动作，不是提醒。剩余窗口约够 2 个 turn。",
    "[budget-warn, session-scoped] 预算交接档（90%）：立即收尾输出交接（不得继续任务、"
    "不得新开探索），下一档为硬挂起且不可自动恢复，交接失败现场仅存 transcript。",
)


def warn_injection() -> Optional[str]:
    """R1 模型可见预警（stage 单调推进，每档一次性，无新档返回 None）。

    消费方：turn 入口（每语义轮至多一次调用，防 submission 层逐请求拼接的
    上下文放大——codex Q1 裁定）。纯展示语义，不改变 pause 判定。
    """
    try:
        ev = get_tracker().evaluate_current()
        if not ev.enabled:
            return None
        # 主维度 = pause 比率最高者（对应 yaml input_tokens: warn 8M/pause 20M）
        best = 0.0
        for v in ev.verdicts:
            if v.thresholds.pause > 0:
                best = max(best, v.current / v.thresholds.pause)
        injected = None
        while _relay_state["warn_stage"] < len(_WARN_STAGE_RATIOS) and best >= _WARN_STAGE_RATIOS[_relay_state["warn_stage"]]:
            injected = _WARN_TEXTS[_relay_state["warn_stage"]]
            _relay_state["warn_stage"] += 1  # 跨档则只注入最高档文本（短句纪律）
        return injected
    except Exception:  # noqa: BLE001 — 预警绝不反噬回合（fail-open 同闸层）
        logger.debug("budget warn_injection failed", exc_info=True)
        return None


def try_last_rites(has_relay_marker: bool, has_todos: bool) -> bool:
    """R2 遗言轮资格判定（用后即焚；资格=有真实交接物 且 本进程未用过）。

    返回 True 表示「本次放行交接」，调用方须立即执行独立小上下文交接请求
    （不传 tools、免重试、≤200K input 上限），不得复用此判定做其他用途。
    """
    if _relay_state["handover_used"]:
        return False
    if not (has_relay_marker or has_todos):
        return False  # 无交接物不给遗言轮（AC 防滥用约束①）
    _relay_state["handover_used"] = True
    return True


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
