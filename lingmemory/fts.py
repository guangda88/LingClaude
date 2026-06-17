# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

"""
灵忆 V1.0 插片：全文搜索同步

FTS5索引同步是插片，不是主干。
主干3操作(create/transition/query)不关心全文搜索。
"""
import json
import sqlite3


class FTSSync:
    """FTS5 全文搜索索引同步 — 插片"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def index(self, record_id: str, data: dict):
        """create/update时自动同步FTS"""
        content = data.get("content", "") or data.get("goal", "") or data.get("title", "") or ""
        if content:
            self.conn.execute(
                "INSERT OR REPLACE INTO records_fts (record_id, content) VALUES (?, ?)",
                (record_id, str(content)),
            )

    def remove(self, record_id: str):
        """删除记录时清理FTS"""
        self.conn.execute("DELETE FROM records_fts WHERE record_id = ?", (record_id,))

    def search(self, keyword: str, limit: int = 20) -> list[str]:
        """全文搜索，返回匹配的record_id列表"""
        rows = self.conn.execute(
            "SELECT record_id FROM records_fts WHERE records_fts MATCH ? ORDER BY rank LIMIT ?",
            (keyword, limit),
        ).fetchall()
        return [r["record_id"] for r in rows]
