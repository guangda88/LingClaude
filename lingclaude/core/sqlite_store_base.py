"""SQLite store 公共基类 — 消除多副本样板（灵元：同一模式多副本 → 单源）。

MemoryStore / CognitiveStore / ExperienceStore / InMemoryExperienceStore
此前各自复制同一套 __init__ 骨架 / _emit / _get_conn / close，现收敛为单源基类。

子类职责：
  - 提供 _SCHEMA（或覆写 _init_db）——各 store 的表结构差异
  - 实现各自 CRUD 方法
  - 需要自定义 db 文件名时传 db_name；不连 SQLite 的 store 可覆写 _get_conn/_init_db
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from lingclaude.core.safe_db import safe_connect

logger = logging.getLogger(__name__)


class SqliteStoreBase:
    """SQLite 持久化 store 公共骨架。

    - 连接懒建立（_get_conn）
    - 旁路事件派发（_emit：永不抛异常、永不递归，P3.3 同款纪律）
    - 默认 db 路径（.lingclaude/<db_name>）
    - close 幂等（无连接时安全跳过）
    """

    #: 子类可覆盖：默认 schema 脚本（executescript 执行）；空则 _init_db 无操作
    _SCHEMA: str = ""

    def __init__(
        self,
        db_path: str | Path | None = None,
        legacy_sink: object | None = None,
        db_name: str = "store.db",
    ) -> None:
        if db_path is None:
            root = Path(__file__).parent.parent.parent / ".lingclaude"
            root.mkdir(parents=True, exist_ok=True)
            db_path = str(root / db_name)
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None
        # P3.3 双写：可选旁观者（如 LingMemoryStoreSink），主路零依赖。
        # self._emit 自指卫兵：无 sink 或在 sink 内部再触发 put 时跳过。
        self._legacy_sink = legacy_sink
        self._in_sink_emit = False
        self._init_db()

    def _emit(self, method: str, *args: object) -> None:
        """旁路事件派发：永不抛异常，永不递归（P3.3 第一件同款纪律）"""
        sink = getattr(self, "_legacy_sink", None)
        if sink is None or self._in_sink_emit:
            return
        self._in_sink_emit = True
        try:
            getattr(sink, method)(*args)
        except Exception as e:  # noqa: BLE001
            logger.warning("legacy_sink.%s 失败（已忽略）: %s", method, e)
        finally:
            self._in_sink_emit = False

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = safe_connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        if self._SCHEMA:
            conn = self._get_conn()
            conn.executescript(self._SCHEMA)
            conn.commit()

    def close(self) -> None:
        conn = getattr(self, "_conn", None)
        if conn is not None:
            conn.close()
            self._conn = None

    # ── 子类可覆写钩子（兼容非 SQLite store）──

    def _try_load_l7(self) -> bool:  # pragma: no cover - 子类可选覆写
        return False

    # 便于调试的统一描述
    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"<{type(self).__name__} db_path={getattr(self, '_db_path', None)!r}>"
