"""灰区 (gray zone) escalate — 灵元安全模型「白/黑/灰区 escalate」落地。

H2 (2026-09-15): 审批未决时不是简单拒绝，而是：
  1. 写入 .lingclaude/guard_pending.jsonl（与 guard/permissions 共用落盘点，单源）
  2. LingBus 通知（best-effort，永不 raise）
  3. 返回结构化 reason=`pending approval`，调用方标记 record.state=escalated

设计约束（与 alert.py 一致）：
  - best-effort：任何失败只落 WARNING 日志，绝不 raise —— 灰区判定失败
    不应反过来让主链路崩掉，最坏情况退化为普通拒绝。
"""

from __future__ import annotations

import logging
from typing import Any

from lingclaude.coordination.alert import send_lingbus_alert
from lingclaude.core.permissions import (
    PENDING_LOG_PATH,
    REASON_PENDING,
    log_pending_action,
)

_logger = logging.getLogger(__name__)


def gray_zone_escalate(
    action: str,
    params: dict[str, Any] | None = None,
    *,
    session_id: str | None = None,
    notify_bus: bool = True,
) -> str:
    """把「需人工审批」的动作 escalate 到灰区。

    返回 reason（REASON_PENDING）。副作用：
      1. 落盘 .lingclaude/guard_pending.jsonl（params 摘要随记录）
      2. LingBus 告警（notify_bus=True 且可用时；失败仅 WARNING 不 raise）

    record.state=escalated 由调用方（execute_tool / 灰区消费端）标记，
    本函数只负责留痕 + 通知 + 返回 reason。
    """
    log_pending_action(action, params, mode="ask")
    if notify_bus:
        _notify_bus(action, params, session_id)
    return REASON_PENDING


def _notify_bus(
    action: str,
    params: dict[str, Any] | None,
    session_id: str | None,
) -> None:
    """LingBus 告警通知 — best-effort，任何失败仅 WARNING。"""
    try:
        param_hint = ""
        if params:
            keys = list(params.keys())[:6]
            param_hint = " params=" + ",".join(keys)
        send_lingbus_alert(
            subject=f"[灰区] 待审批动作: {action}",
            body=(
                f"action={action} session={session_id or 'unknown'}{param_hint}\n"
                f"pending 已落盘: {PENDING_LOG_PATH}"
            ),
        )
    except Exception as e:  # noqa: BLE001 — 告警通路永不 raise
        _logger.warning("灰区 bus 通知失败(忽略): %s", e)
