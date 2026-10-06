"""会话级任务清单存储（oc 式「文件即会话」布局，2026-10-07 重构）。

提供任务创建/查询/完成/列表能力，提升长任务可追踪性。
会话内跨轮次持久化（JSON），与 session 生命周期绑定。

布局（偷师 opencode `storage/todo/<ses_id>.json`）：
    单文件模式: <path>                      ← 一个文件 = 一个库，多 session 行内分桶
                                          （兼容既有测试 / env 共享旧语义路径）
    会话文件模式: <dir>/<session_id>.json    ← 一个文件 = 一个会话，会话结束文件即归档
                                          （生产装配走此模式，见 coding_wiring）

「文件即会话」的收益：单库多 session 行的堆积问题不复存在——旧会话的
任务随会话文件冷置，写入时惰性 GC（保留最近 MAX_SESSION_FILES 个会话
文件，mtime LRU），无需后台清理任务。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

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
# Store (JSON-backed, session-scoped; one file per session in dir mode)
# ---------------------------------------------------------------------------

# 会话文件 GC 上限：目录内保留最近 N 个会话文件（mtime LRU）。
MAX_SESSION_FILES = 50


def _safe_sid(session_id: str) -> str:
    """会话 id → 文件名安全串（防路径穿越/非法字符）。"""
    bad = '/\\<>:"|?*\0'
    out = "".join("_" if ch in bad or ord(ch) < 32 else ch for ch in session_id)
    return (out or "default").strip(". ") or "default"


class TodoStore:
    """JSON-backed persistent todo store.

    单文件模式（path 以 .db/.json 结尾）：一个文件内按 session_id 分桶，
    行为与旧 SQLite 版一致（既有测试 / 显式 env 共享语义）。
    会话文件模式（path 为目录）：<dir>/<session_id>.json 一个文件一个会话，
    写后惰性 GC 保留最近 MAX_SESSION_FILES 个会话。
    """

    def __init__(self, db_path: str | Path, session_id: "str | Callable[[], str]"):
        path = Path(db_path)
        # 会话级隔离（2026-10-07）：session_id 支持传 callable（如绑定 runtime
        # 的 lambda），property 每次访问动态解析 —— /resume、L2 压缩等换会话
        # 后，任务面板自动重绑到新会话，不再读旧 id 的存量行。
        self._session_id: "str | Callable[[], str]" = session_id
        if path.suffix in (".db", ".json"):
            # 单文件模式：一个文件多 session 桶（旧语义/测试用）
            self._file: Path | None = path
            self._root: Path | None = None
        else:
            # 会话文件模式：目录 + <sid>.json（oc 式，生产装配）
            self._file = None
            self._root = path
        # 2026-09-17 Bug B 修复（SQLite 时代遗留约束）：工具执行器每轮可能在
        # 不同线程调用同一 store。JSON 原子写天然线程安全（tempfile+replace），
        # 但读-改-写序列仍需互斥，保留实例级锁。
        self._lock = threading.Lock()

    # ---- 路径与会话解析 --------------------------------------------------

    @property
    def session_id(self) -> str:
        """当前会话 id（callable 时动态解析，随宿主 engine 换会话自动跟随）。"""
        sid = self._session_id
        return str(sid()) if callable(sid) else str(sid)

    @session_id.setter
    def session_id(self, value: "str | Callable[[], str]") -> None:
        self._session_id = value

    @property
    def db_path(self) -> Path:
        """当前会话的存储文件路径（会话文件模式下随 session_id 动态解析）。"""
        if self._root is not None:
            return self._root / f"{_safe_sid(self.session_id)}.json"
        assert self._file is not None
        return self._file

    # ---- 读写 ------------------------------------------------------------

    def _load_bucket(self) -> dict[str, list[dict[str, Any]]]:
        """读当前会话文件 → {session_id: [item_dict]}；坏文件容错为空。"""
        path = self.db_path
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as e:  # noqa: BLE001 — 读失败不阻断任务面板
            import logging

            logging.getLogger(__name__).warning("todo store 读失败 %s: %s", path, e)
            return {}
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            import logging

            logging.getLogger(__name__).warning(
                "todo store 文件损坏（JSON 解析失败，按空处理）: %s", path
            )
            return {}
        return data if isinstance(data, dict) else {}

    def _save_bucket(self, bucket: dict[str, list[dict[str, Any]]]) -> None:
        """原子写当前会话文件 + 会话文件模式下的惰性 GC。"""
        path = self.db_path
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(path.parent), prefix=path.name, suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(bucket, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, path)
        except OSError as e:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).warning("todo store 写失败 %s: %s", path, e)
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        if self._root is not None:
            self._gc_sessions(exclude=path)

    def _gc_sessions(self, exclude: Path) -> None:
        """惰性 GC：会话文件超过上限时按 mtime 淘汰最旧的（排除当前文件）。"""
        try:
            files = sorted(
                (p for p in self._root.glob("*.json") if p != exclude),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return
        for old in files[MAX_SESSION_FILES - 1:]:
            try:
                old.unlink(missing_ok=True)
            except OSError:  # noqa: PERF103 — 单文件删除失败不阻断
                continue

    def _items(self) -> list[TodoItem]:
        bucket = self._load_bucket()
        rows = bucket.get(self.session_id, [])
        items: list[TodoItem] = []
        for r in rows:
            try:
                items.append(
                    TodoItem(
                        id=r["id"],
                        content=r["content"],
                        status=TodoStatus(r["status"]),
                        priority=int(r.get("priority", 0)),
                        tags=list(r.get("tags", [])),
                        parent_id=r.get("parent_id"),
                        created_at=float(r.get("created_at", 0.0)),
                        updated_at=float(r.get("updated_at", 0.0)),
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue  # 坏行跳过，不阻断面板
        return items

    def _write_items(self, items: list[TodoItem]) -> None:
        bucket = self._load_bucket()
        bucket[self.session_id] = [i.to_dict() for i in items]
        self._save_bucket(bucket)

    # ---- 公共 API（与旧 SQLite 版签名一致）--------------------------------

    def add(self, item: TodoItem) -> None:
        with self._lock:
            items = self._items()
            items = [i for i in items if i.id != item.id]  # upsert 语义
            items.append(item)
            self._write_items(items)

    def get(self, id: str) -> TodoItem | None:
        with self._lock:
            for i in self._items():
                if i.id == id:
                    return i
            return None

    def list(
        self,
        status: TodoStatus | None = None,
        tags: list[str] | None = None,
    ) -> list[TodoItem]:
        with self._lock:
            items = self._items()
        if status is not None:
            items = [i for i in items if i.status == status]
        if tags:
            items = [i for i in items if any(t in i.tags for t in tags)]
        items.sort(key=lambda i: (-i.priority, i.created_at))
        return items

    def update_status(self, id: str, status: TodoStatus) -> bool:
        with self._lock:
            items = self._items()
            hit = False
            now = time.time()
            for i in items:
                if i.id == id:
                    i.status = status
                    i.updated_at = now
                    hit = True
            if hit:
                self._write_items(items)
            return hit

    # ------------------------------------------------------------------
    # 状态纪律（2026-09-17，对标 AtomCode todowrite）：
    #  ① 恰好一个 in_progress —— start(item) 时自动把其他 in_progress
    #     退回 pending（"当前执行项"切换语义）；
    #  ② 中断退回 —— 被覆盖的 in_progress 项回到 pending 而非 completed；
    #  ③ 禁批量刷绿 —— complete() 一次只动一项，不提供 all_completed。
    # ------------------------------------------------------------------

    def _release_in_progress(
        self, items: list[TodoItem], exclude_id: str
    ) -> list[str]:
        """把除 exclude_id 外的所有 in_progress 退回 pending，返回被退回的 id。"""
        now = time.time()
        released: list[str] = []
        for i in items:
            if i.id != exclude_id and i.status == TodoStatus.IN_PROGRESS:
                i.status = TodoStatus.PENDING
                i.updated_at = now
                released.append(i.id)
        return released

    def start_item(self, id: str) -> dict:
        """纪律化 start：置该项 in_progress，同时把其他 in_progress 退回 pending。

        守卫（atomcode B3 回归）：missing → not_found；已完成/已取消项
        不得复活 → already_finished。
        """
        with self._lock:
            items = self._items()
            target = next((i for i in items if i.id == id), None)
            if target is None:
                return {"ok": False, "id": id, "error": "not_found"}
            if target.status in (TodoStatus.COMPLETED, TodoStatus.CANCELLED):
                return {"ok": False, "id": id, "error": "already_finished"}
            released = self._release_in_progress(items, exclude_id=id)
            now = time.time()
            target.status = TodoStatus.IN_PROGRESS
            target.updated_at = now
            self._write_items(items)
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
        with self._lock:
            items = self._items()
            kept = [i for i in items if i.id != id]
            if len(kept) == len(items):
                return False
            self._write_items(kept)
            return True


# ---------------------------------------------------------------------------
# Legacy SQLite migration（2026-10-07 v2：sqlite 单库 → oc 式会话文件）
# ---------------------------------------------------------------------------


def migrate_legacy_sqlite_dbs(root: Path, legacy_paths: list[Path]) -> int:
    """把旧 SQLite 库（多 session 行）按 session_id 拆迁为会话文件。

    每库处理：按 session_id 分组 → 各写 <root>/<safe_sid>.json（与既有
    会话文件合并，只覆写该 sid 的桶）→ 全部成功后改名 *.migrated（保留
    备份不删除）。任一步失败即中止该库（部分写入无害：下次迁移按桶合并）。
    返回迁移的会话数。惰性触发：lc 在某目录下次启动时迁移该目录的哈希库，
    其余项目的旧库等各自目录下次启动 lc 时再迁。
    """
    import logging
    import sqlite3

    migrated = 0
    for legacy in legacy_paths:
        if not legacy.exists():
            continue
        try:
            conn = sqlite3.connect(str(legacy))
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM todos").fetchall()
            conn.close()
        except Exception as e:  # noqa: BLE001 — 迁移失败不阻断装配
            logging.getLogger(__name__).warning(
                "todo 旧库读取失败（跳过迁移）%s: %s", legacy, e
            )
            continue

        by_sid: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            sid = _safe_sid(str(r["session_id"] or "default"))
            by_sid.setdefault(sid, []).append(
                {
                    "id": r["id"],
                    "content": r["content"],
                    "status": r["status"],
                    "priority": int(r["priority"] or 0),
                    "tags": json.loads(r["tags"] or "[]"),
                    "parent_id": r["parent_id"],
                    "created_at": float(r["created_at"] or 0.0),
                    "updated_at": float(r["updated_at"] or 0.0),
                }
            )
        try:
            for sid, items in by_sid.items():
                if not items:
                    continue
                path = root / f"{sid}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    bucket = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(bucket, dict):
                        bucket = {}
                except (OSError, json.JSONDecodeError):
                    bucket = {}
                bucket[sid] = items
                path.write_text(
                    json.dumps(bucket, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                migrated += 1
            legacy.rename(Path(str(legacy) + ".migrated"))
        except OSError as e:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "todo 旧库迁移中断 %s: %s", legacy, e
            )
    return migrated


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
        return {"ok": True, "id": id, "deleted": True} if ok else {"ok": False, "error": "not_found"}

    return {
        "create": create,
        "list": list_todos,
        "complete": complete,
        "cancel": cancel,
        "start": start,
        "get": get,
        "delete": delete,
    }
