#!/usr/bin/env python3
"""修复 data/session_history.json 历史残留明文 key（落盘级清洗）。

背景 (2026-09-13 安全审计):
- session_history.json 90810 条历史记录，其中 4 条 query 含 key 形态明文。
- query_engine._append_to_session_history 已接入 redact（新写入脱敏），
  但历史数据仍残留，需一次性清洗。

策略:
- 读取 -> 对每条的 query/title 字段 redact -> 原子写回（tmp + rename）。
- 幂等：redact 幂等，重复执行无副作用。
- 不打印任何 key 值；只打印命中统计。
- 写前备份到 data/session_history.json.bak_<ts>（0600）。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from lingclaude.core.redact import redact, contains_sensitive

ROOT = Path("/home/ai/lingclaude")
TARGET = ROOT / "data" / "session_history.json"


def main() -> int:
    if not TARGET.exists():
        print(f"[错误] 目标不存在: {TARGET}")
        return 2

    raw = TARGET.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, list):
        print("[错误] 期望 JSON 数组")
        return 2

    changed = 0
    hit_before = 0
    for rec in data:
        if not isinstance(rec, dict):
            continue
        for field in ("query", "title"):
            val = rec.get(field)
            if not isinstance(val, str):
                continue
            if contains_sensitive(val):
                hit_before += 1
            scrubbed = redact(val)
            if scrubbed != val:
                rec[field] = scrubbed
                changed += 1

    if changed == 0:
        print(f"[完成] {TARGET.name}: 无残留需清洗 ({len(data)} 条记录)")
        return 0

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = ROOT / "data" / f"session_history.json.bak_{ts}"
    backup.write_bytes(raw.encode("utf-8"))
    os.chmod(backup, 0o600)

    # 原子写回
    tmp = TARGET.with_suffix(".json.tmp")
    tmp.write_bytes(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
    os.replace(tmp, TARGET)
    os.chmod(TARGET, 0o600)

    print(f"[完成] 清洗 {changed} 个字段 (命中前 {hit_before} 条), {len(data)} 条记录")
    print(f"[备份] {backup.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
