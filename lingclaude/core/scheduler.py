"""T3/T2-2: Schedule/Jobs — 定时任务调度器（案 4 接线）。

修复死接线第 4 案：task_scheduler.py（批量调度器）已存在但无定时调度功能。

设计：
- TaskScheduler（已有）作为后端：批量任务管理 + token 配额
- ScheduleManager（新建）：cron 表达式注册 + 定时触发 + LingBus 唤醒
- 接线点：cli/app.py `/schedule` 命令 + LingBus 消息消费

注意：cron 解析用标准库（不引第三方），简化版支持：
- "@daily" / "@hourly" / "@weekly"（预定义）
- "*/N * * * *"（每 N 分钟）
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable

from lingclaude.core.task_scheduler import Task, TaskPriority, TaskScheduler
from lingclaude.core.wakeup_channel import (
    LingBusWakeupChannel,
    LocalFileWakeupChannel,
    WakeupChannel,
)


class ScheduleType(str, Enum):
    """调度类型"""
    DAILY = "@daily"      # 每天 00:00
    HOURLY = "@hourly"    # 每小时
    WEEKLY = "@weekly"    # 每周一 00:00
    INTERVAL = "interval" # 每 N 分钟


@dataclass(frozen=True)
class ScheduledTask:
    """定时任务"""
    task_id: str
    cron: str              # cron 表达式或预定义（@daily/@hourly/@weekly）
    query: str             # 任务内容
    priority: TaskPriority = TaskPriority.MEDIUM
    next_run: str = ""     # 下次运行时间（ISO 格式）
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


class ScheduleManager:
    """定时任务管理器（案 4 接线）。

    功能：
    - 注册定时任务（cron 表达式）
    - 定时触发（后台线程轮询）
    - 到期发 LingBus 消息唤醒
    - 与 TaskScheduler 集成（批量调度）
    """

    def __init__(self, task_scheduler: TaskScheduler | None = None):
        """初始化

        Args:
            task_scheduler: 批量调度器（可选，默认新建）
        """
        self._scheduler = task_scheduler or TaskScheduler()
        self._tasks: dict[str, ScheduledTask] = {}
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        # P1-2: 挂回会话回调（由调用方注册；到期任务触发，把任务内容挂回会话上下文）
        self._on_task_due: Callable[[ScheduledTask], None] | None = None
        # P1-3: 唤醒通道（灵元尺子：变化变成插片）— 默认 LingBus，可注入 LocalFile 等
        self._wakeup_channel = LingBusWakeupChannel()

    def set_on_task_due(self, callback: Callable[[ScheduledTask], None]) -> None:
        """P1-2: 注册到期回调 — 挂回会话（如把任务内容注入会话待处理队列）。"""
        self._on_task_due = callback

    def set_wakeup_channel(self, channel: Any) -> None:
        """P1-3: 注入唤醒通道插片（默认 LingBus，可换 LocalFile/Email/Slack）。

        实现类只需实现 send(task: dict) 方法（WakeupChannel Protocol）。
        """
        self._wakeup_channel = channel

    def register(self, cron: str, query: str, priority: TaskPriority = TaskPriority.MEDIUM) -> str:
        """注册定时任务

        Args:
            cron: cron 表达式（@daily/@hourly/@weekly/interval:N）
            query: 任务内容
            priority: 优先级

        Returns:
            任务 ID
        """
        import uuid

        task_id = str(uuid.uuid4())
        next_run = self._compute_next_run(cron)
        
        task = ScheduledTask(
            task_id=task_id,
            cron=cron,
            query=query,
            priority=priority,
            next_run=next_run,
        )
        
        with self._lock:
            self._tasks[task_id] = task
        
        return task_id

    def _compute_next_run(self, cron: str) -> str:
        """计算下次运行时间"""
        now = datetime.now()
        
        if cron == "@daily":
            next_run = now.replace(hour=0, minute=0, second=0) + timedelta(days=1)
        elif cron == "@hourly":
            next_run = now.replace(minute=0, second=0) + timedelta(hours=1)
        elif cron == "@weekly":
            days_ahead = 7 - now.weekday()  # 下周一
            next_run = now.replace(hour=0, minute=0, second=0) + timedelta(days=days_ahead)
        elif cron.startswith("interval:"):
            minutes = int(cron.split(":")[1])
            next_run = now + timedelta(minutes=minutes)
        elif cron.startswith("after:"):
            # P1-2: after:N — 延迟 N 秒后执行（一次性）
            seconds = int(cron.split(":")[1])
            next_run = now + timedelta(seconds=seconds)
        elif cron.startswith("at:"):
            # P1-2: at:HH:MM — 指定时刻执行（今天，若已过则明天）
            parts = cron.split(":")  # ["at", "HH", "MM"]
            if len(parts) < 3:
                raise ValueError(f"Unsupported at: {cron}（应为 at:HH:MM）")
            hour, minute = int(parts[1]), int(parts[2])
            next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
        else:
            # 简化版 cron：*/N * * * *（每 N 分钟）
            match = re.match(r"^\*/(\d+) \* \* \* \*$", cron)
            if match:
                minutes = int(match.group(1))
                next_run = now + timedelta(minutes=minutes)
            else:
                raise ValueError(f"Unsupported cron: {cron}")
        
        return next_run.isoformat()

    def start(self) -> None:
        """启动后台轮询线程"""
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止后台轮询"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _poll_loop(self) -> None:
        """后台轮询：检查到期任务并发 LingBus 唤醒"""
        while self._running:
            now = datetime.now()
            due_tasks = []
            
            with self._lock:
                for task_id, task in self._tasks.items():
                    if not task.enabled:
                        continue
                    next_run = datetime.fromisoformat(task.next_run)
                    if now >= next_run:
                        due_tasks.append(task)
                        # 更新下次运行时间
                        new_next = self._compute_next_run(task.cron)
                        self._tasks[task_id] = task.__class__(
                            **{**task.__dict__, "next_run": new_next}
                        )
            
            # 发 LingBus 唤醒
            for task in due_tasks:
                self._send_wakeup(task)
                # P1-2: 挂回会话 — 到期任务回调（如注入会话待处理队列）
                callback = self._on_task_due
                if callback is not None:
                    try:
                        callback(task)
                    except Exception:  # noqa: BLE001 — 挂回失败不阻塞轮询
                        import logging
                        logging.getLogger(__name__).warning(
                            f"Schedule attach-to-session failed for {task.task_id}"
                        )
            
            time.sleep(60)  # 每分钟检查一次

    def _send_wakeup(self, task: ScheduledTask) -> None:
        """发唤醒消息（通过注入的 WakeupChannel，可换 LingBus/LocalFile/Email/Slack）。

        P1-3 解耦前：直接 import lingmessage.LingBus + open_thread（焊死）。
        解耦后：调用 self._wakeup_channel.send(task_dict)，主干与实现分离。
        """
        task_dict = {
            "task_id": task.task_id,
            "cron": task.cron,
            "query": task.query,
            "next_run": task.next_run,
            "enabled": task.enabled,
        }
        try:
            self._wakeup_channel.send(task_dict)
        except Exception as e:  # 唤醒失败不阻塞轮询（fail-soft）
            import logging
            logging.getLogger(__name__).warning(f"Schedule wakeup failed: {e}")

    def list_tasks(self) -> list[ScheduledTask]:
        """列出所有任务"""
        with self._lock:
            return list(self._tasks.values())

    def cancel(self, task_id: str) -> bool:
        """取消任务"""
        with self._lock:
            if task_id in self._tasks:
                del self._tasks[task_id]
                return True
        return False


# 全局单例
_schedule_manager: ScheduleManager | None = None


def get_schedule_manager() -> ScheduleManager:
    """获取全局 ScheduleManager 单例"""
    global _schedule_manager
    if _schedule_manager is None:
        _schedule_manager = ScheduleManager()
    return _schedule_manager
