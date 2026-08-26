"""Wakeup Channel Protocol — scheduler 唤醒通道抽象（灵元尺子：变化变成插片）。

不变主干：到期 → 触发 → 唤醒（WakeupChannel 协议）
可变插片：LingBusWakeupChannel（默认）/ LocalFileWakeupChannel（CI 友好）/ 自定义

解耦前：scheduler._send_wakeup 直接 import lingmessage.LingBus → open_thread
        唤醒通道焊死，换本地通知/邮件/Slack 要改主干
解耦后：WakeupChannel.send(task) 抽象，调度器只依赖协议；实现可替换
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class WakeupChannel(Protocol):
    """定时任务唤醒通道协议（灵元：插片可换）。

    实现方需提供 send(task: dict) 方法。
    task 至少含: task_id / cron / query（其他字段透传）。
    失败时建议降级（log warn），不抛异常阻塞调度。
    """

    def send(self, task: dict[str, Any]) -> None:
        """发送唤醒消息（不抛异常，失败降级）。"""
        ...


class LingBusWakeupChannel:
    """默认实现：发 LingBus 消息唤醒（与原 scheduler._send_wakeup 行为一致）。"""

    def __init__(self, sender: str = "lingclaude", recipient: str | None = None) -> None:
        self._sender = sender
        self._recipient = recipient or sender  # 默认发给自己

    def send(self, task: dict[str, Any]) -> None:
        try:
            import sys
            sys.path.insert(0, "/home/ai/lingmessage")
            from lingmessage.lingbus import LingBus

            bus = LingBus()
            bus.open_thread(
                topic=f"schedule_wakeup:{task['task_id']}",
                sender=self._sender,
                recipients=[self._recipient],
                subject=f"[Schedule] 定时任务到期: {task.get('query', '')[:50]}",
                body=(
                    f"任务 ID: {task['task_id']}\n"
                    f"Cron: {task.get('cron', '')}\n"
                    f"内容: {task.get('query', '')}"
                ),
                channel="schedule",
            )
        except Exception as e:  # LingBus 不可用时降级为日志
            logger.warning("LingBus wakeup failed for %s: %s", task.get("task_id"), e)


class LocalFileWakeupChannel:
    """CI/单测友好实现：写本地文件（每任务一行 JSON）。"""

    def __init__(self, output_dir: str | None = None) -> None:
        self._dir = Path(output_dir or os.path.expanduser("~/.lingclaude/wakeups"))

    def send(self, task: dict[str, Any]) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            now = datetime.now(timezone.utc).isoformat()
            path = self._dir / f"{task['task_id']}_{int(datetime.now(timezone.utc).timestamp())}.json"
            payload = {"received_at": now, **task}
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("LocalFile wakeup failed for %s: %s", task.get("task_id"), e)
