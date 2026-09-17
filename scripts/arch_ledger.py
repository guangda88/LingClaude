#!/usr/bin/env python3
"""架构台账 arch_ledger —— 豁免/债务/快照统一走 StateStore（J4 清偿，守卫层先行）。

铁律返审结论（2026-09-17"算子革命先革自己的命"）：守卫体系的豁免台账、
debt record、M6 快照此前私连 .py 注释与 logs/*.jsonl —— 状态未归原语。
本模块把这些真相入册 StateStore（type=arch_*），从此可 create/transition/query。

record 约定：
  type=arch_debt       债务（临时直连/特判等），key=唯一 slug
                       payload: {kind, location, reason, due, resolve_hint, state}
                       state: open -> resolved；due < 今日即失去豁免资格（守卫红）
  type=arch_exemption  守卫豁免，key=f"{guard}:{rel_path}"（含 / 的 key 以嵌套目录存储）
                       payload: {guard, file, lines?, reason, granted, state: active}
                       lines 缺省 = 整文件豁免（M1）；有 lines = 行级豁免（M2/M3）
  type=arch_m6_snapshot M6 趋势快照，key=UTC 时间戳，payload=仪表报告本体

台账根目录：data/arch_ledger/（与业务状态隔离，可整体删除重放）。

用法:
  python3 scripts/arch_ledger.py debt add <slug> --location X --reason Y --due 2026-10-01
  python3 scripts/arch_ledger.py debt resolve <slug>
  python3 scripts/arch_ledger.py exemption add <guard> <rel_path> --reason Y [--lines 1,2]
  python3 scripts/arch_ledger.py exemption remove <guard> <rel_path>
  python3 scripts/arch_ledger.py list [debt|exemption|snapshot] [--open-only]
  python3 scripts/arch_ledger.py query expired    # 到期未清债务（守卫消费点）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lingclaude.core.state_store import StateStore  # noqa: E402

T_DEBT = "arch_debt"
T_EXEMPT = "arch_exemption"
T_SNAP = "arch_m6_snapshot"


def _store() -> StateStore:
    # 台账专用根目录：与业务状态隔离，且可整体删除重放
    return StateStore(backend="json", root=ROOT / "data" / "arch_ledger")


def _ensure_dirs(record_type: str, key: str) -> None:
    """写前建父目录。StateStore.save 内部吞写错误，父目录缺失会静默丢数据。"""
    root = _store()._json_backend._root
    (root / record_type / f"{key}.json").parent.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def debt_add(slug: str, location: str, reason: str, due: str, kind: str = "hardcoded_direct",
             resolve_hint: str = "") -> None:
    date.fromisoformat(due)  # 校验格式，fail fast
    s = _store()
    if s.load(T_DEBT, slug):
        raise SystemExit(f"债务已存在: {slug}（先 resolve 或换 slug）")
    _ensure_dirs(T_DEBT, slug)
    s.save(T_DEBT, slug, {
        "kind": kind, "location": location, "reason": reason,
        "due": due, "resolve_hint": resolve_hint,
        "state": "open", "created": _now(),
    })
    print(f"debt 入册: {slug} (due {due})")


def debt_resolve(slug: str) -> None:
    s = _store()
    rec = s.load(T_DEBT, slug)
    if not rec:
        raise SystemExit(f"债务不存在: {slug}")
    rec["state"] = "resolved"
    rec["resolved_at"] = _now()
    s.save(T_DEBT, slug, rec)
    print(f"debt 清偿: {slug}")


def exemption_add(guard: str, rel_path: str, reason: str, lines: list[int] | None = None) -> None:
    key = f"{guard}:{rel_path}"
    s = _store()
    payload = {
        "guard": guard, "file": rel_path, "reason": reason,
        "granted": date.today().isoformat(), "state": "active",
    }
    if lines:
        payload["lines"] = lines  # 行级豁免（M2/M3）；缺省=整文件（M1）
    _ensure_dirs(T_EXEMPT, key)
    s.save(T_EXEMPT, key, payload)
    print(f"exemption 入册: {key}" + (f" lines={lines}" if lines else ""))


def exemption_remove(guard: str, rel_path: str) -> None:
    key = f"{guard}:{rel_path}"
    s = _store()
    rec = s.load(T_EXEMPT, key)
    if not rec:
        raise SystemExit(f"豁免不存在: {key}")
    rec["state"] = "removed"
    rec["removed_at"] = _now()
    s.save(T_EXEMPT, key, rec)
    print(f"exemption 移除: {key}")


def iter_records(record_type: str) -> list[tuple[str, dict]]:
    """遍历某 type 全部 record（含嵌套 key：相对路径即 key）。"""
    items: list[tuple[str, dict]] = []
    s = _store()
    d = s._json_backend._root / record_type
    if d.is_dir():
        for f in sorted(d.rglob("*.json")):
            key = f.relative_to(d).with_suffix("").as_posix()
            rec = s.load(record_type, key)
            if rec is not None:
                items.append((key, rec))
    return items


def expired_debts() -> list[str]:
    """到期未清债务（守卫消费点：守卫查此清单，到期即红）。"""
    today = date.today().isoformat()
    out = []
    for slug, rec in iter_records(T_DEBT):
        if rec.get("state") == "open" and rec.get("due", "9999") < today:
            out.append(f"{slug} (location={rec.get('location')} due={rec['due']})")
    return out


def list_records(record_type: str, open_only: bool) -> None:
    for slug, rec in iter_records(record_type):
        if open_only and rec.get("state") not in ("open", "active"):
            continue
        print(json.dumps({"key": slug, **rec}, ensure_ascii=False))


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("debt")
    ds = d.add_subparsers(dest="op", required=True)
    da = ds.add_parser("add")
    da.add_argument("slug")
    da.add_argument("--location", required=True)
    da.add_argument("--reason", required=True)
    da.add_argument("--due", required=True)
    da.add_argument("--kind", default="hardcoded_direct")
    da.add_argument("--resolve-hint", default="")
    dr = ds.add_parser("resolve")
    dr.add_argument("slug")

    e = sub.add_parser("exemption")
    es = e.add_subparsers(dest="op", required=True)
    ea = es.add_parser("add")
    ea.add_argument("guard")
    ea.add_argument("rel_path")
    ea.add_argument("--reason", required=True)
    ea.add_argument("--lines", default="", help="行级豁免，逗号分隔；缺省=整文件")
    er = es.add_parser("remove")
    er.add_argument("guard")
    er.add_argument("rel_path")

    ls = sub.add_parser("list")
    ls.add_argument("rtype", choices=["debt", "exemption", "snapshot"])
    ls.add_argument("--open-only", action="store_true")

    q = sub.add_parser("query")
    q.add_argument("what", choices=["expired"])

    args = ap.parse_args()
    if args.cmd == "debt":
        if args.op == "add":
            debt_add(args.slug, args.location, args.reason, args.due, args.kind, args.resolve_hint)
        else:
            debt_resolve(args.slug)
    elif args.cmd == "exemption":
        if args.op == "add":
            lines = [int(x) for x in args.lines.split(",") if x.strip()] if args.lines else None
            exemption_add(args.guard, args.rel_path, args.reason, lines)
        else:
            exemption_remove(args.guard, args.rel_path)
    elif args.cmd == "list":
        t = {"debt": T_DEBT, "exemption": T_EXEMPT, "snapshot": T_SNAP}[args.rtype]
        list_records(t, args.open_only)
    elif args.cmd == "query":
        for line in expired_debts():
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
