"""P0-1: Todo list tool — 对标 AtomCode todo tool / DSH tool-todo.

提供任务创建/查询/完成/列表能力，提升长任务可追踪性。
会话内跨轮次持久化（SQLite），与 session 生命周期绑定。
"""

from __future__ import annotations

import json
import sqlite3
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
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path), autocommit=True)
            self._conn.executescript(self._DDL)
        return self._conn

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

    def delete(self, id: str) -> bool:
        conn = self._connect()
        deleted = conn.execute(
            "DELETE FROM todos WHERE id=? AND session_id=?", (id, self.session_id)
        ).rowcount
        return deleted > 0

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> TodoItem:
        return TodoItem(
            id=row["id"],
            session_id=row["session_id"],
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
        """Mark a todo as in_progress."""
        ok = store.update_status(id, TodoStatus.IN_PROGRESS)
        return {"ok": ok, "id": id, "status": "in_progress"} if ok else {"ok": False, "error": "not_found"}

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
