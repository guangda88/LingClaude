#!/usr/bin/env python3
"""清理 data/spill 历史命令回显日志（含旧 key 形态）。

背景 (2026-09-13 安全审计):
- data/spill/*.txt 是会话工具回显日志，secret 扫描全库命中 242 处 key 形态
  （spill_bash_* / spill_grep_* / spill_web_fetch_* 等）。
- 这些文件未被 git 追踪（data/ 在 .gitignore），但明文 key 形态残留磁盘，
  一旦泄露即暴露历史凭据。

本脚本策略:
- 默认 dry-run：只统计不删除，展示将删除的文件数和释放空间。
- --delete：实际删除（默认保留最近 N 天，见 --keep-days）。
- 删除前写审计清单到 data/spill_cleanup_<ts>.log，记录文件名/大小/删除时间。
- 幂等：可重复执行；无匹配时无副作用。

用法:
  python3 scripts/cleanup_spill_logs.py            # dry-run
  python3 scripts/cleanup_spill_logs.py --delete   # 实际清理（保留最近7天）
  python3 scripts/cleanup_spill_logs.py --delete --keep-days 30
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 密钥形态正则（与 lingclaude/core/redact.py 对齐，用于内容级判定）
_SENSITIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|secret|"
        r"password|auth[_-]?header)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=\-]{12,}['\"]?"
    ),
    re.compile(r"\b(?:sk-[A-Za-z0-9_\-]{12,}|sk-ant-[A-Za-z0-9_\-]{12,})"),
    re.compile(r"\btp-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"\bcpk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"\bglm-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"\bAIza[A-Za-z0-9_\-]{12,}"),
    re.compile(r"\bAKIA[A-Z0-9]{16}"),
    re.compile(r"\bnvapi-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{12,}"),
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]{16,}"),
    re.compile(r"\b[0-9a-f]{64}\b"),
)


def _contains_sensitive(text: str) -> bool:
    if not text:
        return False
    return any(p.search(text) is not None for p in _SENSITIVE_PATTERNS)


def main() -> int:
    parser = argparse.ArgumentParser(description="清理 data/spill 历史日志")
    parser.add_argument("--delete", action="store_true", help="实际删除（默认 dry-run）")
    parser.add_argument("--keep-days", type=int, default=7, help="保留最近 N 天（默认 7）")
    parser.add_argument("--root", type=str, default=".", help="仓库根目录（默认 .）")
    args = parser.parse_args()

    spill_dir = Path(args.root) / "data" / "spill"
    if not spill_dir.is_dir():
        print(f"[错误] spill 目录不存在: {spill_dir}")
        return 2

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.keep_days)
    candidates: list[tuple[Path, int, bool]] = []  # (path, size, has_sensitive)
    total_size = 0
    sensitive_count = 0

    for f in sorted(spill_dir.glob("*.txt")):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            size = f.stat().st_size
        except OSError:
            continue
        if mtime >= cutoff:
            continue  # 保留最近 keep-days 的文件
        has_sensitive = False
        try:
            if size <= 2_000_000:  # 大文件只读前 2MB 判定，避免 OOM
                head = f.read_text(encoding="utf-8", errors="replace")
            else:
                with f.open("r", encoding="utf-8", errors="replace") as fh:
                    head = fh.read(2_000_000)
            has_sensitive = _contains_sensitive(head)
        except OSError:
            pass
        candidates.append((f, size, has_sensitive))
        total_size += size
        if has_sensitive:
            sensitive_count += 1

    mode = "删除" if args.delete else "将删除(dry-run)"
    print(f"[{mode}] spill 目录: {spill_dir}")
    print(f"  过期文件(>{args.keep_days}天): {len(candidates)} 个, 共 {total_size/1024/1024:.1f} MB")
    print(f"  其中含密钥形态: {sensitive_count} 个")
    if not candidates:
        print("  无需清理。")
        return 0

    for f, size, has_sensitive in candidates[:20]:
        tag = " 🔑" if has_sensitive else ""
        print(f"  - {f.name} ({size/1024:.0f} KB){tag}")
    if len(candidates) > 20:
        print(f"  ... 其余 {len(candidates)-20} 个")

    if not args.delete:
        print("\n[提示] 加 --delete 实际删除。审计清单将写入 data/spill_cleanup_<ts>.log")
        return 0

    # 写审计清单（先记录，再删除）
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    audit_path = Path(args.root) / "data" / f"spill_cleanup_{ts}.log"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit: list[dict[str, object]] = []
    deleted = 0
    freed = 0
    for f, size, has_sensitive in candidates:
        try:
            audit.append({
                "path": str(f),
                "size_bytes": size,
                "has_sensitive": has_sensitive,
                "deleted_at": datetime.now(timezone.utc).isoformat(),
            })
            f.unlink()
            deleted += 1
            freed += size
        except OSError as e:
            print(f"  [失败] {f.name}: {e}")
    audit_path.write_text(
        json.dumps({"count": deleted, "freed_bytes": freed, "files": audit},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[完成] 删除 {deleted} 个文件, 释放 {freed/1024/1024:.1f} MB")
    print(f"      审计清单: {audit_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
