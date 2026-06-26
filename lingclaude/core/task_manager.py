"""TaskManager — 任务上下文挂起/恢复，防止需求淹没。

设计原则（灵元1.0）：
- 出：用户每个需求都被完成或显式放弃，不因新需求进来而丢失
- 入：用户需求 + 自驱SDT
- 合适：优先级 用户显式 > 挂起的用户需求 > 自驱SDT > LingBus
- 流畅：挂起/恢复 < 1ms（内存操作），持久化到磁盘非阻塞
- 正确：挂起时保存checkpoint，恢复时完整重建上下文
- 返回：任务完成时自动扫描pending，提醒未完成任务

核心：灵元是唯一执行者，TaskManager只管上下文栈。
"""

import json
import logging

logger = logging.getLogger(__name__)
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TaskSnapshot:
    """任务检查点 — 灵元任务上下文的快照。"""
    task_id: str
    user_prompt: str
    status: str = "active"  # active | suspended | completed | abandoned
    created_at: float = field(default_factory=time.time)
    suspended_at: float = 0.0
    completed_at: float = 0.0

    # 灵元上下文快照
    messages_snapshot: list[str] = field(default_factory=list)
    conversation_snapshot: list[tuple] = field(default_factory=list)
    transcript_snapshot: list[str] = field(default_factory=list)

    # 挂起时的简要状态（供恢复时提示灵元）
    checkpoint_summary: str = ""

    # 灵元判断的任务特征
    complexity: str = ""  # simple | known_flow | complex
    skill_loaded: str = ""  # 如果加载了skill，记录skill名

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "user_prompt": self.user_prompt,
            "status": self.status,
            "created_at": self.created_at,
            "suspended_at": self.suspended_at,
            "completed_at": self.completed_at,
            "messages_count": len(self.messages_snapshot),
            "conversation_count": len(self.conversation_snapshot),
            "checkpoint_summary": self.checkpoint_summary,
            "complexity": self.complexity,
            "skill_loaded": self.skill_loaded,
        }

    @classmethod
    def from_engine(cls, task_id: str, prompt: str, engine: Any) -> "TaskSnapshot":
        """从QueryEngine当前状态创建快照。"""
        return cls(
            task_id=task_id,
            user_prompt=prompt,
            messages_snapshot=list(engine._messages),
            conversation_snapshot=list(engine._conversation),
            transcript_snapshot=list(engine._transcript),
        )

    def restore_to(self, engine: Any) -> None:
        """将快照恢复到QueryEngine。"""
        engine._messages = list(self.messages_snapshot)
        engine._conversation = list(self.conversation_snapshot)
        engine._transcript = list(self.transcript_snapshot)


