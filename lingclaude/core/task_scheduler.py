#!/usr/bin/env python3
"""Task Scheduler - 批量任务调度器

功能：
- 收集待处理任务
- 批量执行任务以提高 token 利用率
- 自动优化任务执行顺序
- 监控配额使用情况
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class TaskPriority(str, Enum):
    """任务优先级"""
    LOW = "低"
    MEDIUM = "中"
    HIGH = "高"
    URGENT = "紧急"


class TaskStatus(str, Enum):
    """任务状态"""
    PENDING = "待处理"
    QUEUED = "已排队"
    RUNNING = "执行中"
    COMPLETED = "已完成"
    FAILED = "失败"


@dataclass(frozen=True)
class Task:
    """任务"""
    task_id: str
    query: str
    priority: TaskPriority = TaskPriority.MEDIUM
    status: TaskStatus = TaskStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    estimated_tokens: int = 500
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SchedulerStats:
    """调度器统计"""
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    total_tokens_used: int = 0
    batch_count: int = 0
    avg_batch_size: float = 0.0

    def get_completion_rate(self) -> float:
        """获取完成率

        Returns:
            完成率
        """
        if self.total_tasks == 0:
            return 0.0
        return self.completed_tasks / self.total_tasks


class TaskScheduler:
    """任务调度器"""

    def __init__(self, max_batch_size: int = 5, quota_limit: int = 160000):
        """初始化调度器

        Args:
            max_batch_size: 最大批量大小
            quota_limit: 配额限制（5小时周期）
        """
        self.max_batch_size = max_batch_size
        self.quota_limit = quota_limit

        # 任务队列
        self._pending_tasks: list[Task] = []
        self._completed_tasks: list[Task] = []

        # 统计
        self._stats = SchedulerStats()

    def add_task(self, query: str, priority: TaskPriority = TaskPriority.MEDIUM,
                 estimated_tokens: int = 500, metadata: dict[str, Any] | None = None) -> str:
        """添加任务

        Args:
            query: 查询内容
            priority: 优先级
            estimated_tokens: 预估 token 数
            metadata: 元数据

        Returns:
            任务 ID
        """
        import uuid

        task = Task(
            task_id=str(uuid.uuid4()),
            query=query,
            priority=priority,
            estimated_tokens=estimated_tokens,
            metadata=metadata or {},
        )

        # 按优先级插入队列
        self._insert_by_priority(task)

        return task.task_id

    def _insert_by_priority(self, task: Task) -> None:
        """按优先级插入任务

        Args:
            task: 任务
        """
        priority_order = {
            TaskPriority.URGENT: 0,
            TaskPriority.HIGH: 1,
            TaskPriority.MEDIUM: 2,
            TaskPriority.LOW: 3,
        }

        # 找到插入位置
        insert_pos = 0
        for i, existing_task in enumerate(self._pending_tasks):
            if priority_order[task.priority] < priority_order[existing_task.priority]:
                insert_pos = i
                break
            insert_pos = i + 1

        self._pending_tasks.insert(insert_pos, task)

    def get_next_batch(self, max_tokens: int | None = None) -> list[Task]:
        """获取下一批任务

        Args:
            max_tokens: 最大 token 数限制

        Returns:
            任务列表
        """
        if not self._pending_tasks:
            return []

        batch = []
        total_tokens = 0
        limit = max_tokens or (self.quota_limit * 0.1)  # 默认使用配额的 10%

        # 选择任务
        for task in self._pending_tasks[:self.max_batch_size]:
            if total_tokens + task.estimated_tokens > limit:
                break
            batch.append(task)
            total_tokens += task.estimated_tokens

        # 更新状态
        for task in batch:
            # 从队列中移除
            self._pending_tasks.remove(task)

        return batch

    def mark_completed(self, task_id: str, tokens_used: int, success: bool = True) -> None:
        """标记任务完成

        Args:
            task_id: 任务 ID
            tokens_used: 使用的 token 数
            success: 是否成功
        """
        # 更新统计
        self._stats = SchedulerStats(
            total_tasks=self._stats.total_tasks + 1,
            completed_tasks=self._stats.completed_tasks + (1 if success else 0),
            failed_tasks=self._stats.failed_tasks + (0 if success else 1),
            total_tokens_used=self._stats.total_tokens_used + tokens_used,
            batch_count=self._stats.batch_count,
            avg_batch_size=self._stats.avg_batch_size,
        )

    def get_stats(self) -> SchedulerStats:
        """获取统计

        Returns:
            统计信息
        """
        return self._stats

    def get_queue_size(self) -> int:
        """获取队列大小

        Returns:
            队列大小
        """
        return len(self._pending_tasks)

    def clear_queue(self) -> None:
        """清空队列"""
        self._pending_tasks.clear()


def main():
    """主函数：演示任务调度器"""
    print("=" * 80)
    print("📋 任务调度器演示")
    print("=" * 80)

    # 创建调度器
    scheduler = TaskScheduler(max_batch_size=5, quota_limit=160000)

    # 添加测试任务
    print("\n📝 添加测试任务...")
    tasks = [
        ("写一个排序函数", TaskPriority.HIGH, 500),
        ("分析这个项目", TaskPriority.MEDIUM, 2000),
        ("修复这个 bug", TaskPriority.URGENT, 800),
        ("生成测试代码", TaskPriority.MEDIUM, 600),
        ("优化性能", TaskPriority.LOW, 1500),
        ("重构代码", TaskPriority.LOW, 1200),
        ("写文档", TaskPriority.LOW, 400),
        ("调试问题", TaskPriority.HIGH, 700),
    ]

    task_ids = []
    for query, priority, tokens in tasks:
        task_id = scheduler.add_task(query, priority, tokens)
        task_ids.append(task_id)
        print(f"  ✓ {priority.value} 优先级: {query} (预估 {tokens} tokens)")

    # 获取第一批任务
    print("\n📦 获取第一批任务...")
    batch1 = scheduler.get_next_batch()
    print(f"  批次大小: {len(batch1)}")
    for task in batch1:
        print(f"    - {task.priority.value}: {task.query}")

    # 标记完成
    print("\n✅ 标记任务完成...")
    for task in batch1:
        tokens = task.estimated_tokens
        scheduler.mark_completed(task.task_id, tokens, success=True)
        print(f"  ✓ {task.query} (使用 {tokens} tokens)")

    # 显示统计
    print("\n" + "=" * 80)
    print("📊 调度器统计")
    print("=" * 80)
    stats = scheduler.get_stats()
    print(f"  总任务数: {stats.total_tasks}")
    print(f"  已完成: {stats.completed_tasks}")
    print(f"  失败: {stats.failed_tasks}")
    print(f"  总 Token: {stats.total_tokens_used:,}")
    print(f"  完成率: {stats.get_completion_rate() * 100:.1f}%")
    print(f"  队列大小: {scheduler.get_queue_size()}")

    # 获取第二批任务
    print("\n📦 获取第二批任务...")
    batch2 = scheduler.get_next_batch()
    print(f"  批次大小: {len(batch2)}")
    for task in batch2:
        print(f"    - {task.priority.value}: {task.query}")

    print("\n" + "=" * 80)
    print("✅ 任务调度器演示完成！")
    print("=" * 80)

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
