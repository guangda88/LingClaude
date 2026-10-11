#!/usr/bin/env python3
"""LingBus 5min poll daemon for lingclaude. Writes pending snapshot to /var/tmp/lingbus_pending_lingclaude.json (atomic write)."""
import json, os, sqlite3, sys, time

DB = "/home/ai/.lingmessage/lingbus.db"
OUT = "/var/tmp/lingbus_pending_lingclaude.json"
OUT_TMP = OUT + ".tmp"
INTERVAL = 300


def poll():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT rowid, message_id, thread_id, sender, subject, channel, timestamp "
        "FROM messages WHERE (recipient='lingclaude' OR recipient='all') "
        "AND sender != 'lingclaude' ORDER BY rowid DESC LIMIT 50"
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def do_poll():
    data = {"polled_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "count": 0, "messages": poll()}
    data["count"] = len(data["messages"])
    with open(OUT_TMP, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(OUT_TMP, OUT)
    return data


if "--once" in sys.argv:
    d = do_poll()
    print(f"polled_at: {d['polled_at']} count: {d['count']}")
    sys.exit(0)

while True:
    try:
        do_poll()
    except Exception as e:
        sys.stderr.write(f"poll error: {e}\n")
    time.sleep(INTERVAL)
