"""会话索引层（SQLite WAL）—— list_sessions / --continue 的快路径。

来源（2026-10-01 借鉴评估复审）：codex 六库分立 + opencode 中心化 sqlite 共同
验证的模式。json 仍是唯一事实源（可随时 rm 索引重建——自愈设计），索引只回答
"有哪些会话"的查询，不存消息体。

单写者场景（每台机一个人用）+ WAL：足够安全，无需复杂事务。
文件锁不引入：save 路径已持 file_edit_lock（session_persist.persist_session
的写互斥），索引 upsert 在锁内执行，天然串行化。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 目录名排除清单：sessions 根下的非项目目录。
# session/ = StateStore 的 record_type 目录（2026-10-01 污染发现：72 个
# state record 被 _list_sessions_in 扫成假会话，68 个与 lingclaude/ 重复）。
NON_PROJECT_DIRS = frozenset({"session", "_snapshots", "_index"})


def sessions_db_path(save_dir: Path) -> Path:
    return save_dir / "_index" / "sessions.db"


class SessionIndex:
    """sqlite 会话索引。json 为主、索引为查询缓存——坏即重建。"""

    def __init__(self, save_dir: Path) -> None:
        self._save_dir = save_dir
        self._db_path = sessions_db_path(save_dir)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    # ---------- 连接与 schema ----------

    def _connect(self) -> sqlite3.Connection | None:
        """惰性连接 + schema 初始化。sqlite 不可用时返回 None（调用方回退 json 扫描）。"""
        if self._conn is not None:
            return self._conn
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=5.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id  TEXT PRIMARY KEY,
                    project_dir TEXT NOT NULL,
                    project_path TEXT NOT NULL DEFAULT '',
                    project_name TEXT NOT NULL DEFAULT '',
                    created_at  TEXT NOT NULL DEFAULT '',
                    mtime       REAL NOT NULL DEFAULT 0,
                    size        INTEGER NOT NULL DEFAULT 0,
                    summary     TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_proj_created "
                "ON sessions(project_dir, created_at DESC)"
            )
            conn.commit()
            self._conn = conn
            return self._conn
        except sqlite3.Error as e:
            logger.warning("session_index 连接失败（回退 json 扫描）: %s", e)
            return None

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    # ---------- 写入（save 路径在 file_edit_lock 内调用） ----------

    def upsert(self, session_id: str, path: Path, payload: dict[str, Any],
               project_dir: str = "") -> None:
        """save 后同步索引。summary 取前 3 条非斜杠消息各 30 字（与
        SessionManager._list_sessions_in 口径一致——两处口径必须同步改）。

        project_dir: 项目目录名。空串时由 path 推导（global 模式 = 父目录名；
        save_dir 根直落 = ""，对应 _session_path 的非全局分支）。
        """
        if not project_dir:
            parent = path.parent.name
            project_dir = "" if parent == self._save_dir.name else parent
        conn = self._connect()
        if conn is None:
            return
        try:
            st = path.stat()
            mtime, size = st.st_mtime, st.st_size
        except OSError:
            mtime, size = 0.0, 0
        conn.execute(
            """
            INSERT INTO sessions (session_id, project_dir, project_path, project_name,
                                  created_at, mtime, size, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                project_dir=excluded.project_dir,
                project_path=excluded.project_path,
                project_name=excluded.project_name,
                created_at=excluded.created_at,
                mtime=excluded.mtime, size=excluded.size, summary=excluded.summary
            """,
            (
                session_id,
                project_dir,
                str(payload.get("project_path", "")),
                str(payload.get("project_name", "")),
                str(payload.get("created_at", "")),
                mtime,
                size,
                _summary_of(payload),
            ),
        )
        conn.commit()

    def remove(self, session_id: str) -> None:
        conn = self._connect()
        if conn is None:
            return
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        conn.commit()

    # ---------- 查询（list_sessions / --continue 快路径） ----------

    def list_rows(self, project_dir: str | None = None,
                  include_default_orphans: bool = False,
                  all_projects: bool = False) -> list[dict[str, str]] | None:
        """返回 None = 索引不可用/未建，调用方回退 json 全量扫描。

        project_dir: 限定项目目录名（对应 _list_sessions_in(target_dir)）。
        all_projects: 全项目列举（对应 list_sessions() 无参路径）。
        include_default_orphans: 并入 _default 中 project_path 为空的孤儿会话
        （2026-09-15 P1-2 口径：跨项目 --continue 过滤的历史兼容）。
        """
        conn = self._connect()
        if conn is None:
            return None
        try:
            if conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0:
                return None  # 空索引 → 触发上层重建
            rows: list[dict[str, str]] = []
            if all_projects:
                cur = conn.execute(
                    "SELECT session_id, project_dir, project_path, project_name, created_at, summary "
                    "FROM sessions ORDER BY created_at DESC"
                )
                for r in cur.fetchall():
                    rows.append(_row_to_dict(r))
                return rows
            # project_dir 匹配：global 布局=子目录名；非 global 布局=根部("")。
            # 用 IN ('<dir>', '') 兼容两种布局（根部行在 global 布局下不存在，无副作用）。
            if project_dir:
                cur = conn.execute(
                    "SELECT session_id, project_dir, project_path, project_name, created_at, summary "
                    "FROM sessions WHERE project_dir IN (?, '') ORDER BY created_at DESC",
                    (project_dir,),
                )
                for r in cur.fetchall():
                    rows.append(_row_to_dict(r))
            if include_default_orphans:
                cur = conn.execute(
                    "SELECT session_id, project_dir, project_path, project_name, created_at, summary "
                    "FROM sessions WHERE (project_dir = '_default' OR project_dir = '') "
                    "AND project_path = '' "
                    "ORDER BY created_at DESC"
                )
                for r in cur.fetchall():
                    rows.append(_row_to_dict(r))
            return rows
        except sqlite3.Error as e:
            logger.warning("session_index 查询失败（回退 json 扫描）: %s", e)
            return None

    # ---------- 自愈：从 json 重建 ----------

    def rebuild_from_json(self) -> int:
        """全量重建索引。json 是事实源；索引损坏/为空/手动 rm 后调用即自愈。"""
        conn = self._connect()
        if conn is None:
            return 0
        try:
            conn.execute("DELETE FROM sessions")
            count = 0
            # 布局一（global）：save_dir/<proj_dir>/*.json
            for proj_dir in sorted(self._save_dir.iterdir()):
                if not proj_dir.is_dir() or proj_dir.name in NON_PROJECT_DIRS:
                    continue
                for p in sorted(proj_dir.glob("*.json")):
                    count += self._rebuild_one(conn, p, proj_dir.name)
            # 布局二（非 global 注入 save_dir）：save_dir/*.json 直落根部
            # （_session_path 非全局分支），project_dir 记 ""。
            for p in sorted(self._save_dir.glob("*.json")):
                if p.stem.startswith("snapshot_"):
                    continue
                count += self._rebuild_one(conn, p, "")
            conn.commit()
            return count
        except (sqlite3.Error, OSError) as e:
            logger.warning("session_index 重建失败: %s", e)
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            return 0

    def _rebuild_one(self, conn: sqlite3.Connection, p: Path, proj_dir_name: str) -> int:
        if p.stem.startswith("snapshot_"):
            return 0
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            return 0  # 损坏档跳过，与 json 扫描口径一致
        conn.execute(
            "INSERT OR REPLACE INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                p.stem,
                proj_dir_name,
                str(data.get("project_path", "")),
                str(data.get("project_name", "")),
                str(data.get("created_at", "")),
                _mtime_of(p),
                p.stat().st_size if p.exists() else 0,
                _summary_of(data),
            ),
        )
        return 1

    def ensure_ready(self) -> bool:
        """索引可用性保证：空/坏则重建。返回 False = sqlite 完全不可用。"""
        if self._connect() is None:
            return False
        try:
            if self._conn is not None and self._conn.execute(
                "SELECT COUNT(*) FROM sessions"
            ).fetchone()[0] == 0:
                self.rebuild_from_json()
        except sqlite3.Error:
            return False
        return True


# ---------- 模块级助手 ----------

def _summary_of(payload: dict[str, Any]) -> str:
    """摘要口径：前 3 条非空非斜杠消息各 30 字（与 _list_sessions_in 同步）。"""
    parts: list[str] = []
    for m in payload.get("messages", ()) or ():
        if isinstance(m, str) and m.strip() and m.strip()[:1] != "/":
            parts.append(m.strip())
        if len(parts) >= 3:
            break
    return " | ".join(x[:30] for x in parts) or "(空会话)"


def _mtime_of(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _row_to_dict(r: tuple) -> dict[str, str]:
    return {
        "session_id": r[0],
        "project_dir": r[1],
        "project_path": r[2],
        "project_name": r[3],
        "created_at": r[4],
        "summary": r[5],
    }