class TaskManager:
    """协作式任务栈 — 单线程内管理灵元的任务上下文。

    栈结构（LIFO）：
    - active: 当前灵元正在处理的任务
    - pending: 被挂起的任务（新需求进来时压栈）
    - completed: 已完成（保留最近N个供回溯）

    调度优先级：
    用户显式需求 > 挂起的用户需求 > 自驱SDT > LingBus消息
    """

    PERSIST_PATH = Path(".lingclaude/tasks.json")
    MAX_COMPLETED = 10  # 保留最近10个完成任务

    def __init__(self) -> None:
        self.active: TaskSnapshot | None = None
        self.pending: list[TaskSnapshot] = []
        self.completed: list[TaskSnapshot] = []
        self._task_counter = 0

    def _next_id(self) -> str:
        self._task_counter += 1
        return f"task-{self._task_counter:04d}"

    def on_new_request(self, prompt: str, engine: Any) -> TaskSnapshot:
        """新用户需求进来 → 挂起当前任务 → 创建新任务。

        返回新创建的TaskSnapshot。
        """
        # 挂起当前任务
        if self.active and self.active.status == "active":
            self._suspend_active(engine)

        # 创建新任务
        task = TaskSnapshot.from_engine(self._next_id(), prompt, engine)
        task.status = "active"
        self.active = task
        return task

    def _suspend_active(self, engine: Any) -> None:
        """挂起当前active任务，保存上下文快照。"""
        if not self.active:
            return

        # 更新快照（捕获最新状态）
        self.active.messages_snapshot = list(engine._messages)
        self.active.conversation_snapshot = list(engine._conversation)
        self.active.transcript_snapshot = list(engine._transcript)
        self.active.status = "suspended"
        self.active.suspended_at = time.time()

        # 生成checkpoint摘要（供恢复时提示）
        msgs = self.active.messages_snapshot
        if msgs:
            last_user = ""
            for i in range(len(msgs) - 1, -1, -2):
                if i >= 0:
                    last_user = msgs[i][:200] if i < len(msgs) else ""
                    break
            self.active.checkpoint_summary = (
                f"挂起时已有{len(msgs)}条消息。"
                f"最后用户输入: {last_user[:100]}..."
            )

        self.pending.append(self.active)
        self.active = None

    def on_task_complete(self, engine: Any) -> TaskSnapshot | None:
        """当前任务完成 → 归档 → 自动弹栈恢复上一个任务。

        返回恢复的TaskSnapshot（如果有），否则None。
        """
        if self.active:
            self.active.status = "completed"
            self.active.completed_at = time.time()
            self.completed.append(self.active)
            if len(self.completed) > self.MAX_COMPLETED:
                self.completed = self.completed[-self.MAX_COMPLETED:]
            self.active = None

        # 弹栈恢复
        if self.pending:
            restored = self.pending.pop()
            restored.status = "active"
            restored.restore_to(engine)
            self.active = restored
            return restored

        return None

    def on_abandon(self, engine: Any) -> TaskSnapshot | None:
        """显式放弃当前任务 → 归档为abandoned → 弹栈恢复。"""
        if self.active:
            self.active.status = "abandoned"
            self.active.completed_at = time.time()
            self.completed.append(self.active)
            if len(self.completed) > self.MAX_COMPLETED:
                self.completed = self.completed[-self.MAX_COMPLETED:]
            self.active = None

        if self.pending:
            restored = self.pending.pop()
            restored.status = "active"
            restored.restore_to(engine)
            self.active = restored
            return restored
        return None

    def can_sdt_run(self) -> bool:
        """自驱SDT触发时检查：有未完用户需求则让路。"""
        if self.pending:
            return False
        if self.active and self.active.status == "active":
            return False
        return True

    def pending_summary(self) -> str:
        """返回pending任务的摘要（供提醒用户）。"""
        if not self.pending:
            return ""
        lines = []
        for i, t in enumerate(reversed(self.pending), 1):
            age = int(time.time() - t.suspended_at) if t.suspended_at else 0
            prompt_short = t.user_prompt[:80]
            lines.append(f"  {i}. [{age}s前挂起] {prompt_short}")
        return "\n".join(lines)

    def status(self) -> dict:
        """返回当前任务管理器状态。"""
        return {
            "active": self.active.to_dict() if self.active else None,
            "pending_count": len(self.pending),
            "pending": [t.to_dict() for t in reversed(self.pending)],
            "completed_count": len(self.completed),
        }

    def persist(self) -> None:
        """持久化到磁盘（会话结束时调用）。"""
        try:
            data = {
                "active": self.active.to_dict() if self.active else None,
                "pending": [t.to_dict() for t in self.pending],
                "completed": [t.to_dict() for t in self.completed],
                "task_counter": self._task_counter,
                "saved_at": time.time(),
            }
            self.PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
            self.PERSIST_PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except (OSError, TypeError, ValueError) as e:
            logger.error("task 持久化失败 (path=%s): %s", self.PERSIST_PATH, e)

    def load(self) -> None:
        """从磁盘加载（会话启动时调用）。"""
        try:
            if not self.PERSIST_PATH.exists():
                return
            data = json.loads(self.PERSIST_PATH.read_text(encoding="utf-8"))
            self._task_counter = data.get("task_counter", 0)

            # 恢复pending（不恢复active，因为engine状态已重置）
            for td in data.get("pending", []):
                t = TaskSnapshot(
                    task_id=td["task_id"],
                    user_prompt=td["user_prompt"],
                    status="suspended",
                    created_at=td.get("created_at", time.time()),
                    suspended_at=td.get("suspended_at", 0),
                    checkpoint_summary=td.get("checkpoint_summary", ""),
                    complexity=td.get("complexity", ""),
                    skill_loaded=td.get("skill_loaded", ""),
                )
                self.pending.append(t)

            # 恢复completed（仅元数据）
            for td in data.get("completed", []):
                t = TaskSnapshot(
                    task_id=td["task_id"],
                    user_prompt=td["user_prompt"],
                    status=td.get("status", "completed"),
                    created_at=td.get("created_at", time.time()),
                    completed_at=td.get("completed_at", 0),
                )
                self.completed.append(t)
        except (OSError, KeyError, TypeError, ValueError, AttributeError) as e:
            logger.warning("task 恢复失败 (path=%s): %s", self.PERSIST_PATH, e)
