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
#: F7 (2026-09-25): frozen zone（死循环熔断闸）——同参数组合连续 rolled_back
#: 达到阈值即冻结优化（跳轮+指数退避），防止优化器在永远赢不了历史最优
#: 的参数上空转烧预算。语义对齐「插片风暴三层熔断闸」。
FROZEN_ROLLBACK_STREAK_THRESHOLD = 5  #: 同参数+同因连续回滚达此数即冻结
FROZEN_MAX_BACKOFF_CYCLES = 3         #: 指数退避上限（2^n 轮跳一轮）

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
        # S0-3 (2026-10-17): 评估器冻结+指纹化——每条实验单记录开单时的
        # 评估器版本与源码指纹，跨轮比较仅同指纹有效（指纹变更 →
        # incomparable，防「换了尺子还当同一把尺子读数」）。
        conn.execute(
            """
            ALTER TABLE experiments ADD COLUMN evaluator_version TEXT NOT NULL DEFAULT ''
            """
        ) if not self._has_column("experiments", "evaluator_version") else None
        conn.execute(
            """
            ALTER TABLE experiments ADD COLUMN evaluator_fingerprint TEXT NOT NULL DEFAULT ''
            """
        ) if not self._has_column("experiments", "evaluator_fingerprint") else None
        # P1-7 (2026-10-17): artifact_origin 归因列（ac-P2-1 提前合并）——
        # 每条变更记录「谁改的」：self=自优化回路 / human=人工 / external=
        # 外部工具。当前回路写入恒为 self；人工改 behavior_policy.yaml
        # 不经本台账（未来可由 startup_watch 指纹漂移检测补录 external）。
        conn.execute(
            """
            ALTER TABLE experiments ADD COLUMN origin TEXT NOT NULL DEFAULT ''
            """
        ) if not self._has_column("experiments", "origin") else None
        conn.commit()

    def _has_column(self, table: str, column: str) -> bool:
        try:
            rows = self._get_connection().execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
            return any(r["name"] == column for r in rows)
        except Exception:
            return True  # 判定失败按「已有」处理，跳过 ALTER（安全侧）

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
        """开一张 pending 实验单，返回 experiment_id；失败返回 None。

        S0-3: 开单即盖评估器指纹（记录「用哪把尺子量的」）。
        """
        try:
            from lingclaude.self_optimizer.evaluator_meta import evaluator_meta

            meta = evaluator_meta()
            exp_id = f"exp_{uuid.uuid4().hex[:12]}"
            conn = self._get_connection()
            conn.execute(
                """
                INSERT INTO experiments
                    (experiment_id, cycle_id, params_json, score_before,
                     verdict, reason, created_at,
                     evaluator_version, evaluator_fingerprint, origin)
                VALUES (?, ?, ?, ?, 'pending', '', ?, ?, ?, 'self')
                """,
                (
                    exp_id,
                    cycle_id,
                    json.dumps(params, ensure_ascii=False, sort_keys=True),
                    score_before,
                    _now(),
                    meta["evaluator_version"],
                    meta["evaluator_fingerprint"],
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

    # ------------------------------------------------------------------ #
    # S0-2 (2026-10-17): 变更速率总闸——24h 窗口 accepted 计数。
    # 数据源即本表（verdict='accepted' 的实验单 = 一次实际生效的参数变更），
    # 阈值执行在 daemon.run_cycle 入口；本方法只负责诚实计数（失败降级 0，
    # 注意降级语义 = 放行：台账不可用时不阻塞优化循环，与 F2 纪律一致）。
    # ------------------------------------------------------------------ #
    def rolled_back_rate(self, days: float = 30.0) -> float | None:
        """近 N 天已结算单中 rolled_back 占比（P1-6 复发分量数据源）。

        无已结算单 → None（分量弃权，不造假数据）。
        """
        try:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat(
                timespec="seconds"
            )
            conn = self._get_connection()
            row = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN verdict = 'rolled_back' THEN 1 ELSE 0 END) AS rb
                FROM experiments
                WHERE decided_at IS NOT NULL AND decided_at >= ?
                """,
                (cutoff,),
            ).fetchone()
            total = int(row["total"] or 0)
            if total == 0:
                return None
            return float(row["rb"] or 0) / total
        except Exception:
            return None

    def accepted_in_window(self, hours: float = 24.0) -> int:
        try:
            # 边界必须与 _now()/decided_at 同基准（本地无时区，timespec=秒）——
            # 混用 utc ISO 串会因字典序错位偏差整个时区。
            since = (
                datetime.now() - timedelta(hours=hours)
            ).isoformat(timespec="seconds")
            row = self._get_connection().execute(
                """
                SELECT COUNT(*) AS n FROM experiments
                 WHERE verdict = 'accepted' AND decided_at >= ?
                """,
                (since,),
            ).fetchone()
            return int(row["n"])
        except Exception:
            logger.warning("[S0-2] 速率窗口查询失败，按 0 计（放行）", exc_info=True)
            return 0

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

    # ------------------------------------------------------------------ #
    # F7 (2026-09-25): frozen zone（死循环熔断闸）查询 API
    # ------------------------------------------------------------------ #
    def recent_rollbacks(self, limit: int = 50) -> list[dict]:
        """最近 settled 的 rolled_back 单（新→旧）。熔断判据数据源。"""
        try:
            rows = self._get_connection().execute(
                """
                SELECT * FROM experiments
                 WHERE verdict = 'rolled_back'
                 ORDER BY created_at DESC, rowid DESC LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            logger.warning("[F7] 回滚查询失败", exc_info=True)
            return []

    def get(self, experiment_id: str) -> dict | None:
        """按 id 取单（P1-7 断链检测用）。无/故障 → None。"""
        try:
            conn = self._get_connection()
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM experiments WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
            return dict(row) if row else None
        except Exception:
            return None

    def rollback_streak(self, params: dict, reason: str) -> int:
        """同参数组合+同因 rolled_back 的**连续**条数（新→旧遇到第一条
        不匹配即停）。staleness 语义：任何 accepted/rejected 单都会打断
        streak——优化已转向，旧回滚不再算「重复撞墙」。"""
        try:
            canonical = json.dumps(params, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            return 0
        streak = 0
        for rec in self.recent_rollbacks(limit=100):
            if rec.get("reason") != reason:
                continue
            try:
                if json.loads(rec.get("params_json") or "{}") != json.loads(canonical):
                    continue
            except (TypeError, ValueError):
                continue
            streak += 1
        return streak

    def latest_verdict_cycle(self, verdict: str) -> int:
        """指定 verdict 的最新 cycle_id（无则 0）。用于冻结期间的
        新进展探测——若该 verdict 有新单落库，说明状态有变，应重新评估。"""
        try:
            row = self._get_connection().execute(
                """
                SELECT cycle_id FROM experiments
                 WHERE verdict = ? AND cycle_id IS NOT NULL
                 ORDER BY rowid DESC LIMIT 1
                """,
                (verdict,),
            ).fetchone()
            return int(row["cycle_id"]) if row and row["cycle_id"] is not None else 0
        except Exception:
            logger.warning("[F7] 最新结算查询失败", exc_info=True)
            return 0
