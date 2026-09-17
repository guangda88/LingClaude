#!/usr/bin/env python3
"""M6 接缝生态趋势仪表（铁律 J5 守卫件套，2026-09-17 用户确认）。

定位：仪表不是守卫——不设红线、不判对错、不退出非零（除非内部错误），
只保证"接缝生态没人看"这件事不发生。输出三类趋势：
  1. 各 SeamType 实现数分布（静态扫描 register(SeamType.*) 调用点）
  2. 单实现接缝的"龄期"（注册点所在文件的 git 首次入库时间）
  3. 装配 spec 总数周环比（WiringSpec( 调用点计数，对比上次快照）

用法:
  python3 scripts/seam_trend_inspect.py           # 巡检并追加快照
  python3 scripts/seam_trend_inspect.py --json    # 机器可读输出

日志/快照: StateStore（data/arch_ledger/，type=arch_m6_snapshot）——
2026-09-17 铁律返审后归原语：快照曾私连 logs/seam_trend.jsonl（J4 违例），
现走 arch_ledger 同款台账，可 query/回放。
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "lingclaude"
sys.path.insert(0, str(ROOT))

from lingclaude.core.state_store import StateStore  # noqa: E402

T_SNAP = "arch_m6_snapshot"


def _snap_store() -> StateStore:
    return StateStore(backend="json", root=ROOT / "data" / "arch_ledger")


def _py_files(base: Path):
    return sorted(p for p in base.rglob("*.py") if p.name != "__init__.py")


def _parse(f: Path):
    try:
        return ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
    except SyntaxError:
        return None


def scan_seam_registrations() -> dict[str, list[dict]]:
    """静态扫描 register(SeamType.X, "name", ...) 调用点。

    返回 {seam_type_value: [{file, line, name}]}。
    静态口径只统计源码注册点；运行时动态注册（经 PluginLoader 等）
    以 --json 消费方自行结合 registry.dump 判断（M6 是仪表，不追求完备）。
    """
    out: dict[str, list[dict]] = {}
    for f in _py_files(SRC):
        rel = f.relative_to(ROOT).as_posix()
        tree = _parse(f)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "register"):
                continue
            args = node.args
            if len(args) < 2 or not isinstance(args[0], ast.Attribute):
                continue
            val = args[0].value
            if not (isinstance(val, ast.Name) and val.id == "SeamType"):
                continue
            name = "?"
            if isinstance(args[1], ast.Constant) and isinstance(args[1].value, str):
                name = args[1].value
            out.setdefault(args[0].attr, []).append(
                {"file": rel, "line": node.lineno, "name": name}
            )
    return out


def scan_wiring_specs() -> dict[str, int]:
    """统计各文件 WiringSpec( 调用点数（装配 spec 总量口径）。"""
    counts: Counter[str] = Counter()
    for f in _py_files(SRC):
        tree = _parse(f)
        if tree is None:
            continue
        n = sum(
            1 for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "WiringSpec"
        )
        if n:
            counts[f.relative_to(ROOT).as_posix()] = n
    return dict(counts)


def git_first_commit_age(rel_path: str) -> str | None:
    """文件首次入库日期（YYYY-MM-DD）；git 不可用或新文件返回 None。"""
    try:
        r = subprocess.run(
            ["git", "log", "--follow", "--format=%ad", "--date=short",
             "--diff-filter=A", "--", rel_path],
            cwd=ROOT, capture_output=True, text=True, timeout=15,
        )
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        return lines[-1] if lines else None  # --follow 后最早的在最后
    except (subprocess.TimeoutExpired, OSError):
        return None


def load_last_snapshot() -> dict | None:
    """取最近一次快照（StateStore，type=arch_m6_snapshot，按 ts key 排序）。"""
    recs = []
    s = _snap_store()
    d = s._json_backend._root / T_SNAP
    if d.is_dir():
        for f in sorted(d.glob("*.json")):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
                recs.append(rec)
            except (ValueError, OSError):
                continue
    return recs[-1] if recs else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args()

    regs = scan_seam_registrations()
    specs = scan_wiring_specs()

    # 1) 实现数分布
    dist = {k: len(v) for k, v in sorted(regs.items())}

    # 2) 单实现接缝龄期（回收候选：铁律修剪语法第 1 条，M6 驱动）
    single_impl_ages = {}
    for st, points in sorted(regs.items()):
        if len(points) == 1:
            age_date = git_first_commit_age(points[0]["file"])
            single_impl_ages[st] = {"file": points[0]["file"], "first_commit": age_date}

    # 3) 装配 spec 总数周环比（对上次快照）
    total_specs = sum(specs.values())
    last = load_last_snapshot()
    spec_delta = None
    if last and "total_specs" in last:
        spec_delta = total_specs - last["total_specs"]

    report = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seam_impl_distribution": dist,
        "single_impl_seams": single_impl_ages,
        "total_specs": total_specs,
        "specs_by_file": specs,
        "total_specs_delta_vs_last": spec_delta,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("== M6 接缝生态趋势仪表 ==")
        print(f"时间: {report['ts']}")
        print("\n[1] SeamType 实现数分布")
        for st, n in dist.items():
            mark = "  ← 单实现（回收候选观察）" if n == 1 else ""
            print(f"  {st:<14} {n}{mark}")
        print("\n[2] 单实现接缝龄期（修剪语法：龄期长且无扩展意图 → 回收候选）")
        if single_impl_ages:
            for st, info in single_impl_ages.items():
                print(f"  {st:<14} {info['file']}  首次入库: {info['first_commit'] or '未入库/新文件'}")
        else:
            print("  （无）")
        print(f"\n[3] 装配 spec 总数: {total_specs}"
              + (f"（较上次快照 {'+' if (spec_delta or 0) >= 0 else ''}{spec_delta}）" if spec_delta is not None else "（首次巡检，无环比）"))
        print("\n定位提醒：仪表不判对错；单实现≠必回收，需结合扩展意图人工审查（铁律修剪语法）。")

    # 追加快照（StateStore 归原语；key=UTC ts，可按时间回放）
    s = _snap_store()
    snap_key = report["ts"].replace(":", "").replace("+", "Z")
    import sys as _sys

    (s._json_backend._root / T_SNAP).mkdir(parents=True, exist_ok=True)
    s.save(T_SNAP, snap_key, report)
    print(f"\n快照已入册: arch_m6_snapshot/{snap_key}", file=_sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
