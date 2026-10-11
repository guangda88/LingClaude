#!/usr/bin/env python3
"""crush.db hot backup — every 30min, keep last 24 copies (12h)

2026-08-14 恢复：脚本曾丢失导致 cron 失效。改用 sqlite3 在线备份 API
（原 shutil.copy2 对活跃 WAL 库不安全）。
"""

import glob
import os
import sqlite3
import time
from pathlib import Path

SRC_CANDIDATES = [
    Path.home() / "lingclaude" / ".crush" / "crush.db",
    Path.home() / ".crush" / "crush.db",
    Path.home() / ".local" / "share" / "crush" / "crush.db",
]
DST_DIR = Path.home() / ".crush_backups"
KEEP = 24


def _find_src():
    for p in SRC_CANDIDATES:
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def main():
    src = _find_src()
    if not src:
        print("SKIP: no valid crush.db found in candidates")
        return
    DST_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    dst = DST_DIR / f"crush.db.{ts}"
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    dst_conn = sqlite3.connect(str(dst))
    with dst_conn:
        src_conn.backup(dst_conn)
    src_conn.close()
    dst_conn.close()
    print(f"BACKUP: {dst} ({dst.stat().st_size} bytes) from {src}")
    backups = sorted(glob.glob(str(DST_DIR / "crush.db.*")))
    if len(backups) > KEEP:
        for old in backups[:-KEEP]:
            os.remove(old)
            print(f"PRUNE: {old}")


if __name__ == "__main__":
    main()
