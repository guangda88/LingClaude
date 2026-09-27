#!/usr/bin/env python3
"""skills_index 缝 — 全族 skill 惰性检索（文件即接缝，用后卸载零成本）。

索引：data/skills_index.json（name/member/path/desc，由 --rebuild 重新生成）
读取：search/list/read 三个子命令，read 时才把 SKILL.md 全文带出——
skill 本体无状态，加载即读、随轮次丢弃，无常驻注册面。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "data" / "skills_index.json"


def _load() -> list[dict]:
    return json.loads(INDEX.read_text(encoding="utf-8"))["skills"]


def search(keyword: str) -> list[dict]:
    k = keyword.lower()
    return [s for s in _load() if k in s["name"].lower() or k in s["desc"].lower()]


def read(name: str) -> str:
    hits = [s for s in _load() if s["name"] == name]
    if not hits:
        raise SystemExit(f"skill 未索引: {name}")
    return Path(hits[0]["path"]).read_text(encoding="utf-8", errors="ignore")


def rebuild() -> None:  # 与生成脚本同源；此处仅校验索引新鲜度
    data = json.loads(INDEX.read_text(encoding="utf-8"))
    missing = [s["path"] for s in data["skills"] if not Path(s["path"]).is_file()]
    print(f"skills={len(data['skills'])} 缺失={len(missing)}")
    for m in missing:
        print("  MISS", m)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "search":
        for s in search(sys.argv[2]):
            print(f"{s['name']:36s} [{s['member']:9s}] {s['desc'][:60]}")
    elif cmd == "read":
        print(read(sys.argv[2]))
    elif cmd == "rebuild":
        rebuild()
    else:  # list
        for s in _load():
            print(f"{s['name']:36s} [{s['member']:9s}] {s['desc'][:60]}")
