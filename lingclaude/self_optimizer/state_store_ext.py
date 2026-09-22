"""飞轮统一读 facade —— §M.6 裁定 B 修正版（虚拟视图层）落地。

M.6 裁定（2026-09-22，用户拍板 B 修正版，见
docs/research/20260922_flywheel_closure_diagnosis.md §M.6）：

- **逻辑合一、物理不动**：ATTACH ``data_flywheel.db`` 到 knowledge.db 连接，
  TEMP VIEW 提供跨库查询面；写路径不动（各写各的物理库）。
- **合并对象 4→3**：arch_ledger（git 指纹审计状态）移出合并名单。
  实际统一对象：knowledge.db（主库）+ data_flywheel.db + datalog/*.jsonl
  （经 P0#1 聚合为 snapshot 入 KB 后经本层可查）。
- **本层只读**：P0#2 经聚合管道写 KB（``flywheel_pattern_`` 前缀），
  走 KnowledgeBase 自身连接，不经过本层。

职责：
- M.8-C：FlywheelRecordType 枚举先就位；
- F0：health_counts() 提供四指标取数基线；
- P0#2：top_error_patterns() 提供聚合模式参考。
"""

from __future__ import annotations

import logging
import sqlite3
from enum import Enum
from pathlib import Path
from typing import Any

from lingclaude.core.safe_db import safe_connect

logger = logging.getLogger(__name__)

# P0#2 聚合规则写入 KB 时的统一前缀（M.6 裁定的制度先例）
FLYWHEEL_PATTERN_PREFIX = "flywheel_pattern_"

# P0#1 snapshot 入 KB 时约定的 category 值（= FeedbackCategory.TELEMETRY.value，
# 2026-09-22 从 "datalog_snapshot" 改齐枚举成员，见 learner/models.py TELEMETRY 注释）
DATALOG_SNAPSHOT_CATEGORY = "telemetry"


class FlywheelRecordType(str, Enum):
    """飞轮记录类型 —— 跨存储统一取数面的逻辑类型。"""

    KNOWLEDGE_RULE = "knowledge_rule"        # knowledge.db rules
    FLYWHEEL_ERROR = "flywheel_error"        # data_flywheel.db error_log
    FLYWHEEL_CORRECTION = "flywheel_correction"  # data_flywheel.db corrections
    DATALOG_SNAPSHOT = "datalog_snapshot"    # KB 内 category=datalog_snapshot 的规则行


# 各逻辑类型 → (TEMP VIEW, 过滤条件, 默认排序列)。排序列须在视图列内（防注入）。
_QUERY_MAP: dict[FlywheelRecordType, tuple[str, str, str]] = {
    FlywheelRecordType.KNOWLEDGE_RULE: (
        "v_knowledge_rules",
        "",
        "updated_at",
    ),
    FlywheelRecordType.FLYWHEEL_ERROR: (
        "v_flywheel_errors",
        "",
        "occurred_at",
    ),
    FlywheelRecordType.FLYWHEEL_CORRECTION: (
        "v_flywheel_corrections",
        "",
        "id",
    ),
    FlywheelRecordType.DATALOG_SNAPSHOT: (
        "v_knowledge_rules",
        "category = 'datalog_snapshot'",
        "updated_at",
    ),
}

_TEMP_VIEWS: dict[str, str] = {
    "v_knowledge_rules": (
        "SELECT id, name, description, category, frequency, confidence, "
        "quality_score, status, created_at, updated_at FROM rules"
    ),
    "v_flywheel_errors": (
        "SELECT id, pattern_type, file_path, error_message, tool_name, "
        "context, session_id, occurred_at FROM flywheel.error_log"
    ),
    "v_flywheel_corrections": (
        "SELECT id, original_error, correction, source "
        "FROM flywheel.corrections"
    ),
}


