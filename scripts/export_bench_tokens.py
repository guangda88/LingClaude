#!/usr/bin/env python3
"""评测 token 遥测导出 — 供外部 bench runner/collector 读取 lingclaude 的 token 用量。

背景 (2026-09-12 bench 复盘):
- atomcode 六 agent 横向评测时, lingclaude 被 `lingclaude run` 独立进程调用,
  token 记录落在 run-wd/.lingclaude/long_task_metrics.jsonl 或 turn_end journal,
  但评测侧 cost_collector.py 只认 token_monitor.db / 特定 jsonl → 记 not_in_journal。
- 本脚本: 扫描指定 run 工作目录下的 .lingclaude/*.jsonl (turn_end/metrics),
  汇总 input/output token, 输出 json 行, 供 cost_ledger 补采。

用法:
  export_bench_tokens.py <run_workdir> [--json]

输出 (--json):
  {"agent": "lingclaude", "input_tokens": N, "output_tokens": N,
   "token_scope": "bench_run_workdir", "files": [...]}
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

TOKEN_RE = re.compile(
    r'"(input_tokens|cache_read_input_tokens|prompt_tokens)"\s*:\s*(\d+)')
OUT_RE = re.compile(
    r'"(output_tokens|completion_tokens)"\s*:\s*(\d+)')


def scan(base: Path) -> tuple[int, int, list[str]]:
    tin = tout = 0
    files: list[str] = []
    for f in sorted(base.rglob("*.jsonl")):
        if not (f.parent.name == ".lingclaude" or ".lingclaude" in f.parts):
            continue
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        i = sum(int(m.group(2)) for m in TOKEN_RE.finditer(txt))
        o = sum(int(m.group(2)) for m in OUT_RE.finditer(txt))
        if i or o:
            tin += i
            tout += o
            files.append(str(f))
    return tin, tout, files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_workdir", help="评测 run 工作目录 (含 .lingclaude/)")
    ap.add_argument("--json", action="store_true", help="输出 json 单行")
    args = ap.parse_args()
    base = Path(args.run_workdir)
    if not base.exists():
        print(f"ERR: {base} 不存在", file=sys.stderr)
        return 2
    tin, tout, files = scan(base)
    if args.json:
        print(json.dumps({
            "agent": "lingclaude",
            "input_tokens": tin,
            "output_tokens": tout,
            "total_tokens": tin + tout,
            "token_scope": "bench_run_workdir",
            "files": files,
        }, ensure_ascii=False))
    else:
        print(f"input={tin} output={tout} total={tin + tout}")
        print(f"source files ({len(files)}):")
        for f in files:
            print(f"  {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
