#!/usr/bin/env python3
"""
disk_cleanup.py — Disk 92% 紧急清理 (灵克 owner 范围)

设计原则 (灵元 1.0):
  - 只清 owner 范围 (/home/ai/lingclaude/)
  - 跨灵备份 (.lingclaude/backups/) 不删 (用户拍板)
  - 临时文件 / 旧备份 / 缓存 安全删
  - 大文件 dry-run + 列出, 不直接删

清理目标 (按风险从低到高):
  1. data/tmp*.tmp        (~25 MB, 临时)
  2. lingmemory.db.bak_v2 (~5 MB, 旧版)
  3. crush.db.prespawn.bak (~368 MB, 7/2 06:08 残留)

预计释放: ~400 MB

用法:
  python3 scripts/disk_cleanup.py --dry-run    # 列出可清
  python3 scripts/disk_cleanup.py --safe        # 清 1+2+3 (owner 范围安全)
  python3 scripts/disk_cleanup.py --aggressive  # + ~/.cache/whisper 等 (用户拍板)
"""
from __future__ import annotations
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/ai/lingclaude")
SAFE_TARGETS = [
    {
        "name": "data/tmp*.tmp",
        "path": REPO / "data",
        "pattern": "tmp*.tmp",
        "size_mb_estimate": 25,
        "reason": "临时文件, 7/2 期间累积, 安全清",
    },
    {
        "name": "lingmemory.db.bak_v2",
        "path": REPO / "lingmemory",
        "files": ["lingmemory.db.bak_v2"],
        "size_mb_estimate": 5,
        "reason": "v2 升级前备份, 已被 v3 取代",
    },
    {
        "name": "crush.db.prespawn.bak",
        "path": REPO / ".crush",
        "files": ["crush.db.prespawn.bak"],
        "size_mb_estimate": 368,
        "reason": "7/2 06:08 残留 bak, 当前 crush.db 已重新写入",
    },
]

AGGRESSIVE_TARGETS = [
    {
        "name": "~/.cache/whisper",
        "path": Path("/home/ai/.cache/whisper"),
        "size_mb_estimate": 600,
        "reason": "Whisper 模型缓存, 若不使用可清",
        "confirm_required": True,
    },
    {
        "name": "~/.cache/go-build",
        "path": Path("/home/ai/.cache/go-build"),
        "size_mb_estimate": 229,
        "reason": "Go 编译缓存, v2/ 已搁置可清",
        "confirm_required": True,
    },
    {
        "name": "/tmp/OmniRoute",
        "path": Path("/tmp/OmniRoute"),
        "size_mb_estimate": 2400,
        "reason": "临时测试数据, omniroute.log 等",
        "confirm_required": True,
    },
]


def get_size_mb(path: Path) -> float:
    if path.is_file():
        return path.stat().st_size / (1024 * 1024)
    elif path.is_dir():
        total = sum(p.stat().st_size for p in path.rglob('*') if p.is_file())
        return total / (1024 * 1024)
    return 0


def list_targets(targets, label):
    print(f"\n=== {label} ===")
    total = 0
    for t in targets:
        path = t["path"]
        if "files" in t:
            for fname in t["files"]:
                fpath = path / fname
                if fpath.exists():
                    size = get_size_mb(fpath)
                    total += size
                    print(f"  [{size:7.1f} MB] {fpath}")
                    print(f"             reason: {t['reason']}")
        elif "pattern" in t:
            import glob
            for f in glob.glob(str(path / t["pattern"])):
                fpath = Path(f)
                if fpath.is_file():
                    size = get_size_mb(fpath)
                    total += size
                    print(f"  [{size:7.1f} MB] {fpath}")
                    print(f"             reason: {t['reason']}")
        else:
            if path.exists():
                size = get_size_mb(path)
                total += size
                print(f"  [{size:7.1f} MB] {path}")
                print(f"             reason: {t['reason']}")
                if t.get("confirm_required"):
                    print(f"             ⚠️ 需用户确认 (跨 owner 或高风险)")
    print(f"\n  TOTAL: {total:.1f} MB ({total/1024:.2f} GB)")


def do_cleanup(targets, dry_run=True):
    for t in targets:
        path = t["path"]
        if "files" in t:
            for fname in t["files"]:
                fpath = path / fname
                if fpath.exists():
                    if dry_run:
                        print(f"  [DRY] would remove {fpath}")
                    else:
                        fpath.unlink()
                        print(f"  [DONE] removed {fpath}")
        elif "pattern" in t:
            import glob
            for f in glob.glob(str(path / t["pattern"])):
                fpath = Path(f)
                if fpath.is_file():
                    if dry_run:
                        print(f"  [DRY] would remove {fpath}")
                    else:
                        fpath.unlink()
                        print(f"  [DONE] removed {fpath}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只列不删")
    parser.add_argument("--safe", action="store_true", help="清 owner 范围安全目标")
    parser.add_argument("--aggressive", action="store_true", help="+ 用户拍板目标")
    args = parser.parse_args()

    if args.dry_run or (not args.safe and not args.aggressive):
        list_targets(SAFE_TARGETS, "SAFE (owner 范围, 自动安全)")
        if args.aggressive:
            list_targets(AGGRESSIVE_TARGETS, "AGGRESSIVE (跨 owner, 需用户拍板)")
        return

    print(f"=== Disk cleanup at {datetime.now(timezone.utc).isoformat()} ===")
    if args.safe:
        print("[SAFE] cleaning owner 范围 targets...")
        do_cleanup(SAFE_TARGETS, dry_run=False)
    if args.aggressive:
        print("[AGGRESSIVE] cleaning 拍板 targets...")
        for t in AGGRESSIVE_TARGETS:
            print(f"  ⚠️ {t['name']}: {t['reason']}")
            resp = input(f"  确认删除 {t['name']} ({t['size_mb_estimate']}MB)? (yes/no): ")
            if resp.lower() == "yes":
                import shutil
                if t["path"].exists():
                    if t["path"].is_dir():
                        shutil.rmtree(t["path"])
                    else:
                        t["path"].unlink()
                    print(f"  [DONE] removed {t['name']}")
            else:
                print(f"  [SKIP] {t['name']}")


if __name__ == "__main__":
    main()