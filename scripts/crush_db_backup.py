#!/usr/bin/env python3
"""crush.db hot backup — every 30min, keep last 24 copies (12h)"""

import shutil, glob, time, os
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
        print(f"SKIP: no valid crush.db found in candidates")
        return
    DST_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    dst = DST_DIR / f"crush.db.{ts}"
    shutil.copy2(str(src), str(dst))
    print(f"BACKUP: {dst} ({dst.stat().st_size} bytes) from {src}")
    # prune old
    backups = sorted(glob.glob(str(DST_DIR / "crush.db.*")))
    if len(backups) > KEEP:
        for old in backups[:-KEEP]:
            os.remove(old)
            print(f"PRUNE: {old}")

if __name__ == "__main__":
    main()
