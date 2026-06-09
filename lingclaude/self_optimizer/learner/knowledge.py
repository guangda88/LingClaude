from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from lingclaude.core.safe_db import safe_commit, safe_connect

from lingclaude.core.types import Result
from lingclaude.self_optimizer.learner.models import (
    FeedbackCategory,
    LearnedRule,
    Pattern,
)

logger = logging.getLogger(__name__)


class KnowledgeBase:
    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            project_root = Path(__file__).parent.parent.parent.parent
            db_path = str(project_root / ".lingclaude" / "knowledge.db")

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._initialize_db()

    def _initialize_db(self) -> None:
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rules (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                category TEXT NOT NULL,
                pattern_json TEXT NOT NULL,
                tools_json TEXT NOT NULL,
                frequency INTEGER NOT NULL,
                confidence REAL NOT NULL,
                quality_score REAL NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata_json TEXT
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_rules_category ON rules(category)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_rules_status ON rules(status)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_rules_quality ON rules(quality_score)"
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

    def add_rule(self, rule: LearnedRule) -> Result[bool]:
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            now = datetime.now().isoformat()

            cursor.execute(
                """
                INSERT OR REPLACE INTO rules (
                    id, name, description, category,
                    pattern_json, tools_json, frequency,
                    confidence, quality_score, status,
                    created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    rule.id,
                    rule.name,
                    rule.description,
                    rule.category.value,
                    json.dumps(
                        {
                            "file_patterns": rule.pattern.file_patterns,
                            "code_patterns": rule.pattern.code_patterns,
                            "context_keywords": rule.pattern.context_keywords,
                            "severity_distribution": rule.pattern.severity_distribution,
                            "tool_support": rule.pattern.tool_support,
                        }
                    ),
                    json.dumps(rule.tools),
                    rule.frequency,
                    rule.confidence,
                    rule.quality_score,
                    rule.status,
                    rule.created_at.isoformat() if rule.created_at else now,
                    now,
                    json.dumps({}),
                ),
            )
            safe_commit(conn)
            return Result.ok(True)
        except Exception as e:
            logger.warning("添加规则失败: %s", rule.id, exc_info=True)
            return Result.fail(f"Failed to add rule: {e}", code="DB_ERROR")

    def add_rules_batch(self, rules: list[LearnedRule]) -> Result[int]:
        count = 0
        for rule in rules:
            r = self.add_rule(rule)
            if r.is_ok:
                count += 1
        return Result.ok(count)

    def get_rule(self, rule_id: str) -> Result[LearnedRule]:
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM rules WHERE id = ?", (rule_id,))
            row = cursor.fetchone()
            if row:
                return Result.ok(self._row_to_rule(row))
            return Result.fail(f"Rule not found: {rule_id}", code="NOT_FOUND")
        except Exception as e:
            logger.warning("获取规则失败: %s", rule_id, exc_info=True)
            return Result.fail(f"Failed to get rule: {e}", code="DB_ERROR")

    def get_all_rules(
        self,
        category: FeedbackCategory | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> Result[tuple[LearnedRule, ...]]:
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            query = "SELECT * FROM rules WHERE 1=1"
            params: list[Any] = []

            if category:
                query += " AND category = ?"
                params.append(category.value)
            if status:
                query += " AND status = ?"
                params.append(status)

            query += " ORDER BY quality_score DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            return Result.ok(tuple(self._row_to_rule(row) for row in cursor.fetchall()))
        except Exception as e:
            logger.warning("获取规则列表失败", exc_info=True)
            return Result.fail(f"Failed to get rules: {e}", code="DB_ERROR")

    def search_rules(self, keyword: str, limit: int = 20) -> Result[tuple[LearnedRule, ...]]:
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            pattern = f"%{keyword}%"

            cursor.execute(
                """
                SELECT * FROM rules
                WHERE name LIKE ? OR description LIKE ?
                ORDER BY quality_score DESC LIMIT ?
            """,
                (pattern, pattern, limit),
            )
            return Result.ok(tuple(self._row_to_rule(row) for row in cursor.fetchall()))
        except Exception as e:
            logger.warning("搜索规则失败: %s", keyword, exc_info=True)
            return Result.fail(f"Failed to search rules: {e}", code="DB_ERROR")

    def update_rule_status(self, rule_id: str, status: str) -> Result[bool]:
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE rules SET status = ?, updated_at = ? WHERE id = ?",
                (status, datetime.now().isoformat(), rule_id),
            )
            safe_commit(conn)
            return Result.ok(cursor.rowcount > 0)
        except Exception as e:
            logger.warning("更新规则状态失败: %s", rule_id, exc_info=True)
            return Result.fail(f"Failed to update rule status: {e}", code="DB_ERROR")

    def delete_rule(self, rule_id: str) -> Result[bool]:
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
            safe_commit(conn)
            return Result.ok(cursor.rowcount > 0)
        except Exception as e:
            logger.warning("删除规则失败: %s", rule_id, exc_info=True)
            return Result.fail(f"Failed to delete rule: {e}", code="DB_ERROR")

    def get_statistics(self) -> Result[dict[str, Any]]:
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM rules")
            total = cursor.fetchone()[0]

            cursor.execute(
                "SELECT category, COUNT(*) FROM rules GROUP BY category"
            )
            by_category = {row[0]: row[1] for row in cursor.fetchall()}

            cursor.execute(
                "SELECT status, COUNT(*) FROM rules GROUP BY status"
            )
            by_status = {row[0]: row[1] for row in cursor.fetchall()}

            cursor.execute("SELECT AVG(quality_score) FROM rules")
            avg_quality = cursor.fetchone()[0] or 0.0

            return Result.ok({
                "total_rules": total,
                "by_category": by_category,
                "by_status": by_status,
                "average_quality": round(avg_quality, 2),
            })
        except Exception as e:
            logger.warning("获取统计信息失败", exc_info=True)
            return Result.fail(f"Failed to get statistics: {e}", code="DB_ERROR")

    def _row_to_rule(self, row: sqlite3.Row) -> LearnedRule:
        pattern_data = json.loads(row["pattern_json"])
        pattern = Pattern(
            file_patterns=tuple(pattern_data.get("file_patterns", [])),
            code_patterns=tuple(pattern_data.get("code_patterns", [])),
            context_keywords=tuple(pattern_data.get("context_keywords", [])),
            severity_distribution=pattern_data.get("severity_distribution", {}),
            tool_support=tuple(pattern_data.get("tool_support", [])),
        )

        return LearnedRule(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            category=FeedbackCategory(row["category"]),
            pattern=pattern,
            tools=tuple(json.loads(row["tools_json"])),
            frequency=row["frequency"],
            confidence=row["confidence"],
            quality_score=row["quality_score"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class InMemoryKnowledgeBase(KnowledgeBase):
    def __init__(self) -> None:
        self._rules: dict[str, LearnedRule] = {}

    def _initialize_db(self) -> None:
        pass

    def _get_connection(self) -> sqlite3.Connection:
        raise NotImplementedError("InMemoryKnowledgeBase does not use SQLite")

    def add_rule(self, rule: LearnedRule) -> Result[bool]:
        self._rules[rule.id] = rule
        return Result.ok(True)

    def add_rules_batch(self, rules: list[LearnedRule]) -> Result[int]:
        count = 0
        for rule in rules:
            self._rules[rule.id] = rule
            count += 1
        return Result.ok(count)

    def get_rule(self, rule_id: str) -> Result[LearnedRule]:
        rule = self._rules.get(rule_id)
        if rule is None:
            return Result.fail(f"Rule not found: {rule_id}", code="NOT_FOUND")
        return Result.ok(rule)

    def get_all_rules(
        self,
        category: FeedbackCategory | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> Result[tuple[LearnedRule, ...]]:
        rules = list(self._rules.values())
        if category:
            rules = [r for r in rules if r.category == category]
        if status:
            rules = [r for r in rules if r.status == status]
        rules.sort(key=lambda r: r.quality_score, reverse=True)
        return Result.ok(tuple(rules[:limit]))

    def search_rules(self, keyword: str, limit: int = 20) -> Result[tuple[LearnedRule, ...]]:
        kw = keyword.lower()
        rules = [
            r for r in self._rules.values()
            if kw in r.name.lower() or kw in r.description.lower()
        ]
        rules.sort(key=lambda r: r.quality_score, reverse=True)
        return Result.ok(tuple(rules[:limit]))

    def update_rule_status(self, rule_id: str, status: str) -> Result[bool]:
        if rule_id in self._rules:
            old = self._rules[rule_id]
            self._rules[rule_id] = LearnedRule(
                id=old.id,
                name=old.name,
                description=old.description,
                category=old.category,
                pattern=old.pattern,
                tools=old.tools,
                frequency=old.frequency,
                confidence=old.confidence,
                quality_score=old.quality_score,
                status=status,
                created_at=old.created_at,
            )
            return Result.ok(True)
        return Result.fail(f"Rule not found: {rule_id}", code="NOT_FOUND")

    def delete_rule(self, rule_id: str) -> Result[bool]:
        if rule_id in self._rules:
            del self._rules[rule_id]
            return Result.ok(True)
        return Result.fail(f"Rule not found: {rule_id}", code="NOT_FOUND")

    def get_statistics(self) -> Result[dict[str, Any]]:
        total = len(self._rules)
        by_category: dict[str, int] = {}
        by_status: dict[str, int] = {}
        total_quality = 0.0

        for rule in self._rules.values():
            cat = rule.category.value
            by_category[cat] = by_category.get(cat, 0) + 1
            by_status[rule.status] = by_status.get(rule.status, 0) + 1
            total_quality += rule.quality_score

        return Result.ok({
            "total_rules": total,
            "by_category": by_category,
            "by_status": by_status,
            "average_quality": round(total_quality / total, 2) if total > 0 else 0.0,
        })

    def close(self) -> None:
        pass