class FlywheelStateStore:
    """只读统一取数面：ATTACH data_flywheel.db，TEMP VIEW 跨库查询。

    用法::

        store = FlywheelStateStore()
        rows = store.query(FlywheelRecordType.FLYWHEEL_ERROR, limit=10)
        counts = store.health_counts()
        store.close()

    也可作 context manager（退出时自动 close）。
    """

    def __init__(self, project_root: Path | None = None) -> None:
        root = project_root or Path(__file__).resolve().parent.parent.parent
        self._main_path = root / ".lingclaude" / "knowledge.db"
        self._flywheel_path = root / ".lingclaude" / "data_flywheel.db"
        self._conn: sqlite3.Connection | None = None
        self._flywheel_attached = False

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------
    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = safe_connect(self._main_path)
            if self._flywheel_path.exists():
                attached = conn.execute(
                    "SELECT count(*) FROM pragma_database_list "
                    "WHERE name = 'flywheel'"
                ).fetchone()[0]
                if not attached:
                    conn.execute(
                        "ATTACH DATABASE ? AS flywheel",
                        (str(self._flywheel_path),),
                    )
                self._flywheel_attached = True
            else:
                logger.warning(
                    "data_flywheel.db 不存在（%s），flywheel 相关查询将返回空",
                    self._flywheel_path,
                )
            for name, select_sql in _TEMP_VIEWS.items():
                if not self._flywheel_attached and name.startswith("v_flywheel"):
                    continue  # 未挂载时不建 flywheel 视图，避免查询报 no such table
                conn.execute(
                    f"CREATE TEMP VIEW {name} AS {select_sql}"  # noqa: S608 - 常量拼接
                )
            # 只读 facade：ATTACH / TEMP VIEW 就绪后统一禁写
            conn.execute("PRAGMA query_only = ON")
            self._conn = conn
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            self._flywheel_attached = False

    def __enter__(self) -> "FlywheelStateStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # 查询 API
    # ------------------------------------------------------------------
    def query(
        self,
        record_type: FlywheelRecordType,
        *,
        limit: int = 100,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        """按逻辑类型取数，返回 dict 列表（列名 → 值）。"""
        view, where, default_order = _QUERY_MAP[record_type]
        order_col = order_by or default_order
        valid_cols = self._view_columns(view)
        if order_col not in valid_cols:
            raise ValueError(
                f"order_by 列 {order_col!r} 不在 {view} 列白名单内: {valid_cols}"
            )
        sql = f"SELECT * FROM {view}"
        if where:
            sql += f" WHERE {where}"
        sql += f" ORDER BY {order_col} DESC LIMIT ?"
        conn = self._connection()
        rows = conn.execute(sql, (limit,)).fetchall()
        return [dict(zip(valid_cols, row)) for row in rows]

    def top_error_patterns(self, limit: int = 10) -> list[dict[str, Any]]:
        """error_log 按 pattern_type 聚合 top-N（P0#2 DataFlywheel top-N 模式参考）。"""
        conn = self._connection()
        rows = conn.execute(
            """
            SELECT pattern_type, tool_name, count(*) AS cnt,
                   max(occurred_at) AS last_seen
            FROM v_flywheel_errors
            GROUP BY pattern_type, tool_name
            ORDER BY cnt DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        cols = ["pattern_type", "tool_name", "cnt", "last_seen"]
        return [dict(zip(cols, row)) for row in rows]

    def health_counts(self) -> dict[str, int]:
        """F0 基线取数：各存储现存行数（缺失存储记 0）。"""
        counts: dict[str, int] = {
            "knowledge_rules": 0,
            "datalog_snapshots": 0,
            "flywheel_errors": 0,
            "flywheel_corrections": 0,
        }
        conn = self._connection()
        counts["knowledge_rules"] = conn.execute(
            "SELECT count(*) FROM v_knowledge_rules"
        ).fetchone()[0]
        counts["datalog_snapshots"] = conn.execute(
            "SELECT count(*) FROM v_knowledge_rules "
            "WHERE category = ?",
            (DATALOG_SNAPSHOT_CATEGORY,),
        ).fetchone()[0]
        if self._flywheel_attached:
            counts["flywheel_errors"] = conn.execute(
                "SELECT count(*) FROM v_flywheel_errors"
            ).fetchone()[0]
            counts["flywheel_corrections"] = conn.execute(
                "SELECT count(*) FROM v_flywheel_corrections"
            ).fetchone()[0]
        return counts

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _view_columns(self, view: str) -> list[str]:
        cursor = self._connection().execute(
            f"SELECT * FROM {view} LIMIT 0"  # noqa: S608 - view 来自常量表
        )
        return [d[0] for d in cursor.description]
