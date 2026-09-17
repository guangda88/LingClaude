"""P0-1: Todo list tool — 对标 AtomCode todo tool / DSH tool-todo.

提供任务创建/查询/完成/列表能力，提升长任务可追踪性。
会话内跨轮次持久化（SQLite），与 session 生命周期绑定。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class TodoStatus(str, Enum):
    """Lifecycle: pending → in_progress → completed / cancelled."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class TodoItem:
    """A single todo item, serialisable to JSON for tool result."""

    id: str
    content: str
    status: TodoStatus
    created_at: float
    updated_at: float
    priority: int = 0  # higher = more urgent
    tags: list[str] = field(default_factory=list)
    parent_id: str | None = None  # for sub-tasks

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "status": self.status.value,
            "priority": self.priority,
            "tags": self.tags,
            "parent_id": self.parent_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ---------------------------------------------------------------------------
# Store (SQLite-backed, session-scoped)
# ---------------------------------------------------------------------------


class TodoStore:
    """SQLite-backed persistent todo store, one DB per session_id."""

    _DDL = """
    CREATE TABLE IF NOT EXISTS todos (
        id          TEXT PRIMARY KEY,
        session_id  TEXT NOT NULL,
        content     TEXT NOT NULL,
        status      TEXT NOT NULL DEFAULT 'pending',
        priority    INTEGER NOT NULL DEFAULT 0,
        tags        TEXT NOT NULL DEFAULT '[]',
        parent_id   TEXT,
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_todos_session ON todos(session_id);
    CREATE INDEX IF NOT EXISTS idx_todos_parent  ON todos(parent_id);
    """

    def __init__(self, db_path: str | Path, session_id: str):
        self.db_path = Path(db_path)
        self.session_id = session_id
        # 2026-09-17 Bug B 修复：工具执行器每轮可能在不同线程调用同一 store，
        # 连接若缓存在实例属性上会跨线程复用，被 sqlite3 默认
        # check_same_thread=True 拒绝（报错 "SQLite objects created in a thread
        # can only be used in that same thread"）。改为 threading.local，
        # 每线程各自持一条连接，天然线程安全且无锁开销。
        self._local = threading.local()

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            # timeout=5: 多线程并发写同一库文件时等待锁而非立即 OperationalError
            conn = sqlite3.connect(
                str(self.db_path), autocommit=True, timeout=5.0
            )
            # 2026-09-17: 统一 Row 工厂（Bug A 修复，随 d626120 入库）——原连接
            # 无 row_factory，_row_to_item 与 _release_in_progress 对 r["id"]
            # 的字典式访问在默认元组行下会 TypeError。设 Row 后全库访问一致。
            conn.row_factory = sqlite3.Row
            conn.executescript(self._DDL)
            self._local.conn = conn
        return conn

    def add(self, item: TodoItem) -> None:
        conn = self._connect()
        conn.execute(
            """INSERT INTO todos
               (id,session_id,content,status,priority,tags,parent_id,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                item.id,
                self.session_id,
                item.content,
                item.status.value,
                item.priority,
                json.dumps(item.tags),
                item.parent_id,
                item.created_at,
                item.updated_at,
            ),
        )

    def get(self, id: str) -> TodoItem | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM todos WHERE id=? AND session_id=?",
            (id, self.session_id),
        ).fetchone()
        return self._row_to_item(row) if row else None

    def list(
        self,
        status: TodoStatus | None = None,
        tags: list[str] | None = None,
    ) -> list[TodoItem]:
        conn = self._connect()
        query = "SELECT * FROM todos WHERE session_id=?"
        params: list[Any] = [self.session_id]
        if status:
            query += " AND status=?"
            params.append(status.value)
        if tags:
            for t in tags:
                query += " AND tags LIKE ?"
                params.append(f"%{t}%")
        query += " ORDER BY priority DESC, created_at ASC"
        rows = conn.execute(query, params).fetchall()
        return [self._row_to_item(r) for r in rows]

    def update_status(self, id: str, status: TodoStatus) -> bool:
        conn = self._connect()
        updated = conn.execute(
            "UPDATE todos SET status=?, updated_at=? WHERE id=? AND session_id=?",
            (status.value, time.time(), id, self.session_id),
        ).rowcount
        return updated > 0

    # ------------------------------------------------------------------
    # 状态纪律（2026-09-17，对标 AtomCode todowrite）：
    #  ① 恰好一个 in_progress —— start(item) 时自动把其他 in_progress
    #     退回 pending（"当前执行项"切换语义）；
    #  ② 中断退回 —— 被覆盖的 in_progress 项回到 pending 而非 completed；
    #  ③ 禁批量刷绿 —— complete() 一次只动一项，不提供 all_completed。
    # ------------------------------------------------------------------

    def _release_in_progress(self, conn: sqlite3.Connection, exclude_id: str) -> list[str]:
        """把除 exclude_id 外的所有 in_progress 退回 pending，返回被退回的 id。"""
        rows = conn.execute(
            "SELECT id FROM todos WHERE session_id=? AND status=? AND id!=?",
            (self.session_id, TodoStatus.IN_PROGRESS.value, exclude_id),
        ).fetchall()
        now = time.time()
        for r in rows:
            row_id = r["id"] if isinstance(r, sqlite3.Row) else r[0]
            conn.execute(
                "UPDATE todos SET status=?, updated_at=? WHERE id=? AND session_id=?",
                (TodoStatus.PENDING.value, now, row_id, self.session_id),
            )
        return [
            r["id"] if isinstance(r, sqlite3.Row) else r[0] for r in rows
        ]

    def start_item(self, id: str) -> dict:
        """纪律化 start：置该项 in_progress，同时把其他 in_progress 退回 pending。

        返回 {ok, id, released:[...]}，released 是被中断退回 pending 的项。
        """
        conn = self._connect()
        target = conn.execute(
            "SELECT id FROM todos WHERE id=? AND session_id=?",
            (id, self.session_id),
        ).fetchone()
        if not target:
            return {"ok": False, "id": id, "error": "not_found"}
        released = self._release_in_progress(conn, id)
        conn.execute(
            "UPDATE todos SET status=?, updated_at=? WHERE id=? AND session_id=?",
            (TodoStatus.IN_PROGRESS.value, time.time(), id, self.session_id),
        )
        return {"ok": True, "id": id, "status": "in_progress", "released": released}

    def active_items(self) -> list[TodoItem]:
        """待办视图数据源：未完成项（in_progress + pending）按 priority 降序。

        已完成/已取消不显示在活跃面板（历史可经 list() 全量查）。
        """
        return [
            i for i in self.list()
            if i.status in (TodoStatus.IN_PROGRESS, TodoStatus.PENDING)
        ]

    def delete(self, id: str) -> bool:
        conn = self._connect()
        deleted = conn.execute(
            "DELETE FROM todos WHERE id=? AND session_id=?", (id, self.session_id)
        ).rowcount
        return deleted > 0

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> TodoItem:
        # session_id 不入 TodoItem（它是 store 作用域，已隐式归属）——
        # 原实现误传 session_id kwarg，TodoItem dataclass 无此字段。
        return TodoItem(
            id=row["id"],
            content=row["content"],
            status=TodoStatus(row["status"]),
            priority=row["priority"],
            tags=json.loads(row["tags"]),
            parent_id=row["parent_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# ---------------------------------------------------------------------------
# Handlers (called by CodingRuntime via registry)
# ---------------------------------------------------------------------------

import uuid


def make_handlers(store: TodoStore) -> dict:
    """Return a dict of handler callables for each todo sub-command."""

    def create(
        content: str,
        priority: int = 0,
        tags: list[str] | None = None,
        parent_id: str | None = None,
    ) -> dict:
        """Create a new todo item."""
        now = time.time()
        item = TodoItem(
            id=str(uuid.uuid4())[:8],
            content=content,
            status=TodoStatus.PENDING,
            created_at=now,
            updated_at=now,
            priority=priority,
            tags=tags or [],
            parent_id=parent_id,
        )
        store.add(item)
        return {"ok": True, "todo": item.to_dict()}

    def list_todos(
        status: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """List todos, optionally filtered by status or tags."""
        st = TodoStatus(status) if status else None
        items = store.list(status=st, tags=tags)
        return {
            "ok": True,
            "count": len(items),
            "todos": [i.to_dict() for i in items],
        }

    def complete(id: str) -> dict:
        """Mark a todo as completed."""
        ok = store.update_status(id, TodoStatus.COMPLETED)
        return {"ok": ok, "id": id, "status": "completed"} if ok else {"ok": False, "error": "not_found"}

    def cancel(id: str) -> dict:
        """Cancel a todo item."""
        ok = store.update_status(id, TodoStatus.CANCELLED)
        return {"ok": ok, "id": id, "status": "cancelled"} if ok else {"ok": False, "error": "not_found"}

    def start(id: str) -> dict:
        """Mark a todo as in_progress（走状态纪律：其他 in_progress 自动退回 pending）。"""
        result = store.start_item(id)
        if result["ok"]:
            return {"ok": True, "id": id, "status": "in_progress", "released": result["released"]}
        return {"ok": False, "error": "not_found"}

    def get(id: str) -> dict:
        """Get a single todo by id."""
        item = store.get(id)
        return {"ok": True, "todo": item.to_dict()} if item else {"ok": False, "error": "not_found"}

    def delete(id: str) -> dict:
        """Delete a todo item."""
        ok = store.delete(id)
        return {"ok": ok, "id": id, "deleted": ok} if not ok else {"ok": False, "error": "not_found"}

    return {
        "create": create,
        "list": list_todos,
        "complete": complete,
        "cancel": cancel,
        "start": start,
        "get": get,
        "delete": delete,
    }
