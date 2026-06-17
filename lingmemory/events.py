# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

"""
灵忆 V1.0 插片：审计日志

每次create/transition的事件记录是插片，不是主干。
主干只需要写records和改state，events是审计需求。
"""
import json
import sqlite3
from typing import Any


class EventLog:
    """事件审计日志 — 插片"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def record(
        self,
        record_id: str,
        event_type: str,
        from_state: str | None,
        to_state: str,
        actor: str,
        timestamp: str,
        data: dict[str, Any] | None = None,
    ):
        self.conn.execute(
            """INSERT INTO events (record_id, event_type, from_state, to_state, actor, data, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (record_id, event_type, from_state, to_state, actor,
             json.dumps(data or {}, ensure_ascii=False), timestamp),
        )

    def get_history(self, record_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE record_id=? ORDER BY timestamp", (record_id,)
        ).fetchall()
        result = []
        for r in rows:
            item = dict(r)
            if item.get("data"):
                item["data"] = json.loads(item["data"])
            result.append(item)
        return result
