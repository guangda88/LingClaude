"""SQLite 并发安全工具 — 防 SQLITE_BUSY.

所有 lingclaude SQLite 写入必须经过此模块，确保：
  1. WAL journal mode（读写不互斥）
  2. busy_timeout 30s（等锁而非立即失败）
  3. 同步提交带指数退避重试
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_BUSY_TIMEOUT_MS = 30000
_RETRY_ATTEMPTS = 5
_RETRY_BASE_DELAY = 0.1
_RETRY_MAX_DELAY = 2.0

_WRITE_RLOCK = threading.RLock()


def safe_connect(
    db_path: str | Path,
    *,
    busy_timeout: int = _BUSY_TIMEOUT_MS,
    foreign_keys: bool = True,
) -> sqlite3.Connection:
    """创建带并发保护的 SQLite 连接.

    Args:
        db_path: 数据库文件路径
        busy_timeout: 等锁超时毫秒数，默认 30s
        foreign_keys: 是否启用外键约束

    Returns:
        已配置好 WAL + busy_timeout 的连接
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute(f"PRAGMA busy_timeout={busy_timeout}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    if foreign_keys:
        conn.execute("PRAGMA foreign_keys=ON")
    return conn


def safe_execute(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> None:
    """带重试的 SQL 执行，专门对付 SQLITE_BUSY."""
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            conn.execute(sql, params)
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() and "busy" not in str(e).lower():
                raise
            if attempt == _RETRY_ATTEMPTS:
                raise
            delay = min(_RETRY_BASE_DELAY * (2 ** (attempt - 1)), _RETRY_MAX_DELAY)
            logger.warning("SQLITE_BUSY retry %d/%d, waiting %.1fs", attempt, _RETRY_ATTEMPTS, delay)
            time.sleep(delay)


def safe_commit(conn: sqlite3.Connection) -> None:
    """带重试的 commit，专门对付 SQLITE_BUSY."""
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            conn.commit()
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() and "busy" not in str(e).lower():
                raise
            if attempt == _RETRY_ATTEMPTS:
                raise
            delay = min(_RETRY_BASE_DELAY * (2 ** (attempt - 1)), _RETRY_MAX_DELAY)
            logger.warning("SQLITE_BUSY commit retry %d/%d, waiting %.1fs", attempt, _RETRY_ATTEMPTS, delay)
            time.sleep(delay)


def serialized_write(func):
    """装饰器：进程内串行化写操作（RLock）.

    跨进程安全由 SQLite WAL + busy_timeout 保证。
    用法同 lingmessage/lingbus.py 的 @_serialized_write。
    """
    def wrapper(*args, **kwargs):
        with _WRITE_RLOCK:
            return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    wrapper.__qualname__ = func.__qualname__
    return wrapper
