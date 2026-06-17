# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

"""
灵忆维护层 — 信息生命周期自动流转

信息5状态：active → archived → expired → purged
retain=true 的信息跳过自动清理。
is_conclusion=true 的信息跳过自动清理。

不改主干，只调用 transition。
"""

from datetime import datetime, timedelta, timezone

from lingmemory.core import LingMemory


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Maintenance:
    """信息生命周期维护"""

    def __init__(self, lm: LingMemory):
        self.lm = lm

    def archive_stale_infos(
        self, max_age_hours: int = 72, batch_size: int = 100
    ) -> dict:
        """将超龄的 active info 归档

        跳过: retain=true, is_conclusion=true
        返回: {archived: int, skipped: int}
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()

        rows = self.lm.conn.execute(
            """SELECT id, data FROM records
               WHERE type = 'info' AND state = 'active'
               AND updated_at < ?
               AND json_extract(data, '$.retain') IS NOT 1
               AND json_extract(data, '$.is_conclusion') IS NOT 1
               ORDER BY updated_at ASC LIMIT ?""",
            (cutoff, batch_size),
        ).fetchall()

        archived = 0
        skipped = 0
        for row in rows:
            try:
                self.lm.transition(row["id"], "archive", actor="maintenance")
                archived += 1
            except Exception:
                skipped += 1

        return {"archived": archived, "skipped": skipped}

    def expire_archived_infos(
        self, max_age_hours: int = 168, batch_size: int = 100
    ) -> dict:
        """将超龄的 archived info 标记为 expired（默认7天）"""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()

        rows = self.lm.conn.execute(
            """SELECT id FROM records
               WHERE type = 'info' AND state = 'archived'
               AND updated_at < ?
               ORDER BY updated_at ASC LIMIT ?""",
            (cutoff, batch_size),
        ).fetchall()

        expired = 0
        for row in rows:
            try:
                self.lm.transition(row["id"], "expire", actor="maintenance")
                expired += 1
            except Exception:
                pass

        return {"expired": expired}

    def purge_expired_infos(self, batch_size: int = 100) -> dict:
        """清理 expired info（物理删除）"""
        rows = self.lm.conn.execute(
            """SELECT id FROM records
               WHERE type = 'info' AND state = 'purged'
               LIMIT ?""",
            (batch_size,),
        ).fetchall()

        purged = 0
        for row in rows:
            self.lm.conn.execute("DELETE FROM events WHERE record_id = ?", (row["id"],))
            self.lm.conn.execute("DELETE FROM records WHERE id = ?", (row["id"],))
            purged += 1

        if purged:
            self.lm.conn.commit()

        return {"purged": purged}

    def run_full_cycle(self) -> dict:
        """执行完整的清理周期：archive → expire → purge"""
        step1 = self.archive_stale_infos()
        step2 = self.expire_archived_infos()
        step3 = self.purge_expired_infos()

        return {
            "archived": step1["archived"],
            "expired": step2["expired"],
            "purged": step3["purged"],
            "skipped": step1["skipped"],
            "ran_at": _now(),
        }

    def stats(self) -> dict:
        """数据库统计"""
        total = self.lm.conn.execute("SELECT COUNT(*) as c FROM records").fetchone()["c"]
        events = self.lm.conn.execute("SELECT COUNT(*) as c FROM events").fetchone()["c"]

        by_type = {}
        rows = self.lm.conn.execute(
            "SELECT type, COUNT(*) as c FROM records GROUP BY type"
        ).fetchall()
        for r in rows:
            by_type[r["type"]] = r["c"]

        by_state = {}
        rows = self.lm.conn.execute(
            "SELECT state, COUNT(*) as c FROM records GROUP BY state"
        ).fetchall()
        for r in rows:
            by_state[r["state"]] = r["c"]

        return {
            "total_records": total,
            "total_events": events,
            "by_type": by_type,
            "by_state": by_state,
        }
