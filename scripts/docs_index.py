#!/usr/bin/env python3
"""docs/ 统一入口索引生成器（弱点③ MVP，2026-10-10）。

扫描 docs/ 全树 markdown + 根级入口文档，生成 docs/INDEX.md：
- 按一级目录分桶，每篇一行：路径 | 标题（首个 # 标题，缺省用文件名）| 行数 | mtime
- 幂等可重跑；总账对齐 md 实测数
- 退出码：0=全量覆盖；2=有文件未入索引（供 CI 后续接线）

用法：python3 scripts/docs_index.py [--check]
"""
from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
OUT = DOCS / "INDEX.md"
# 根级入口文档（不属于 docs/ 但属统一入口的一部分）
ROOT_ENTRIES = ["AGENTS.md", "CRUSH.md"]

BUCKET_DESC = {
    "research": "研究/评审/调研",
    "handover": "交接与状态快照",
    "agent-knowledge": "Agent 知识/守卫/铁律",
    "arch_ledger": "架构台账相关",
}


def list_md(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.md") if p != OUT and ".git" not in p.parts)


def doc_title(p: Path) -> str:
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if s.startswith("# "):
                    return s[2:].strip()[:80]
    except OSError:
        pass
    return p.stem


def bucket_of(p: Path) -> str:
    rel = p.relative_to(ROOT)
    if rel.parts[0] != "docs":
        return "入口文档"
    return rel.parts[1] if len(rel.parts) > 2 else "docs(根)"


def fmt_mtime(p: Path) -> str:
    return datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验覆盖，不重写 INDEX.md")
    args = ap.parse_args()

    files = list_md(DOCS)
    entries = [ROOT / e for e in ROOT_ENTRIES if (ROOT / e).exists()]
    all_docs = files + entries

    buckets: dict[str, list[Path]] = {}
    for p in all_docs:
        buckets.setdefault(bucket_of(p), []).append(p)

    lines: list[str] = [
        "# docs 统一入口索引（自动生成，勿手编）",
        "",
        f"> 生成器：`scripts/docs_index.py` · 生成时间：{datetime.datetime.now():%Y-%m-%d %H:%M} · "
        f"共 {len(all_docs)} 篇（docs/ {len(files)} + 根入口 {len(entries)}）",
        "",
    ]
    for b in sorted(buckets):
        lines.append(f"## {b}")
        if b in BUCKET_DESC:
            lines.append(f"_{BUCKET_DESC[b]}_")
        lines.append("")
        lines.append("| 文档 | 标题 | 行数 | 最后更新 |")
        lines.append("|---|---|---|---|")
        for p in buckets[b]:
            rel = p.relative_to(ROOT)
            try:
                nlines = sum(1 for _ in p.open("r", encoding="utf-8", errors="replace"))
            except OSError:
                nlines = -1
            lines.append(f"| `{rel}` | {doc_title(p)} | {nlines} | {fmt_mtime(p)} |")
        lines.append("")

    if args.check:
        if not OUT.exists():
            print(f"[MISS] {OUT} 不存在")
            return 2
        have = len(
            {ln.split("`")[1] for ln in OUT.read_text().splitlines() if ln.startswith("| `")}
        )
        missing = len(all_docs) - have
        print(f"[CHECK] INDEX.md 已登记 {have}/{len(all_docs)}，未登记 {missing}")
        return 0 if missing == 0 else 2

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] docs/INDEX.md 已生成：{len(all_docs)} 篇（{len(buckets)} 个桶）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
