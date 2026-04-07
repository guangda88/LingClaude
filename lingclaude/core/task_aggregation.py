"""Task Aggregation for Batch Processing

功能：
- 识别相关任务
- 批量处理相关任务
- 减少初始化开销
- 预期节省：20% tokens
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class TaskPriority(str, Enum):
    """任务优先级"""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TaskStatus(str, Enum):
    """任务状态"""
    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class Task:
    """任务"""
    id: str
    query: str
    task_type: str
    priority: TaskPriority = TaskPriority.MEDIUM
    context: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_relevance_key(self) -> str:
        """获取相关性键值

        Returns:
            相关性键值（用于任务分组）
        """
        # 基于任务类型和上下文文件
        key_parts = [self.task_type]

        # 添加文件路径
        files = self.context.get("files", [])
        if files:
            # 使用第一个文件的目录作为键值的一部分
            first_file = files[0]
            file_path = Path(first_file)
            key_parts.append(str(file_path.parent))

        return "|".join(key_parts)


@dataclass(frozen=True)
class TaskGroup:
    """任务组"""
    id: str
    tasks: tuple[Task, ...]
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: TaskStatus = TaskStatus.PENDING

    @property
    def size(self) -> int:
        """获取任务数量"""
        return len(self.tasks)

    @property
    def combined_query(self) -> str:
        """获取合并后的查询"""
        queries = [f"任务 {i+1}: {task.query}" for i, task in enumerate(self.tasks)]
        return "\n\n".join(queries)


@dataclass(frozen=True)
class AggregationStats:
    """聚合统计"""
    total_tasks: int = 0
    total_groups: int = 0
    batched_tasks: int = 0
    standalone_tasks: int = 0
    avg_group_size: float = 0.0
    tokens_saved: int = 0  # 估算节省的 tokens


class TaskAggregator:
    """任务聚合器"""

    def __init__(
        self,
        db_path: str | Path | None = None,
        max_group_size: int = 5,
        max_wait_seconds: int = 30,
    ):
        """初始化聚合器

        Args:
            db_path: 数据库路径
            max_group_size: 最大组大小
            max_wait_seconds: 最大等待时间（秒）
        """
        self.max_group_size = max_group_size
        self.max_wait_seconds = max_wait_seconds

        if db_path is None:
            db_path = Path.home() / ".lingclaude" / "task_aggregation.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """初始化数据库"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        # 任务表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                query TEXT NOT NULL,
                task_type TEXT NOT NULL,
                priority TEXT NOT NULL,
                context TEXT,
                created_at TEXT NOT NULL,
                metadata TEXT,
                status TEXT NOT NULL
            )
        """)

        # 任务组表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_groups (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                relevance_key TEXT NOT NULL
            )
        """)

        # 任务-组关联表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_group_members (
                group_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                PRIMARY KEY (group_id, task_id),
                FOREIGN KEY (group_id) REFERENCES task_groups(id),
                FOREIGN KEY (task_id) REFERENCES tasks(id)
            )
        """)

        # 索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_status
            ON tasks(status)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_created_at
            ON tasks(created_at)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_task_groups_relevance
            ON task_groups(relevance_key)
        """)

        conn.commit()
        conn.close()

    def add_task(
        self,
        query: str,
        task_type: str,
        priority: TaskPriority = TaskPriority.MEDIUM,
        context: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """添加任务

        Args:
            query: 查询内容
            task_type: 任务类型
            priority: 任务优先级
            context: 上下文信息
            metadata: 元数据

        Returns:
            任务 ID
        """
        import uuid

        task_id = f"task_{uuid.uuid4().hex[:12]}"
        context = context or {}
        metadata = metadata or {}

        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO tasks
            (id, query, task_type, priority, context, created_at, metadata, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            task_id,
            query,
            task_type,
            priority.value,
            json.dumps(context, ensure_ascii=False),
            datetime.now(timezone.utc).isoformat(),
            json.dumps(metadata, ensure_ascii=False),
            TaskStatus.PENDING.value,
        ))

        conn.commit()
        conn.close()

        return task_id

    def _get_pending_tasks(self, limit: int | None = None) -> list[dict]:
        """获取待处理任务

        Args:
            limit: 数量限制

        Returns:
            任务列表
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        query = """
            SELECT id, query, task_type, priority, context, created_at, metadata
            FROM tasks
            WHERE status = ?
            ORDER BY created_at ASC
        """
        params = [TaskStatus.PENDING.value]

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        tasks = []
        for row in rows:
            task = Task(
                id=row[0],
                query=row[1],
                task_type=row[2],
                priority=TaskPriority(row[3]),
                context=json.loads(row[4]) if row[4] else {},
                created_at=row[5],
                metadata=json.loads(row[6]) if row[6] else {},
            )
            tasks.append(task)

        return tasks

    def _group_tasks_by_relevance(self, tasks: list[Task]) -> dict[str, list[Task]]:
        """按相关性分组任务

        Args:
            tasks: 任务列表

        Returns:
            {相关性键值: 任务列表}
        """
        groups = defaultdict(list)

        for task in tasks:
            key = task.get_relevance_key()
            groups[key].append(task)

        return dict(groups)

    def aggregate_tasks(self) -> list[TaskGroup]:
        """聚合任务

        Returns:
            任务组列表
        """
        # 获取待处理任务
        pending_tasks = self._get_pending_tasks(limit=self.max_group_size * 10)

        if not pending_tasks:
            return []

        # 按相关性分组
        relevance_groups = self._group_tasks_by_relevance(pending_tasks)

        # 创建任务组
        task_groups = []
        import uuid

        for key, tasks in relevance_groups.items():
            # 限制组大小
            if len(tasks) > self.max_group_size:
                tasks = tasks[:self.max_group_size]

            # 只有多于一个任务时才创建组
            if len(tasks) > 1:
                group_id = f"group_{uuid.uuid4().hex[:12]}"

                # 保存任务组
                conn = sqlite3.connect(str(self.db_path))
                cursor = conn.cursor()

                cursor.execute("""
                    INSERT INTO task_groups
                    (id, created_at, status, relevance_key)
                    VALUES (?, ?, ?, ?)
                """, (
                    group_id,
                    datetime.now(timezone.utc).isoformat(),
                    TaskStatus.QUEUED.value,
                    key,
                ))

                # 关联任务
                for task in tasks:
                    cursor.execute("""
                        INSERT INTO task_group_members
                        (group_id, task_id)
                        VALUES (?, ?)
                    """, (group_id, task.id))

                    # 更新任务状态
                    cursor.execute("""
                        UPDATE tasks
                        SET status = ?
                        WHERE id = ?
                    """, (TaskStatus.QUEUED.value, task.id))

                conn.commit()
                conn.close()

                # 创建任务组对象
                task_group = TaskGroup(
                    id=group_id,
                    tasks=tuple(tasks),
                    status=TaskStatus.QUEUED,
                )
                task_groups.append(task_group)

        return task_groups

    def get_task_group(self, group_id: str) -> TaskGroup | None:
        """获取任务组

        Args:
            group_id: 任务组 ID

        Returns:
            任务组或 None
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        # 获取任务组信息
        cursor.execute("""
            SELECT id, created_at, status
            FROM task_groups
            WHERE id = ?
        """, (group_id,))
        row = cursor.fetchone()

        if not row:
            conn.close()
            return None

        # 获取任务列表
        cursor.execute("""
            SELECT t.id, t.query, t.task_type, t.priority, t.context, t.created_at, t.metadata
            FROM tasks t
            INNER JOIN task_group_members m ON t.id = m.task_id
            WHERE m.group_id = ?
            ORDER BY t.created_at ASC
        """, (group_id,))
        task_rows = cursor.fetchall()
        conn.close()

        tasks = []
        for task_row in task_rows:
            task = Task(
                id=task_row[0],
                query=task_row[1],
                task_type=task_row[2],
                priority=TaskPriority(task_row[3]),
                context=json.loads(task_row[4]) if task_row[4] else {},
                created_at=task_row[5],
                metadata=json.loads(task_row[6]) if task_row[6] else {},
            )
            tasks.append(task)

        return TaskGroup(
            id=row[0],
            tasks=tuple(tasks),
            created_at=row[1],
            status=TaskStatus(row[2]),
        )

    def mark_group_completed(self, group_id: str) -> None:
        """标记任务组为完成

        Args:
            group_id: 任务组 ID
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        # 更新任务组状态
        cursor.execute("""
            UPDATE task_groups
            SET status = ?
            WHERE id = ?
        """, (TaskStatus.COMPLETED.value, group_id))

        # 更新所有任务状态
        cursor.execute("""
            UPDATE tasks
            SET status = ?
            WHERE id IN (SELECT task_id FROM task_group_members WHERE group_id = ?)
        """, (TaskStatus.COMPLETED.value, group_id))

        conn.commit()
        conn.close()

    def get_stats(self) -> AggregationStats:
        """获取聚合统计

        Returns:
            聚合统计
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        # 总任务数
        cursor.execute("SELECT COUNT(*) FROM tasks")
        total_tasks = cursor.fetchone()[0] or 0

        # 总组数
        cursor.execute("SELECT COUNT(*) FROM task_groups")
        total_groups = cursor.fetchone()[0] or 0

        # 批量任务数
        cursor.execute("""
            SELECT COUNT(DISTINCT m.task_id)
            FROM task_group_members m
            INNER JOIN task_groups g ON m.group_id = g.id
        """)
        batched_tasks = cursor.fetchone()[0] or 0

        # 独立任务数
        cursor.execute("""
            SELECT COUNT(*)
            FROM tasks
            WHERE id NOT IN (SELECT task_id FROM task_group_members)
        """)
        standalone_tasks = cursor.fetchone()[0] or 0

        # 平均组大小
        cursor.execute("""
            SELECT AVG(cnt)
            FROM (
                SELECT COUNT(*) as cnt
                FROM task_group_members
                GROUP BY group_id
            )
        """)
        avg_group_size = cursor.fetchone()[0] or 0.0

        # 估算节省的 tokens（假设每次批量处理节省 5000 tokens）
        tokens_saved = batched_tasks * 5000

        conn.close()

        return AggregationStats(
            total_tasks=total_tasks,
            total_groups=total_groups,
            batched_tasks=batched_tasks,
            standalone_tasks=standalone_tasks,
            avg_group_size=avg_group_size,
            tokens_saved=tokens_saved,
        )


