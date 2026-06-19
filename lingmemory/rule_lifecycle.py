"""
灵忆V2.0 Phase 2: rule生命周期管理

状态: active(在用) → review(30天没命中) → archived(验证失败)
                                    ↓
                                active(验证通过)

定时运行: 每天检查一次, 把30天没命中的rule标为review
"""

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

RULES_DB = Path(__file__).parent / "lingmemory_rules.db"
STALE_DAYS = 30


def mark_stale_rules():
    """把30天没命中的rule标为review"""
    conn = sqlite3.connect(str(RULES_DB))
    conn.row_factory = sqlite3.Row

    cutoff = (datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)).isoformat()

    # 找30天没命中的active rule
    stale = conn.execute(
        "SELECT id, type, data FROM records WHERE state = 'validated' AND (last_hit IS NULL OR last_hit < ?)",
        (cutoff,)
    ).fetchall()

    if not stale:
        print(f"没有过期的rule")
        conn.close()
        return 0

    # 标为review
    now = datetime.now(timezone.utc).isoformat()
    for row in stale:
        conn.execute("UPDATE records SET state = 'review', updated_at = ? WHERE id = ?", (now, row["id"]))

    conn.commit()
    print(f"标记 {len(stale)} 条rule为review(30天没命中)")
    for row in stale[:5]:
        print(f"  {row['type']}: {row['id'][:8]}...")
    if len(stale) > 5:
        print(f"  ... 还有 {len(stale)-5} 条")
    conn.close()
    return len(stale)


def hit_rule(record_id):
    """rule被query命中时调用, 更新last_hit"""
    conn = sqlite3.connect(str(RULES_DB))
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE records SET last_hit = ? WHERE id = ?", (now, record_id))
    # 如果是review状态, 自动回到validated
    conn.execute("UPDATE records SET state = 'validated' WHERE id = ? AND state = 'review'", (record_id,))
    conn.commit()
    conn.close()


def archive_rule(record_id, reason=""):
    """验证失败的rule归档"""
    conn = sqlite3.connect(str(RULES_DB))
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE records SET state = 'deprecated', updated_at = ? WHERE id = ?", (now, record_id))
    conn.commit()
    conn.close()
    print(f"archived {record_id[:8]}: {reason}")


def rule_stats():
    """rule库统计"""
    conn = sqlite3.connect(str(RULES_DB))
    conn.row_factory = sqlite3.Row
    total = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    by_state = {}
    for row in conn.execute("SELECT state, COUNT(*) as c FROM records GROUP BY state ORDER BY c DESC"):
        by_state[row["state"]] = row["c"]
    by_type = {}
    for row in conn.execute("SELECT type, COUNT(*) as c FROM records GROUP BY type ORDER BY c DESC LIMIT 10"):
        by_type[row["type"]] = row["c"]
    conn.close()
    return {"total": total, "by_state": by_state, "by_type": by_type}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        s = rule_stats()
        print(f"总rule: {s['total']}")
        print("按状态:")
        for k, v in s["by_state"].items():
            print(f"  {k}: {v}")
        print("按类型TOP10:")
        for k, v in s["by_type"].items():
            print(f"  {k}: {v}")
    else:
        mark_stale_rules()
