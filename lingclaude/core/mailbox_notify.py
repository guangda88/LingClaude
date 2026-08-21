"""Mailbox notification functions for QueryEngine."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class MailboxNotifier:
    """Encapsulates mailbox notification logic extracted from QueryEngine."""

    def __init__(self, mailbox: Any = None) -> None:
        self._mailbox = mailbox

    @property
    def mailbox(self) -> Any:
        return self._mailbox

    @mailbox.setter
    def mailbox(self, mailbox: Any) -> None:
        self._mailbox = mailbox

    def read_lingmessage_threads(self) -> tuple[Any, ...]:
        if self._mailbox is None:
            return ()
        return self._mailbox.list_threads()

    def notify_completion(self, task: str, result_summary: str, channel: str = "ecosystem") -> None:
        if self._mailbox is None:
            return
        try:
            self._mailbox.open_thread(
                sender="LINGCLAUDE",
                recipients=["ALL"],
                channel=channel,
                topic=f"工作完成: {task[:50]}",
                subject=f"灵克完成: {task}",
                body=result_summary,
            )
        except Exception as e:
            logger.warning("灵信工作完成通知失败: %s", e)

    def notify_risk(self, risk_type: str, details: str, severity: str = "warning") -> None:
        if self._mailbox is None:
            return
        try:
            self._mailbox.open_thread(
                sender="LINGCLAUDE",
                recipients=["ALL"],
                channel="ecosystem",
                topic=f"风险预警: {risk_type}",
                subject=f"[{severity.upper()}] 灵克风险预警: {risk_type}",
                body=details,
            )
        except Exception as e:
            logger.warning("灵信风险预警失败: %s", e)

    def notify_vote(self, proposal: str, options: list[str], deadline_hours: int = 48) -> None:
        if self._mailbox is None:
            return
        try:
            body = f"提案: {proposal}\n\n选项:\n"
            for i, opt in enumerate(options, 1):
                body += f"  {i}. {opt}\n"
            body += f"\n截止时间: {deadline_hours}小时后"
            self._mailbox.open_thread(
                sender="LINGCLAUDE",
                recipients=["ALL"],
                channel="ecosystem",
                topic=f"灵委会投票: {proposal[:50]}",
                subject=f"[投票] {proposal}",
                body=body,
            )
        except Exception as e:
            logger.warning("灵信投票通知失败: %s", e)
