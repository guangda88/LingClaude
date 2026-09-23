from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from lingclaude.core.safe_db import safe_commit, safe_connect

from lingclaude.core.types import Result

logger = logging.getLogger(__name__)

FLYWHEEL_DB_NAME = "data_flywheel.db"


@dataclass(frozen=True)
class ErrorPattern:
    pattern_type: str
    file_path: str
    error_message: str
    tool_name: str
    context: str
    occurred_at: str
    session_id: str = ""


@dataclass(frozen=True)
class ToolEvent:
    """F6 (2026-09-23): 双边工具事件——success 位区分成功/失败侧。"""

    session_id: str
    tool_name: str
    success: bool
    occurred_at: str


@dataclass(frozen=True)
class CorrectionEntry:
    original_error: str
    correction: str
    source: str
    confidence: float
    applied_at: str
    # F2 (2026-09-22): 证据上下文——correction 发生时的优化实验单 id。
    # 非空 ⇒ 该纠正发生在某次参数应用的观察窗内，是 F5「证据挂钩」
    # 的挂钩点（conf 上限 0.9/0.6 分级）。None = 无实验上下文。
    experiment_id: str | None = None


@dataclass
class FlywheelStats:
    total_errors: int = 0
    total_corrections: int = 0
    error_categories: dict[str, int] = field(default_factory=dict)
    top_error_files: dict[str, int] = field(default_factory=dict)
    recurrence_rate: float = 0.0
    correction_rate: float = 0.0


