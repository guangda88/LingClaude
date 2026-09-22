"""F2 归因链（2026-09-22）：优化实验台账。

诊断文档 §M 执行序 F2——experiment_id 贯穿 + accept/rollback 结算。
此前 daemon 三条出口（P0 门禁拒绝 / P1 择优回滚 / 正常应用）不留痕，
优化"是否真的被采纳、是否被回滚"在数据上无据可查；corrections 流
（F3-2 接通）也不知道自己发生在哪次参数实验的观察窗内。

本模块是纯加法：
- 独立库 ``.lingclaude/experiments.db``（B 裁定：逻辑合一、物理不动），
  经 state_store_ext 视图对齐 F0 指标（参数应用率 / rollback率）。
- 结算语义幂等（已结算行二次 settle 为 no-op）。
- 所有异常吞掉降级为 warning——台账故障不允许打断优化主循环
  （同 turn_learner 纪律，钱学森增益纪律：观测器故障不得引入新扰动）。

生命周期::

    exp_id = ledger.start(cycle_id, params, score_before)   # verdict=pending
    ... daemon 决策 ...
    ledger.settle(exp_id, "accepted", reason="applied")      # 终态落库
    # pending 超过 PENDING_TTL 由 daemon 周期清扫为 rolled_back
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

#: pending 状态容忍时长——超过即视为「结果未知」，清扫为 rolled_back。
#: 24h ≈ daemon 正常节奏下两轮周期间隔的上界。
PENDING_TTL_HOURS = 24

#: verdict 终态白名单（settle 校验，防拼写漂移进库）
VERDICTS = ("accepted", "rejected", "rolled_back")


@dataclass(frozen=True)
class ExperimentRecord:
    experiment_id: str
    cycle_id: int | None
    params_json: str
    score_before: float | None
    score_after: float | None
    verdict: str  # pending | accepted | rejected | rolled_back
    reason: str
    created_at: str
    decided_at: str | None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ExperimentLedger:
    """实验台账（SQLite，独立库）。全部方法故障安全。"""

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            project_root = Path(__file__).resolve().parent.parent.parent
            db_path = str(project_root / ".lingclaude" / "experiments.db")
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        # 容错初始化：目录/建表失败（只读 FS 等）不抛出——台账是观测件，
        # 其故障不得阻断 daemon 启动（后续方法各自 try/except 降级）。
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_db()
        except Exception:
            logger.warning(
                "[F2] 实验台账初始化失败（%s），降级为无台账运行",
                self.db_path,
                exc_info=True,
            )

    # ------------------------------------------------------------------ #
    def _init_db(self) -> None:
        conn = self._get_connection()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS experiments (
                experiment_id TEXT PRIMARY KEY,
                cycle_id INTEGER,
                params_json TEXT NOT NULL,
                score_before REAL,
                score_after REAL,
                verdict TEXT NOT NULL DEFAULT 'pending',
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                decided_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_exp_verdict ON experiments(verdict)"
        )
        conn.commit()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------ #
    def start(
        self,
        cycle_id: int | None,
        params: dict,
        score_before: float | None,
    ) -> str | None:
        """开一张 pending 实验单，返回 experiment_id；失败返回 None。"""
        try:
            exp_id = f"exp_{uuid.uuid4().hex[:12]}"
            conn = self._get_connection()
            conn.execute(
                """
                INSERT INTO experiments
                    (experiment_id, cycle_id, params_json, score_before,
                     verdict, reason, created_at)
                VALUES (?, ?, ?, ?, 'pending', '', ?)
                """,
                (
                    exp_id,
                    cycle_id,
                    json.dumps(params, ensure_ascii=False, sort_keys=True),
                    score_before,
                    _now(),
                ),
            )
            conn.commit()
            return exp_id
        except Exception:
            logger.warning("[F2] 实验开单失败（降级为无台账）", exc_info=True)
            return None

    def settle(
        self,
        experiment_id: str | None,
        verdict: str,
        score_after: float | None = None,
        reason: str = "",
    ) -> bool:
        """结算。幂等：已终态的行二次 settle 返回 False（不覆盖）。

        缺失 experiment_id（台账开单失败场景）静默返回 False——
        「无台账」本身是合法降级态，不刷警告。
        """
        if not experiment_id:
            return False
        if verdict not in VERDICTS:
            logger.warning("[F2] 非法 verdict=%r，拒结算", verdict)
            return False
        try:
            conn = self._get_connection()
            cur = conn.execute(
                """
                UPDATE experiments
                   SET verdict = ?, score_after = ?, reason = ?, decided_at = ?
                 WHERE experiment_id = ? AND verdict = 'pending'
                """,
                (verdict, score_after, reason, _now(), experiment_id),
            )
            conn.commit()
            return cur.rowcount > 0
        except Exception:
            logger.warning("[F2] 结算失败 exp=%s", experiment_id, exc_info=True)
            return False

    # ------------------------------------------------------------------ #
    def current_pending_id(self) -> str | None:
        """最新一张 pending 单的 id（无则 None）。

        供 turn_learner 在 corrections 落库时携带证据上下文：
        存在 pending 单 ⇒ 正处于某次参数应用的观察窗内。
        排序用隐式 rowid 而非 created_at——后者秒级精度，同秒多单
        顺序不稳定（测试 test_current_pending_returns_latest 抓出）。
        """
        try:
            row = self._get_connection().execute(
                """
                SELECT experiment_id FROM experiments
                 WHERE verdict = 'pending'
                 ORDER BY rowid DESC LIMIT 1
                """
            ).fetchone()
            return row["experiment_id"] if row else None
        except Exception:
            return None

    def expire_stale_pending(self) -> int:
        """把超过 PENDING_TTL 的 pending 清扫为 rolled_back。返回条数。"""
        cutoff = (datetime.now() - timedelta(hours=PENDING_TTL_HOURS)).isoformat(
            timespec="seconds"
        )
        try:
            conn = self._get_connection()
            cur = conn.execute(
                """
                UPDATE experiments
                   SET verdict = 'rolled_back',
                       reason = 'stale_pending_expired',
                       decided_at = ?
                 WHERE verdict = 'pending' AND created_at < ?
                """,
                (_now(), cutoff),
            )
            conn.commit()
            return cur.rowcount
        except Exception:
            logger.warning("[F2] pending 清扫失败", exc_info=True)
            return 0

    # ------------------------------------------------------------------ #
    def counts(self) -> dict[str, int]:
        """按 verdict 计数——F0「参数应用率 / rollback率」的数据源。"""
        try:
            rows = self._get_connection().execute(
                "SELECT verdict, COUNT(*) AS n FROM experiments GROUP BY verdict"
            ).fetchall()
            out = {v: 0 for v in ("pending", *VERDICTS)}
            for r in rows:
                out[r["verdict"]] = int(r["n"])
            return out
        except Exception:
            logger.warning("[F2] 台账计数失败", exc_info=True)
            return {v: 0 for v in ("pending", *VERDICTS)}

    def list_recent(self, limit: int = 20) -> list[dict]:
        try:
            rows = self._get_connection().execute(
                """
                SELECT * FROM experiments
                 ORDER BY created_at DESC LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            logger.warning("[F2] 台账查询失败", exc_info=True)
            return []