def main():
    """主函数：测试任务聚合"""
    print("=" * 80)
    print("📦 任务聚合测试")
    print("=" * 80)

    # 创建聚合器
    aggregator = TaskAggregator(max_group_size=5, max_wait_seconds=30)

    # 添加测试任务
    print("\n➕ 添加测试任务...")

    # 相关任务 1（同一目录的代码生成）
    aggregator.add_task(
        query="写一个 hello world 函数",
        task_type="code_generation",
        context={"files": ["/home/ai/project/utils.py"]},
        priority=TaskPriority.HIGH,
    )

    # 相关任务 2（同一目录的代码生成）
    aggregator.add_task(
        query="写一个日志函数",
        task_type="code_generation",
        context={"files": ["/home/ai/project/utils.py"]},
        priority=TaskPriority.HIGH,
    )

    # 相关任务 3（同一目录的代码生成）
    aggregator.add_task(
        query="写一个配置加载函数",
        task_type="code_generation",
        context={"files": ["/home/ai/project/config.py"]},
        priority=TaskPriority.MEDIUM,
    )

    # 独立任务（不同目录）
    aggregator.add_task(
        query="分析整个项目架构",
        task_type="analysis",
        context={"files": ["/home/ai/project/"]},
        priority=TaskPriority.LOW,
    )

    print("✓ 已添加 4 个任务")

    # 聚合任务
    print("\n🔄 聚合任务...")
    groups = aggregator.aggregate_tasks()

    print("✓ 创建了", len(groups), "个任务组")

    # 显示任务组
    for i, group in enumerate(groups, 1):
        print(f"\n任务组 {i}:")
        print(f"  ID: {group.id}")
        print(f"  任务数: {group.size}")
        print(f"  状态: {group.status}")
        print("  合并查询（前 200 字符）:")
        for j, task in enumerate(group.tasks, 1):
            print(f"    {j}. {task.query[:100]}...")

    # 统计
    print("\n" + "=" * 80)
    print("📊 聚合统计")
    print("=" * 80)
    stats = aggregator.get_stats()
    print(f"  总任务数: {stats.total_tasks}")
    print(f"  总组数: {stats.total_groups}")
    print(f"  批量任务数: {stats.batched_tasks}")
    print(f"  独立任务数: {stats.standalone_tasks}")
    print(f"  平均组大小: {stats.avg_group_size:.1f}")
    print(f"  估算节省 tokens: {stats.tokens_saved:,}")

    # 模拟处理完成
    print("\n✅ 标记任务组为完成...")
    for group in groups:
        aggregator.mark_group_completed(group.id)
    print(f"✓ 已完成 {len(groups)} 个任务组")

    print("\n" + "=" * 80)
    print("✅ 任务聚合测试完成！")
    print("=" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(main())
