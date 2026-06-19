"""
灵忆V2.0 Phase 1: 双DB分离

rule库: lingmemory_rules.db (高频读/低频写/每日备份)
event库: lingmemory_events.db (高频写/定期清理)

分离原因: rule和event生命周期完全不同, 混在一个DB里:
- event增长拖慢rule查询
- 备份恢复只能一起做
- 清理event可能误删rule
"""

import sqlite3
import json
import shutil
from pathlib import Path
from datetime import datetime, timezone

DB_DIR = Path(__file__).parent
MAIN_DB = DB_DIR / "lingmemory.db"
RULES_DB = DB_DIR / "lingmemory_rules.db"
EVENTS_DB = DB_DIR / "lingmemory_events.db"

# rule类type(长期保留)
RULE_TYPES = {
    "coding_rule", "ops_rule", "arch_rule", "security_rule",
    "collab_rule", "domain_rule", "meta_rule", "security_gate",
    "intent_gate", "code_trace",
}

# event类type(可清理)
EVENT_TYPES = {
    "audit_finding", "audit_check", "code_trace",
}


def split_db():
    """把主DB按type拆分到rules和events两个DB"""
    if not MAIN_DB.exists():
        print("主DB不存在")
        return

    # 备份
    backup = MAIN_DB.with_suffix(".db.bak_v2")
    shutil.copy2(MAIN_DB, backup)
    print(f"备份: {backup}")

    # 连接
    main = sqlite3.connect(str(MAIN_DB))
    main.row_factory = sqlite3.Row

    rules = sqlite3.connect(str(RULES_DB))
    rules.row_factory = sqlite3.Row
    rules.execute("PRAGMA journal_mode=WAL")

    events = sqlite3.connect(str(EVENTS_DB))
    events.row_factory = sqlite3.Row
    events.execute("PRAGMA journal_mode=WAL")

    # 在两个DB里建同样的表结构
    for db in [rules, events]:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS records (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'created',
                data TEXT,
                parent_id TEXT,
                created_by TEXT DEFAULT 'system',
                created_at TEXT,
                updated_at TEXT,
                last_hit TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                record_id TEXT,
                event_type TEXT,
                from_state TEXT,
                to_state TEXT,
                actor TEXT,
                ts TEXT,
                data TEXT
            );
        """)

    # 分发records — 流式游标，不全表加载到内存
    rule_count = 0
    event_count = 0
    main_row_factory = main.row_factory
    for row in main.execute("SELECT id,type,state,data,parent_id,created_by,created_at,updated_at FROM records"):
        rtype = row["type"]
        if rtype in RULE_TYPES:
            rules.execute(
                "INSERT OR REPLACE INTO records (id,type,state,data,parent_id,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (row["id"], row["type"], row["state"], row["data"],
                 row["parent_id"], row["created_by"], row["created_at"], row["updated_at"]))
            rule_count += 1
        else:
            events.execute(
                "INSERT OR REPLACE INTO records (id,type,state,data,parent_id,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (row["id"], row["type"], row["state"], row["data"],
                 row["parent_id"], row["created_by"], row["created_at"], row["updated_at"]))
            event_count += 1

    # 预加载rule库的record_id集合，避免N+1查询
    rule_ids = {r[0] for r in rules.execute("SELECT id FROM records")}

    # 分发events — 流式游标
    ev_count = 0
    for row in main.execute("SELECT id,record_id,event_type,from_state,to_state,actor,timestamp,data FROM events"):
        rid = row["record_id"]
        target = rules if rid in rule_ids else events
        target.execute(
            "INSERT OR REPLACE INTO events (id,record_id,event_type,from_state,to_state,actor,ts,data) VALUES (?,?,?,?,?,?,?,?)",
            (str(row["id"]), row["record_id"], row["event_type"], row["from_state"],
             row["to_state"], row["actor"], row["timestamp"], row["data"]))
        ev_count += 1

    rules.commit()
    events.commit()

    print(f"分离完成: {rule_count} rules + {event_count} other records + {ev_count} events")
    print(f"rule库: {RULES_DB} ({RULES_DB.stat().st_size // 1024}KB)")
    print(f"event库: {EVENTS_DB} ({EVENTS_DB.stat().st_size // 1024}KB)")
    print(f"主库保留: {MAIN_DB} ({MAIN_DB.stat().st_size // 1024}KB)")

    main.close()
    rules.close()
    events.close()


def stats():
    """查看两个DB的统计"""
    for name, path in [("rules", RULES_DB), ("events", EVENTS_DB), ("main", MAIN_DB)]:
        if not path.exists():
            print(f"{name}: 不存在")
            continue
        conn = sqlite3.connect(str(path))
        records = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'").fetchone() else 0
        size = path.stat().st_size // 1024
        types = conn.execute("SELECT type, COUNT(*) as c FROM records GROUP BY type ORDER BY c DESC LIMIT 5").fetchall()
        print(f"\n{name} ({path.name}, {size}KB):")
        print(f"  records: {records}, events: {events}")
        for t, c in types:
            print(f"    {t}: {c}")
        conn.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        stats()
    else:
        split_db()
