"""SQLite 并发安全工具 — 防 SQLITE_BUSY + ro 文件系统回退.

所有 lingclaude SQLite 写入必须经过此模块，确保：
  1. WAL journal mode（读写不互斥）；ro 目录自动降级 DELETE
  2. busy_timeout 30s（等锁而非立即失败）
  3. 同步提交带指数退避重试
  4. H18 同族环境修复：~/.lingclaude 只读挂载（ro fs）时，
     自动回退到可写目录（LC_DB_FALLBACK_DIR > 项目 .lingclaude/db > tmp）
"""
from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_BUSY_TIMEOUT_MS = 30000
_RETRY_ATTEMPTS = 5
_RETRY_BASE_DELAY = 0.1
_RETRY_MAX_DELAY = 2.0

_WRITE_RLOCK = threading.RLock()

# 回退重定向记录：{原路径: 实际路径}（诊断用）
_redirects: dict[str, str] = {}


def fallback_dir() -> Path:
    """按优先级解析可写回退目录：env > 项目内 > tmp."""
    env = os.environ.get("LC_DB_FALLBACK_DIR")
    if env:
        return Path(env)
    # lingclaude/core/safe_db.py -> parents[2] = 项目根（源码运行形态）
    project_root = Path(__file__).resolve().parents[2]
    candidate = project_root / ".lingclaude" / "db"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write-probe"
        probe.touch()
        probe.unlink()
        return candidate
    except OSError:
        pass
    tmp = Path(tempfile.gettempdir()) / "lingclaude-db"
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


def _redirected_path(path: Path) -> Path:
    """若原路径已记录重定向，返回实际路径。"""
    actual = _redirects.get(str(path))
    return Path(actual) if actual else path


def _try_connect(path: Path, busy_timeout: int) -> sqlite3.Connection:
    """connect + 写探测（journal_mode 设置即写探测）。失败抛 OperationalError。"""
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute(f"PRAGMA busy_timeout={busy_timeout}")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        # ro 目录（无法建 -wal/-shm）或 ro db：降级 DELETE 再探测一次写能力
        try:
            conn.execute("PRAGMA journal_mode=DELETE")
        except sqlite3.OperationalError as e:
            conn.close()
            raise e
    try:
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.OperationalError:
        pass
    return conn


def safe_connect(
    db_path: str | Path,
    *,
    busy_timeout: int = _BUSY_TIMEOUT_MS,
    foreign_keys: bool = True,
) -> sqlite3.Connection:
    """创建带并发保护的 SQLite 连接（ro 环境自动回退）.

    Args:
        db_path: 数据库文件路径
        busy_timeout: 等锁超时毫秒数，默认 30s
        foreign_keys: 是否启用外键约束

    Returns:
        已配置好 WAL(或降级 DELETE) + busy_timeout 的连接
    """
    path = Path(_redirected_path(Path(db_path)))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # ro 父目录：mkdir 失败留给 _try_connect 探测兜底

    try:
        conn = _try_connect(path, busy_timeout)
    except sqlite3.OperationalError:
        # H18 同族：原路径所在文件系统只读 → 回退可写目录，同文件名保持可辨识
        fb = fallback_dir() / path.name
        if fb.resolve() == path.resolve():
            raise  # 已经在 fallback 上还失败，无路可退
        conn = _try_connect(fb, busy_timeout)
        _redirects[str(db_path)] = str(fb)
        logger.warning(
            "DB path %s unwritable (ro fs?), redirected to %s", db_path, fb
        )

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
