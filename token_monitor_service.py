#!/usr/bin/env python3
"""token_monitor 本地灵元实例 — 全自包含独立服务

启动:
  python3 token_monitor_service.py               # FastAPI :13470
  python3 token_monitor_service.py --cli report   # JSON 日报

端点:
  POST /record  {"model":"...","task_type":"...","total_tokens":N,...}
  GET  /stats?days=7
  GET  /health
"""

import json, sqlite3, hashlib, os, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from dataclasses import dataclass

try:
    from fastapi import FastAPI
    import uvicorn
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

DB_DIR = Path.home() / ".lingclaude"
DB_PATH = DB_DIR / "token_monitor.db"


def _connect():
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS usage_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        model TEXT NOT NULL,
        task_type TEXT NOT NULL,
        total_tokens INTEGER NOT NULL,
        input_tokens INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL,
        prompt_count INTEGER NOT NULL,
        metadata TEXT
    )""")
    return conn


def record_usage(model, task_type, total_tokens, input_tokens, output_tokens, prompt_count=1, metadata=None):
    conn = _connect()
    conn.execute(
        "INSERT INTO usage_records (timestamp,model,task_type,total_tokens,input_tokens,output_tokens,prompt_count,metadata) VALUES (?,?,?,?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(), model, task_type, total_tokens, input_tokens, output_tokens, prompt_count,
         json.dumps(metadata or {})),
    )
    conn.commit()
    conn.close()


def get_recent(days=7):
    conn = _connect()
    cur = conn.execute(
        "SELECT COUNT(*), SUM(total_tokens), SUM(input_tokens), SUM(output_tokens) FROM usage_records WHERE timestamp > datetime('now',?,'utc')",
        (f"-{days} days",),
    )
    row = cur.fetchone()
    conn.close()
    return {"period_days": days, "prompts": row[0] or 0, "total_tokens": row[1] or 0, "input_tokens": row[2] or 0, "output_tokens": row[3] or 0}


def get_daily(days=30):
    conn = _connect()
    cur = conn.execute(
        "SELECT date(timestamp), COUNT(*), SUM(total_tokens) FROM usage_records WHERE timestamp > datetime('now',?,'utc') GROUP BY date(timestamp) ORDER BY date(timestamp) DESC",
        (f"-{days} days",),
    )
    rows = cur.fetchall()
    conn.close()
    return [{"date": r[0], "prompts": r[1], "tokens": r[2] or 0} for r in rows]


# ===== FastAPI 服务 =====
if HAS_FASTAPI:
    app = FastAPI(title="token_monitor 本地灵元", version="0.1.0")

    @app.post("/record")
    async def record(data: dict):
        record_usage(
            model=data.get("model", "unknown"),
            task_type=data.get("task_type", "unknown"),
            total_tokens=data.get("total_tokens", 0),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            prompt_count=data.get("prompt_count", 1),
        )
        return {"status": "recorded"}

    @app.get("/stats")
    async def stats(days: int = 7):
        return get_recent(days)

    @app.get("/stats/daily")
    async def stats_daily(days: int = 30):
        return get_daily(days)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "token_monitor", "version": "0.1.0"}


# ===== CLI 入口 =====
def cli_report():
    stats = get_recent(7)
    daily = get_daily(7)
    print(json.dumps({"stats": stats, "daily": daily}, indent=2, ensure_ascii=False))


def cli_live():
    import time
    while True:
        r = get_recent(1)
        sys.stdout.write(f"[{datetime.now().strftime('%H:%M:%S')}] 1h: {r['prompts']} prompts, {r['total_tokens']} tokens\r")
        sys.stdout.flush()
        time.sleep(60)


if __name__ == "__main__":
    if "--cli" in sys.argv:
        idx = sys.argv.index("--cli")
        mode = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "report"
        if mode == "live":
            cli_live()
        else:
            cli_report()
    elif HAS_FASTAPI:
        port = 13470
        if "--port" in sys.argv:
            idx = sys.argv.index("--port")
            port = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else port
        host = os.environ.get("TOKEN_MONITOR_HOST", "127.0.0.1")
        print(f"token_monitor 本地灵元: http://{host}:{port}")
        uvicorn.run(app, host=host, port=port)
    else:
        print("需 fastapi+uvicorn: pip install fastapi uvicorn")
        print("或使用: python3 token_monitor_service.py --cli report")
