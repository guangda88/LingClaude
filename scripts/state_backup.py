#!/usr/bin/env python3
"""状态文件自动备份 — INC-20260905-RMRF 事故善后 P0

背景:
- 2026-09-05 rm 类清理事故: knowledge.db / metrics.db / data_flywheel.db /
  meta_cognition.json 全部丢失 (untracked 无版本 + 零备份 + ai01 断链 63 天)
- 档案: .audit/incident_rm_rf_four_files_20260905.md

设计:
- 每次备份落到时间戳目录 .lingclaude/backups/state/<YYYYmmdd_HHMMSS>/
- .db 用 sqlite3 backup API (在线安全, 不会拷到半写状态)
- .json 直接复制
- 轮转: 默认保留最近 14 份, 超出删除
- manifest.json 记录每份文件大小 + sha256, 便于校验
- fail-soft: 单文件失败不影响其余; 全部失败 rc=2, 部分失败 rc=1

用法:
  python3 scripts/state_backup.py           # 执行备份
  python3 scripts/state_backup.py --keep 7  # 自定义保留份数
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / ".lingclaude"
BACKUP_ROOT = SRC_DIR / "backups" / "state"
LOG_FILE = SRC_DIR / "backups" / "backup.log"

STATE_FILES = [
    "knowledge.db",
    "metrics.db",
    "data_flywheel.db",
    "meta_cognition.json",
]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def backup_sqlite(src: Path, dst: Path) -> None:
    """在线备份 sqlite: 只读连接 -> backup API, 不锁库不拷半写."""
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        dst_conn = sqlite3.connect(dst)
        try:
            with dst_conn:
                src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def rotate(keep: int) -> None:
    dirs = sorted(d for d in BACKUP_ROOT.iterdir() if d.is_dir())
    if len(dirs) > keep:
        for old in dirs[:-keep]:
            shutil.rmtree(old, ignore_errors=True)


def backup_once(keep: int) -> int:
    ts = time.strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_ROOT / ts
    dest.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, dict] = {}
    failures: list[str] = []
    for name in STATE_FILES:
        src = SRC_DIR / name
        if not src.exists():
            failures.append(f"{name}: 源不存在(跳过)")
            continue
        dst = dest / name
        try:
            if name.endswith(".db"):
                backup_sqlite(src, dst)
            else:
                shutil.copy2(src, dst)
            manifest[name] = {"size": dst.stat().st_size, "sha256": sha256(dst)}
        except Exception as exc:  # fail-soft: 单文件失败不影响其余
            failures.append(f"{name}: {exc}")
            dst.unlink(missing_ok=True)

    (dest / "manifest.json").write_text(
        json.dumps({"ts": ts, "files": manifest}, ensure_ascii=False, indent=2)
    )
    rotate(keep)

    ok = len(manifest)
    line = (
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} ts={ts} "
        f"ok={ok}/{len(STATE_FILES)} failures={failures}"
    )
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a") as f:
        f.write(line + "\n")
    print(line)

    if ok == 0:
        return 2
    if failures:
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="lingclaude 状态文件备份")
    ap.add_argument("--keep", type=int, default=14, help="保留份数 (默认 14)")
    args = ap.parse_args()
    try:
        return backup_once(args.keep)
    except Exception as exc:
        print(f"backup fatal: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
