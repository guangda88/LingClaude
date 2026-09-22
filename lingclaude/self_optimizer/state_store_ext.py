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

# F4（2026-09-23）：返审值守报告的 KB category 值（= FeedbackCategory.AUDIT.value）
AUDIT_CATEGORY = "audit"


class FlywheelRecordType(str, Enum):
    """飞轮记录类型 —— 跨存储统一取数面的逻辑类型。"""

    KNOWLEDGE_RULE = "knowledge_rule"        # knowledge.db rules
    FLYWHEEL_ERROR = "flywheel_error"        # data_flywheel.db error_log
    FLYWHEEL_CORRECTION = "flywheel_correction"  # data_flywheel.db corrections
    DATALOG_SNAPSHOT = "datalog_snapshot"    # KB 内 category=TELEMETRY("telemetry") 的规则行
    AUDIT_REPORT = "audit_report"            # KB 内 category=audit 的值守报告行（F4）
    EXPERIMENT = "experiment"                # experiments.db experiments（F2 归因链）


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
        # 2026-09-23 F4 修正：原硬编码 'datalog_snapshot' 与写入侧
        # TELEMETRY("telemetry") 不一致（health_counts 用常量正确、
        # 本过滤为潜伏死路径，query 恒空）——统一引用常量。
        f"category = '{DATALOG_SNAPSHOT_CATEGORY}'",
        "updated_at",
    ),
    FlywheelRecordType.AUDIT_REPORT: (
        "v_knowledge_rules",
        # F4：值守报告行（category=audit），同 telemetry 用常量引用
        f"category = '{AUDIT_CATEGORY}'",
        "updated_at",
    ),
    FlywheelRecordType.EXPERIMENT: (
        "v_experiments",
        "",
        "created_at",
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
    "v_experiments": (
        "SELECT experiment_id, cycle_id, params_json, score_before, "
        "score_after, verdict, reason, created_at, decided_at "
        "FROM experiments.experiments"
    ),
    # v_flywheel_corrections 在 _connection 里动态建——列集随存量库
    # 是否已做 F2 迁移（experiment_id 列）而不同，见 _create_correction_view。
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
        # F2 (2026-09-22): experiments.db —— daemon 实验台账（见 experiments.py）
        self._experiments_path = root / ".lingclaude" / "experiments.db"
        self._conn: sqlite3.Connection | None = None
        self._flywheel_attached = False
        self._experiments_attached = False

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
            if self._flywheel_attached:
                # F2: corrections 列集随存量库迁移状态而不同 → 动态建视图
                self._create_correction_view(conn)
            if self._experiments_path.exists():
                conn.execute(
                    "ATTACH DATABASE ? AS experiments",
                    (str(self._experiments_path),),
                )
                self._experiments_attached = True
            else:
                logger.warning(
                    "experiments.db 不存在（%s），实验视图查询将返回空",
                    self._experiments_path,
                )
            for name, select_sql in _TEMP_VIEWS.items():
                if not self._flywheel_attached and name.startswith("v_flywheel"):
                    continue  # 未挂载时不建 flywheel 视图，避免查询报 no such table
                if not self._experiments_attached and name.startswith("v_experiments"):
                    continue  # 同上（F2 台账库缺失时优雅降级）
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
            self._experiments_attached = False

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
        conn = self._connection()  # 先建连接：attached 标志在懒初始化内置位
        # 降级短路：对应库未挂载时视图未建 → 返回空而非报错
        # （F2 之前 flywheel 视图同样存在此缺口，一并补齐）
        if view.startswith("v_flywheel") and not self._flywheel_attached:
            return []
        if view.startswith("v_experiments") and not self._experiments_attached:
            return []
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
        # F4：值守报告行计数（2026-09-23 起，历史行不存在时自然为 0）
        counts["audit_reports"] = conn.execute(
            "SELECT count(*) FROM v_knowledge_rules WHERE category = ?",
            (AUDIT_CATEGORY,),
        ).fetchone()[0]
        if self._flywheel_attached:
            counts["flywheel_errors"] = conn.execute(
                "SELECT count(*) FROM v_flywheel_errors"
            ).fetchone()[0]
            counts["flywheel_corrections"] = conn.execute(
                "SELECT count(*) FROM v_flywheel_corrections"
            ).fetchone()[0]
        if self._experiments_attached:
            counts["experiments_total"] = conn.execute(
                "SELECT count(*) FROM v_experiments"
            ).fetchone()[0]
            counts["experiments_pending"] = conn.execute(
                "SELECT count(*) FROM v_experiments WHERE verdict = 'pending'"
            ).fetchone()[0]
        return counts

    def optimization_outcomes(self) -> dict[str, float | int]:
        """F0 指标（F2 结算数据源）：参数应用率 / rollback率。

        - applied = verdict='accepted' 的实验数
        - rolled_back = 'rolled_back'（含超时清扫——结果未知的保守归档）
        - rejected = P0 门禁拒绝
        - apply_rate / rollback_rate 以已结算实验为分母
        """
        out: dict[str, float | int] = {
            "experiments_settled": 0,
            "applied": 0,
            "rolled_back": 0,
            "rejected": 0,
            "apply_rate": 0.0,
            "rollback_rate": 0.0,
        }
        self._connection()  # 先建连接：attached 标志在懒初始化内置位
        if not self._experiments_attached:
            return out
        conn = self._connection()
        rows = conn.execute(
            "SELECT verdict, count(*) AS n FROM v_experiments "
            "WHERE verdict != 'pending' GROUP BY verdict"
        ).fetchall()
        # safe_connect 不设 row_factory → 行是 tuple，按下标取
        tally = {r[0]: int(r[1]) for r in rows}
        settled = sum(tally.values())
        out["experiments_settled"] = settled
        out["applied"] = tally.get("accepted", 0)
        out["rolled_back"] = tally.get("rolled_back", 0)
        out["rejected"] = tally.get("rejected", 0)
        if settled:
            out["apply_rate"] = round(out["applied"] / settled, 4)
            out["rollback_rate"] = round(out["rolled_back"] / settled, 4)
        return out

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _create_correction_view(self, conn: sqlite3.Connection) -> None:
        """F2: 按存量库实际列集建 corrections 视图。

        未迁移库（无 experiment_id 列）置 NULL 占位，保持视图列集稳定，
        消费方无需感知迁移进度。
        """
        cols = {
            r[1] for r in conn.execute("PRAGMA flywheel.table_info(corrections)")
        }
        exp_expr = "experiment_id" if "experiment_id" in cols else "NULL"
        conn.execute(
            "CREATE TEMP VIEW v_flywheel_corrections AS "
            "SELECT id, original_error, correction, source, confidence, "
            f"applied_at, {exp_expr} AS experiment_id FROM flywheel.corrections"
        )

    def _view_columns(self, view: str) -> list[str]:
        cursor = self._connection().execute(
            f"SELECT * FROM {view} LIMIT 0"  # noqa: S608 - view 来自常量表
        )
        return [d[0] for d in cursor.description]
