from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lingclaude.core.types import Result


@dataclass(frozen=True)
class MetricPoint:
    timestamp: str
    category: str
    name: str
    value: float
    tags: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class TrendResult:
    name: str
    points: tuple[float, ...]
    timestamps: tuple[str, ...]
    moving_avg: float
    delta: float
    direction: str  # "up", "down", "flat"


@dataclass(frozen=True)
class QualityScore:
    overall: float
    safety: float
    structure: float
    behavior: float
    knowledge: float
    timestamp: str


class MetricsStore:
    def __init__(self, db_path: str | Path = ".lingclaude/metrics.db") -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                value REAL NOT NULL,
                tags TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_metrics_category ON metrics(category);
            CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(name);
            CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics(timestamp);
            CREATE INDEX IF NOT EXISTS idx_metrics_cat_name ON metrics(category, name);
        """)
        self._conn.commit()

    def record(self, category: str, name: str, value: float, **tags: str) -> Result[MetricPoint]:
        ts = datetime.now(timezone.utc).isoformat()
        tags_str = json.dumps(tags, ensure_ascii=False) if tags else "{}"
        try:
            self._conn.execute(
                "INSERT INTO metrics (timestamp, category, name, value, tags) VALUES (?, ?, ?, ?, ?)",
                (ts, category, name, value, tags_str),
            )
            self._conn.commit()
            return Result.ok(MetricPoint(
                timestamp=ts, category=category, name=name, value=value,
                tags=tuple(tags.items()),
            ))
        except sqlite3.Error as e:
            return Result.fail(f"写入指标失败: {e}")

    def record_batch(self, points: tuple[MetricPoint, ...]) -> Result[int]:
        try:
            self._conn.executemany(
                "INSERT INTO metrics (timestamp, category, name, value, tags) VALUES (?, ?, ?, ?, ?)",
                [
                    (p.timestamp, p.category, p.name, p.value, json.dumps(dict(p.tags)))
                    for p in points
                ],
            )
            self._conn.commit()
            return Result.ok(len(points))
        except sqlite3.Error as e:
            return Result.fail(f"批量写入失败: {e}")

    def query(
        self,
        category: str | None = None,
        name: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
    ) -> Result[tuple[MetricPoint, ...]]:
        clauses: list[str] = []
        params: list[Any] = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if name:
            clauses.append("name = ?")
            params.append(name)
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if until:
            clauses.append("timestamp <= ?")
            params.append(until)
        where = " AND ".join(clauses) if clauses else "1=1"
        sql = f"SELECT * FROM metrics WHERE {where} ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        try:
            rows = self._conn.execute(sql, params).fetchall()
            points = tuple(
                MetricPoint(
                    timestamp=r["timestamp"],
                    category=r["category"],
                    name=r["name"],
                    value=r["value"],
                    tags=tuple(json.loads(r["tags"]).items()),
                )
                for r in rows
            )
            return Result.ok(points)
        except sqlite3.Error as e:
            return Result.fail(f"查询失败: {e}")

    def get_trend(
        self,
        category: str,
        name: str,
        window: int = 10,
        since: str | None = None,
    ) -> Result[TrendResult]:
        q = self.query(category=category, name=name, since=since, limit=window)
        if q.is_error:
            return Result.fail(q.error)  # type: ignore[return-value]
        points = q.data
        if not points:
            return Result.ok(TrendResult(
                name=name, points=(), timestamps=(),
                moving_avg=0.0, delta=0.0, direction="flat",
            ))
        values = tuple(p.value for p in reversed(points))
        timestamps = tuple(p.timestamp for p in reversed(points))
        avg = sum(values) / len(values)
        if len(values) >= 2:
            delta = values[-1] - values[0]
        else:
            delta = 0.0
        if delta > 0.01:
            direction = "up"
        elif delta < -0.01:
            direction = "down"
        else:
            direction = "flat"
        return Result.ok(TrendResult(
            name=name, points=values, timestamps=timestamps,
            moving_avg=avg, delta=delta, direction=direction,
        ))

    def get_categories(self) -> Result[tuple[str, ...]]:
        try:
            rows = self._conn.execute(
                "SELECT DISTINCT category FROM metrics ORDER BY category"
            ).fetchall()
            return Result.ok(tuple(r[0] for r in rows))
        except sqlite3.Error as e:
            return Result.fail(f"查询失败: {e}")

    def get_latest(self, category: str, name: str) -> Result[MetricPoint | None]:
        q = self.query(category=category, name=name, limit=1)
        if q.is_error:
            return Result.fail(q.error)  # type: ignore[return-value]
        return Result.ok(q.data[0] if q.data else None)

    def get_statistics(self) -> Result[dict[str, Any]]:
        try:
            total = self._conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
            cats = self._conn.execute(
                "SELECT category, COUNT(*) as cnt FROM metrics GROUP BY category ORDER BY cnt DESC"
            ).fetchall()
            return Result.ok({
                "total_points": total,
                "categories": {r[0]: r[1] for r in cats},
            })
        except sqlite3.Error as e:
            return Result.fail(f"统计失败: {e}")

    def prune(self, before: str) -> Result[int]:
        try:
            cur = self._conn.execute("DELETE FROM metrics WHERE timestamp < ?", (before,))
            self._conn.commit()
            return Result.ok(cur.rowcount)
        except sqlite3.Error as e:
            return Result.fail(f"清理失败: {e}")

    def close(self) -> None:
        self._conn.close()


class QualityScorer:
    def __init__(self, store: MetricsStore | None = None) -> None:
        self._store = store

    def score_structure(self, metrics: dict[str, Any]) -> float:
        violations = metrics.get("violations", 0)
        avg_complexity = metrics.get("avg_complexity", 5.0)
        large_classes = metrics.get("large_classes", 0)
        complexity_penalty = max(0, (avg_complexity - 10) * 0.05)
        violation_penalty = min(1.0, violations * 0.1)
        size_penalty = min(0.5, large_classes * 0.1)
        return max(0.0, min(1.0, 1.0 - violation_penalty - complexity_penalty - size_penalty))

    def score_behavior(self, metrics: dict[str, Any]) -> float:
        hallucination = metrics.get("hallucination_risk", 0.0)
        frustration = metrics.get("frustration_rate", 0.0)
        tool_errors = metrics.get("tool_error_rate", 0.0)
        penalty = hallucination * 0.4 + frustration * 0.3 + tool_errors * 0.3
        return max(0.0, min(1.0, 1.0 - penalty))

    def score_safety(self, metrics: dict[str, Any]) -> float:
        hard_stops = metrics.get("hard_stops", 0)
        verification_passes = metrics.get("verification_passes", 1)
        verification_total = metrics.get("verification_total", 1)
        pass_rate = verification_passes / max(1, verification_total)
        stop_penalty = min(1.0, hard_stops * 0.2)
        return max(0.0, min(1.0, pass_rate - stop_penalty))

    def score_knowledge(self, stats: dict[str, Any]) -> float:
        total_rules = stats.get("total_rules", 0)
        active_rules = stats.get("by_status", {}).get("active", 0)
        avg_quality = stats.get("average_quality", 0.0)
        depth_score = min(1.0, total_rules / 50.0)
        active_score = active_rules / max(1, total_rules)
        return depth_score * 0.3 + active_score * 0.3 + avg_quality * 0.4

    def compute_overall(
        self,
        structure: dict[str, Any] | None = None,
        behavior: dict[str, Any] | None = None,
        safety: dict[str, Any] | None = None,
        knowledge: dict[str, Any] | None = None,
    ) -> QualityScore:
        s = self.score_structure(structure) if structure else 0.5
        b = self.score_behavior(behavior) if behavior else 0.5
        sf = self.score_safety(safety) if safety else 0.5
        k = self.score_knowledge(knowledge) if knowledge else 0.5
        overall = s * 0.25 + b * 0.25 + sf * 0.30 + k * 0.20
        score = QualityScore(
            overall=overall,
            safety=sf,
            structure=s,
            behavior=b,
            knowledge=k,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        if self._store:
            self._store.record("quality", "overall", overall)
            self._store.record("quality", "safety", sf)
            self._store.record("quality", "structure", s)
            self._store.record("quality", "behavior", b)
            self._store.record("quality", "knowledge", k)
        return score
