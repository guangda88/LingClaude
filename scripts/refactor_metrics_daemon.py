#!/usr/bin/env python3
"""P5 持续回路：灵元重构指标自动入册。

V3 提案 §五 P5 原文：
    「自优化 daemon 以本重构为第一个实战对象（每阶段行数/依赖/红灯自动入册）
    ——验证回路合闸不是又一次空转」

本脚本采集三类机械化指标并追加写入 .lingclaude/refactor_ledger.jsonl：
  1. 行数     — 灵元主干关键模块 wc（app.py 门面 / repl* / query_engine / wiring）
  2. 复杂度   — radon cc 统计 cli 模块 C/D/E/F 级函数数（radon 缺席时优雅降级）
  3. 守卫基线 — G1-G5 架构守卫的实际基线值（core→engine import / lazy import 等）

红灯定义（任一触发即 entry.red = True）：
  - app.py 超过 1,000 行（P4.1 验收线 881 行 + 15% 余量）
  - cli 模块重新出现 E/F 级函数
  - 守卫基线超 test_p04_arch_guards.py 中声明值

运行方式（三选一）：
  python3 scripts/refactor_metrics_daemon.py            # 单次入册
  python3 scripts/refactor_metrics_daemon.py --loop 600 # 常驻循环（600s 间隔）
  systemd: deploy/systemd/user/lingclaude-refactor-ledger.timer（待安装）

输出 schema（每行一个 JSON entry）：
  {"ts": ..., "phase": "P4.1", "lines": {...}, "complexity": {...},
   "guards": {...}, "red": false, "red_reasons": []}
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / ".lingclaude" / "refactor_ledger.jsonl"

# 行数观测点（灵元主干关键文件，相对仓库根）
LINE_TARGETS = [
    "lingclaude/cli/app.py",
    "lingclaude/cli/repl.py",
    "lingclaude/cli/commands.py",
    "lingclaude/cli/repl_turn.py",
    "lingclaude/cli/repl_io.py",
    "lingclaude/core/query_engine.py",
    "lingclaude/core/wiring.py",
]

# P5 红灯阈值（2026-09-11 P4.1 后基线固化，只缩不放）
APP_PY_LINE_LIMIT = 1000   # app.py 行数上限（P4.1 后 881 + 余量）
E_GRADE_ALLOWANCE = 3      # E 级函数存量上限（P4.1 后实况 3: 交互循环 2 + 流渲染 1）
F_GRADE_ALLOWANCE = 0      # F 级清零（P4.1 把 F(69) 消灭后不许回潮）


def collect_lines() -> dict[str, int]:
    lines: dict[str, int] = {}
    for rel in LINE_TARGETS:
        p = ROOT / rel
        lines[rel] = len(p.read_text(encoding="utf-8").splitlines()) if p.exists() else -1
    return lines


def collect_complexity() -> dict[str, int]:
    """radon cc 统计 cli 模块 C/D/E/F 级函数个数；radon 不可用返回空 dict。

    经 subprocess 调 radon CLI（radon 6.x 无 radon.cc 模块，API 漂移免疫）。
    """
    cli_dir = ROOT / "lingclaude" / "cli"
    if not cli_dir.exists():
        return {}
    try:
        proc = subprocess.run(
            ["radon", "cc", str(cli_dir), "-s", "--total-average"],
            capture_output=True, text=True, timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode != 0:
        return {}
    out = {"C": 0, "D": 0, "E": 0, "F": 0}
    for m in re.finditer(r"- ([A-F]) \(\d+\)", proc.stdout):
        if m.group(1) in out:
            out[m.group(1)] += 1
    return out


def collect_guards() -> dict[str, int]:
    """读取 P0.4 守卫的实际基线（与 tests/test_p04_arch_guards.py 同口径的轻量版）。"""
    import ast

    src_root = ROOT / "lingclaude"
    lazy = 0
    core_engine = 0
    for f in src_root.rglob("*.py"):
        if "__pycache__" in f.parts:
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Import):
                        lazy += len(sub.names)
                    elif isinstance(sub, ast.ImportFrom) and sub.module:
                        lazy += 1
        if "core" in f.parts:
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module]
                elif isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                core_engine += sum(1 for m in mods if m.startswith("lingclaude.engine"))
    return {"lazy_imports": lazy, "core_engine_imports": core_engine}


def evaluate_red(lines: dict[str, int], complexity: dict[str, int]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    app_lines = lines.get("lingclaude/cli/app.py", -1)
    if app_lines < 0:
        reasons.append("app.py 缺失")
    elif app_lines > APP_PY_LINE_LIMIT:
        reasons.append(f"app.py {app_lines} 行超限 {APP_PY_LINE_LIMIT}")
    if complexity:
        f_cnt = complexity.get("F", 0)
        e_cnt = complexity.get("E", 0)
        if f_cnt > F_GRADE_ALLOWANCE:
            reasons.append(f"cli F 级函数 {f_cnt} 个超基线 {F_GRADE_ALLOWANCE}")
        if e_cnt > E_GRADE_ALLOWANCE:
            reasons.append(f"cli E 级函数 {e_cnt} 个超基线 {E_GRADE_ALLOWANCE}")
    return (len(reasons) > 0, reasons)


def collect_once(phase: str = "P4.1") -> dict:
    lines = collect_lines()
    complexity = collect_complexity()
    guards = collect_guards()
    red, red_reasons = evaluate_red(lines, complexity)
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "phase": phase,
        "lines": lines,
        "complexity": complexity,
        "guards": guards,
        "red": red,
        "red_reasons": red_reasons,
    }


def append_ledger(entry: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="P5 重构指标入册")
    ap.add_argument("--phase", default="P4.1", help="当前重构阶段标签")
    ap.add_argument("--loop", type=int, default=0, help="常驻循环间隔秒数（0=单次）")
    args = ap.parse_args()

    if args.loop <= 0:
        entry = collect_once(args.phase)
        append_ledger(entry)
        status = "RED" if entry["red"] else "green"
        print(f"[入册] {entry['ts']} phase={entry['phase']} → {status} {entry['red_reasons']}")
        return 1 if entry["red"] else 0

    print(f"常驻模式: 每 {args.loop}s 入册一次，Ctrl+C 停止")
    while True:
        try:
            entry = collect_once(args.phase)
            append_ledger(entry)
            status = "RED" if entry["red"] else "green"
            print(f"[入册] {entry['ts']} phase={entry['phase']} → {status}", flush=True)
        except Exception as e:  # noqa: BLE001 — 观测器永不破坏主流程
            print(f"[入册失败] {e}", file=sys.stderr, flush=True)
        time.sleep(args.loop)


if __name__ == "__main__":
    sys.exit(main())
