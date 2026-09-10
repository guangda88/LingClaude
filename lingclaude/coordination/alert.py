"""LingBus 告警公共通路 — N5 守卫 / N1 豁免复核共用。

设计约束（与 n5_token_guard 一致）：
  - best-effort：任何失败只落 WARNING 日志，绝不 raise
  - 无线程 ID 时走 open_thread 建新线程（不赌 post_reply 对空 thread_id 的行为）
"""
from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)

_MEMBER_ID = "lingclaude"
_DEFAULT_RECIPIENT = "lingflow_plus"


def send_lingbus_alert(
    subject: str,
    body: str,
    *,
    recipient: str = _DEFAULT_RECIPIENT,
    thread_id: str | None = None,
    message_type: str = "alert",
) -> str | None:
    """发一条 LingBus 告警；返回 message_id，失败返回 None。

    thread_id 给定 → post_reply；否则 → open_thread 建新线程。
    """
    try:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path.home() / "lingmessage"))
        from lingmessage.lingbus import LingBus

        bus: Any = LingBus()
        if thread_id:
            return bus.post_reply(
                thread_id=thread_id,
                sender=_MEMBER_ID,
                recipient=recipient,
                subject=subject,
                body=body,
                message_type=message_type,
            )
        msg_thread_id, message_id = bus.open_thread(
            topic=subject,
            sender=_MEMBER_ID,
            recipients=[recipient],
            subject=subject,
            body=body,
        )
        return message_id
    except Exception as e:  # noqa: BLE001 — 告警通路永不 raise
        _logger.warning("LingBus 告警发送失败(忽略): %s", e)
        return None