class DataFlywheel:
    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            project_root = Path(__file__).parent.parent.parent
            db_path = str(project_root / ".lingclaude" / FLYWHEEL_DB_NAME)

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._initialize_db()

    def _initialize_db(self) -> None:
        conn = self._get_connection()
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS error_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_type TEXT NOT NULL,
                file_path TEXT NOT NULL,
                error_message TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                context TEXT NOT NULL,
                session_id TEXT NOT NULL DEFAULT '',
                occurred_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_error TEXT NOT NULL,
                correction TEXT NOT NULL,
                source TEXT NOT NULL,
                confidence REAL NOT NULL,
                applied_at TEXT NOT NULL,
                experiment_id TEXT
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_errors_type ON error_log(pattern_type)"
        )
        # F2 (2026-09-22): 存量库补 experiment_id 证据列（幂等迁移）。
        cols = {r[1] for r in c.execute("PRAGMA table_info(corrections)").fetchall()}
        if "experiment_id" not in cols:
            c.execute("ALTER TABLE corrections ADD COLUMN experiment_id TEXT")
        # R10-1 (2026-09-23): 复发率埋点——被注入后同会话 30min 窗口内守卫
        # 再触发 ⇒ 该纠正标记复发。无跨轮状态传递的诚实近似（详见
        # record_recurrence docstring 的三重局限说明）。
        if "recurrence_count" not in cols:
            c.execute("ALTER TABLE corrections ADD COLUMN recurrence_count INTEGER NOT NULL DEFAULT 0")
        if "last_recurred_at" not in cols:
            c.execute("ALTER TABLE corrections ADD COLUMN last_recurred_at TEXT")
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_errors_file ON error_log(file_path)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_errors_tool ON error_log(tool_name)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_corrections_source ON corrections(source)"
        )
        # F6 (2026-09-23): tool_events 双边事件表——tool.call 成功+失败双边
        # 语料，供 replay_objective churn 反力项消费（Phase 2 接线）。
        # 与 error_log 差异：只记最小面（success 位 + session + tool + 时序），
        # 失败侧 error_log 已有详情，此处不冗余存错误文本。
        c.execute("""
            CREATE TABLE IF NOT EXISTS tool_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL DEFAULT '',
                tool_name TEXT NOT NULL,
                success INTEGER NOT NULL DEFAULT 1,
                occurred_at TEXT NOT NULL
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_tool_events_session "
            "ON tool_events(session_id, occurred_at)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_tool_events_tool "
            "ON tool_events(tool_name)"
        )
        conn.commit()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = safe_connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def log_error(self, error: ErrorPattern) -> Result[int]:
        try:
            conn = self._get_connection()
            c = conn.cursor()
            c.execute(
                """INSERT INTO error_log
                   (pattern_type, file_path, error_message, tool_name, context, session_id, occurred_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    error.pattern_type,
                    error.file_path,
                    error.error_message,
                    error.tool_name,
                    error.context,
                    error.session_id,
                    error.occurred_at,
                ),
            )
            safe_commit(conn)
            return Result.ok(c.lastrowid)
        except Exception as e:
            logger.warning("飞轮记录错误失败: %s", e)
            return Result.fail(f"Flywheel log failed: {e}", code="DB_ERROR")

    def log_tool_event(self, event: ToolEvent) -> Result[int]:
        """F6 (2026-09-23): 双边事件写入。失败侧仍照常写 error_log（详情），
        此处补成功位快照（双边流），供 churn 反力项回放消费。"""
        try:
            conn = self._get_connection()
            c = conn.cursor()
            c.execute(
                """INSERT INTO tool_events
                   (session_id, tool_name, success, occurred_at)
                   VALUES (?, ?, ?, ?)""",
                (
                    event.session_id,
                    event.tool_name,
                    1 if event.success else 0,
                    event.occurred_at,
                ),
            )
            safe_commit(conn)
            return Result.ok(c.lastrowid)
        except Exception as e:
            logger.warning("飞轮记录工具事件失败: %s", e)
            return Result.fail(f"Flywheel tool_event failed: {e}", code="DB_ERROR")

    def log_correction(self, correction: CorrectionEntry) -> Result[int]:
        try:
            conn = self._get_connection()
            c = conn.cursor()
            c.execute(
                """INSERT INTO corrections
                   (original_error, correction, source, confidence, applied_at,
                    experiment_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    correction.original_error,
                    correction.correction,
                    correction.source,
                    correction.confidence,
                    correction.applied_at,
                    correction.experiment_id,
                ),
            )
            safe_commit(conn)
            return Result.ok(c.lastrowid)
        except Exception as e:
            logger.warning("飞轮记录纠正失败: %s", e)
            return Result.fail(f"Flywheel correction failed: {e}", code="DB_ERROR")

    def get_recent_corrections(self, limit: int = 3) -> Result[list[dict[str, Any]]]:
        """R9 (2026-09-23): corrections 消费面——为 prompt 注入提供读取。

        两路取样：24h 内最近 top-N（热记忆）+ 全库随机 1 条（冷唤醒，
        防止「只记得最近」导致旧错误无限复发）。内容存的是用户原话
        （b7c7283 新格式），注入即真实教训，非计数壳。
        """
        try:
            conn = self._get_connection()
            c = conn.cursor()
            c.execute(
                """SELECT original_error, correction, confidence, applied_at
                   FROM corrections
                   WHERE datetime(applied_at) >= datetime('now', '-1 day')
                   ORDER BY id DESC LIMIT ?""",
                (limit,),
            )
            recent = [
                {
                    "original_error": r[0],
                    "correction": r[1],
                    "confidence": r[2],
                    "applied_at": r[3],
                }
                for r in c.fetchall()
            ]
            sampled: list[dict[str, Any]] = []
            c.execute("SELECT COUNT(*) FROM corrections")
            total = c.fetchone()[0]
            if total > 0:
                c.execute(
                    """SELECT original_error, correction, confidence, applied_at
                       FROM corrections ORDER BY RANDOM() LIMIT 1"""
                )
                r = c.fetchone()
                if r:
                    sampled.append(
                        {
                            "original_error": r[0],
                            "correction": r[1],
                            "confidence": r[2],
                            "applied_at": r[3],
                        }
                    )
            return Result.ok(recent + sampled)
        except Exception as e:
            logger.warning("飞轮读取近期纠正失败: %s", e)
            return Result.fail(f"Flywheel recent_corrections failed: {e}", code="DB_ERROR")

    def record_recurrence(
        self,
        session_id: str,
        fact_types: list[str],
        window_minutes: int = 30,
        occurred_at: str | None = None,
    ) -> Result[int]:
        """R10-1 (2026-09-23): 幻觉复发埋点——写入 error_log + 命中计数。

        时序关联近似（无跨轮注入清单传递的诚实方案）：
        - 「复发」判据 = 同 session_id 在 window_minutes 内有注入记录
          （meta.feedback），此时守卫再触发 ⇒ 近似视为「注入未抑制住」。
        - 命中的注入记录 recurrence_count +1。

        三重已知局限（都是低召回方向，不产生误报性乐观）：
        1. 窗口内注入未被 LLM 实际读到（prompt 构建时机差异）⇒ 误标
        2. 注入有效但错误类型不同 ⇒ 只体现在 fact_type 聚类里
        3. 非 strict 模式下守卫只对部分回合生效 ⇒ 漏计
        """
        occurred_at = occurred_at or datetime.now().isoformat(timespec="seconds")
        try:
            conn = self._get_connection()
            c = conn.cursor()
            since = (
                datetime.fromisoformat(occurred_at)
                - timedelta(minutes=window_minutes)
            ).isoformat(timespec="seconds")
            # R10-3: 细粒度 pattern_type——hallucination:hard_fact 等。
            # 此前 error_log 表 0 生产写入方，state_store.top_error_patterns
            # 的 pattern_type+tool_name 聚合一直在聚空表。
            inserted = 0
            for ft in fact_types or ["unknown"]:
                c.execute(
                    """INSERT INTO error_log
                       (pattern_type, file_path, error_message, tool_name,
                        context, session_id, occurred_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        f"hallucination:{ft}",
                        "response_text",
                        "幻觉守卫触发（R10 埋点）",
                        "hallucination_guard",
                        "",
                        session_id,
                        occurred_at,
                    ),
                )
                inserted += 1
            # 时序窗口内的注入记录 → 复发计数 +1
            # 注意：SQLite 的 datetime(a)-datetime(b) 是字符串前缀转数字相减
            # （同年前缀抵消恒 0），必须用 julianday 天差换算秒。
            c.execute(
                """UPDATE corrections
                   SET recurrence_count = recurrence_count + 1,
                       last_recurred_at = ?
                   WHERE id IN (
                       SELECT id FROM corrections
                       WHERE source = 'meta.feedback'
                         AND (julianday(?) - julianday(applied_at)) * 86400.0 >= 0
                         AND (julianday(?) - julianday(applied_at)) * 86400.0 <= ?
                       ORDER BY id DESC LIMIT 5
                   )""",
                (occurred_at, occurred_at, occurred_at, window_minutes * 60),
            )
            recurrent = c.rowcount
            safe_commit(conn)
            logger.info(
                "R10 复发埋点: session=%s fact_types=%s 新增=%d 命中注入=%d",
                session_id, fact_types, inserted, recurrent,
            )
            return Result.ok(recurrent)
        except Exception as e:
            logger.warning("R10 复发埋点失败（fail-soft）: %s", e)
            return Result.fail(f"Flywheel recurrence failed: {e}", code="DB_ERROR")

    def get_recent_corrections_with_recurrence(
        self, limit: int = 3
    ) -> Result[list[dict[str, Any]]]:
        """R10-1: get_recent_corrections 的复发感知版——每条附 recurrence_count，
        供 prompt_builder 对「注入过仍复发」的纠正加重点标记。"""
        base = self.get_recent_corrections(limit=limit)
        if base.is_error:
            return base
        try:
            conn = self._get_connection()
            c = conn.cursor()
            c.execute(
                """SELECT correction, recurrence_count FROM corrections
                   WHERE recurrence_count > 0 ORDER BY last_recurred_at DESC LIMIT 50"""
            )
            hot = {r[0]: r[1] for r in c.fetchall()}
            enriched = []
            for item in base.data or []:
                count = hot.get(item.get("correction", ""), 0)
                enriched.append({**item, "recurrence_count": count})
            return Result.ok(enriched)
        except Exception as e:
            # fail-soft： enrichment 失败退回无计数版本
            logger.warning("复发计数 enrich 失败（降级）: %s", e)
            return Result.ok(
                [{**item, "recurrence_count": 0} for item in (base.data or [])]
            )

    def get_recurring_errors(self, min_count: int = 2, limit: int = 20) -> Result[list[dict[str, Any]]]:
        try:
            conn = self._get_connection()
            c = conn.cursor()
            c.execute(
                """SELECT pattern_type, file_path, error_message, COUNT(*) as count,
                          MAX(occurred_at) as last_seen
                   FROM error_log
                   GROUP BY pattern_type, file_path, error_message
                   HAVING count >= ?
                   ORDER BY count DESC
                   LIMIT ?""",
                (min_count, limit),
            )
            rows = c.fetchall()
            return Result.ok([dict(r) for r in rows])
        except Exception as e:
            return Result.fail(f"Query failed: {e}", code="DB_ERROR")

    def get_stats(self) -> FlywheelStats:
        try:
            conn = self._get_connection()
            c = conn.cursor()

            c.execute("SELECT COUNT(*) FROM error_log")
            total_errors = c.fetchone()[0]

            c.execute("SELECT COUNT(*) FROM corrections")
            total_corrections = c.fetchone()[0]

            c.execute("SELECT pattern_type, COUNT(*) FROM error_log GROUP BY pattern_type")
            error_categories = {r[0]: r[1] for r in c.fetchall()}

            c.execute("SELECT file_path, COUNT(*) FROM error_log GROUP BY file_path ORDER BY COUNT(*) DESC LIMIT 10")
            top_error_files = {r[0]: r[1] for r in c.fetchall()}

            c.execute("SELECT COUNT(DISTINCT error_message) FROM error_log")
            unique_errors = c.fetchone()[0]

            correction_rate = (total_corrections / total_errors) if total_errors > 0 else 0.0
            recurrence_rate = ((total_errors - unique_errors) / total_errors) if total_errors > 0 else 0.0

            return FlywheelStats(
                total_errors=total_errors,
                total_corrections=total_corrections,
                error_categories=error_categories,
                top_error_files=top_error_files,
                recurrence_rate=round(recurrence_rate, 3),
                correction_rate=round(correction_rate, 3),
            )
        except Exception as e:
            logger.warning("飞轮统计失败: %s", e)
            return FlywheelStats()

    def should_alert(self, threshold: float = 0.5) -> bool:
        stats = self.get_stats()
        return stats.recurrence_rate > threshold

    def get_recent_errors(self, limit: int = 10) -> Result[list[dict[str, Any]]]:
        try:
            conn = self._get_connection()
            c = conn.cursor()
            c.execute(
                "SELECT * FROM error_log ORDER BY occurred_at DESC LIMIT ?",
                (limit,),
            )
            return Result.ok([dict(r) for r in c.fetchall()])
        except Exception as e:
            return Result.fail(f"Query failed: {e}", code="DB_ERROR")
