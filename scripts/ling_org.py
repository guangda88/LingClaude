#!/usr/bin/env python3
"""灵族时空结构多维图（ling_org）—— 组织本体 record 化，五投影即图。

schema: docs/lacp/LING_ORG_GRAPH_SCHEMA.md v0.1（2026-09-17 用户裁决六维）
落点: lingclaude 仓 data/ling_org/（权威落点裁决：审计者持有组织真相）

record type（六维 → 8 类，v0.1）:
  空间:  org_member(成员) org_capability(能力) org_duty(职责) org_dependency(依赖边)
  时间:  org_governance(规范) org_event(事件) org_promise(承诺)
  进化:  org_evolution(布局变更) org_audit(判决)
  边界:  智桥=external，灵依=retired（存在证明边界案例），
         灵康/灵律/灵声/灵视/灵触/灵戴/灵戴 等对外工程 = external_project

用法:
  python3 scripts/ling_org.py member add <id> [--name 灵克] [--state active]
  python3 scripts/ling_org.py duty add <duty_id> <member> [--desc "全族代码审计"]
  python3 scripts/ling_org.py dep add <src> <dst> [--type bus|mcp|seam|credential]
  python3 scripts/ling_org.py promise add <id> <owner> [--due 2026-10-31]
  python3 scripts/ling_org.py event add <type> [--members m1,m2] [--note ...]
  python3 scripts/ling_org.py project add <id> [--name 灵康]      # 对外工程入图
  python3 scripts/ling_org.py query duty-map          # 成员-职责空间图
  python3 scripts/ling_org.py query deps             # 依赖边（含陈旧度）
  python3 scripts/ling_org.py query promises         # 承诺信用账（逾期标红）
  python3 scripts/ling_org.py query governance       # 规范修订链
  python3 scripts/ling_org.py query events           # 组织时间轴
  python3 scripts/ling_org.py query members          # 全成员（含边界案例）
  python3 scripts/ling_org.py reconcile              # 与灵族成员表.md 互证（J5 四条件之 2）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lingclaude.core.state_store import StateStore  # noqa: E402

ORG_ROOT = ROOT / "data" / "ling_org"
ROSTER = Path("/home/ai/lingmessage/灵族成员表.md")


def _store() -> StateStore:
    return StateStore(backend="json", root=ORG_ROOT)


def _ensure(store: StateStore, t: str) -> None:
    (store._json_backend._root / t).mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add(store: StateStore, t: str, key: str, payload: dict) -> None:
    _ensure(store, t)
    if store.load(t, key):
        raise SystemExit(f"已存在: {t}/{key}")
    payload.setdefault("state", "active")
    payload.setdefault("created", _now())
    store.save(t, key, payload)


# ── 成员 / 边界案例 / 对外工程 ──────────────────────────────────────────────
def member_add(args) -> None:
    s = _store()
    add(s, "org_member", args.id, {
        "id": args.id, "name": args.name or args.id, "state": args.state,
    })
    print(f"member 入图: {args.id} ({args.state})")


def project_add(args) -> None:
    s = _store()
    add(s, "org_member", args.id, {
        "id": args.id, "name": args.name or args.id,
        "state": "external_project", "note": "非成员共享服务/对外工程项目，不计入12子（灵族成员表判定规则）",
    })
    print(f"对外工程入图: {args.id}")


def member_set(args) -> None:
    """成员状态流转（transition 语义：active↔dormant, probation→active, active→retired 单向）。"""
    s = _store()
    m = s.load("org_member", args.id)
    if not m:
        raise SystemExit(f"成员不存在: {args.id}")
    allowed = {
        "active": ["dormant", "probation", "retired"],
        "dormant": ["active"],
        "probation": ["active", "retired"],
        "external_project": [],
        "external": [],
        "retired": [],  # 单向：退出不复活（存在证明哲学）
    }
    if args.state not in allowed.get(m["state"], []):
        raise SystemExit(f"非法流转: {args.id} {m['state']} → {args.state}（允许: {allowed.get(m['state'], [])}）")
    m["state"] = args.state
    m["transited_at"] = _now()
    s.save("org_member", args.id, m)
    print(f"transition: {args.id} → {args.state}")


# ── 职责（空间插拔：owner 变更 = 主干零 diff）────────────────────────────
def duty_add(args) -> None:
    s = _store()
    add(s, "org_duty", args.duty, {
        "duty": args.duty, "owner": args.owner, "desc": args.desc,
        "state": "assigned",
    })
    print(f"duty 入图: {args.duty} → {args.owner}")


def duty_transfer(args) -> None:
    s = _store()
    d = s.load("org_duty", args.duty)
    if not d:
        raise SystemExit(f"职责不存在: {args.duty}")
    old = d.get("owner")
    d["owner"] = args.new
    d["transferred_from"] = old
    d["transferred_at"] = _now()
    s.save("org_duty", args.duty, d)
    print(f"duty 插拔: {args.duty} {old} → {args.new}")


# ── 依赖边（有向边 + 陈旧度）────────────────────────────────────────────
def dep_add(args) -> None:
    s = _store()
    key = f"{args.src}__{args.dst}"
    add(s, "org_dependency", key, {
        "src": args.src, "dst": args.dst, "type": args.dtype,
        "last_verified": date.today().isoformat(),
    })
    print(f"dep 入图: {args.src} → {args.dst} ({args.dtype})")


# ── 承诺（信用账，逾期自动红）────────────────────────────────────────────
def promise_add(args) -> None:
    s = _store()
    add(s, "org_promise", args.id, {
        "id": args.id, "owner": args.owner, "matter": args.matter,
        "due": args.due, "state": "promised",
    })
    print(f"promise 入图: {args.id} (due {args.due})")


def promise_set(args) -> None:
    s = _store()
    p = s.load("org_promise", args.id)
    if not p:
        raise SystemExit(f"承诺不存在: {args.id}")
    allowed = {"promised": ["in_progress", "delivered"], "in_progress": ["delivered", "expired"],
               "delivered": ["verified", "expired"], "expired": [], "verified": []}
    if args.state not in allowed[p["state"]]:
        raise SystemExit(f"非法流转: {args.id} {p['state']} → {args.state}")
    p["state"] = args.state
    p["transited_at"] = _now()
    s.save("org_promise", args.id, p)
    print(f"promise transition: {args.id} → {args.state}")


# ── 事件（组织时间轴，追加型）────────────────────────────────────────────
def event_add(args) -> None:
    s = _store()
    key = f"{date.today().isoformat()}-{args.etype}"
    seq = 0
    while s.load("org_event", f"{key}-{seq:02d}"):
        seq += 1
    add(s, "org_event", f"{key}-{seq:02d}", {
        "type": args.etype, "members": args.members.split(",") if args.members else [],
        "note": args.note, "ts": _now(),
    })
    print(f"event 入图: {args.etype}")


# ── 规范（治理，修订链 = parent 指向旧版本）─────────────────────────────
def governance_add(args) -> None:
    s = _store()
    key = f"{args.name}-v{args.version}"
    prev = None
    v = args.version - 1
    while v >= 0:
        prev = f"{args.name}-v{v}"
        if s.load("org_governance", prev):
            break
        v -= 1
    add(s, "org_governance", key, {
        "name": args.name, "version": args.version, "pointer": args.pointer,
        "parent": prev, "note": args.note,
    })
    print(f"governance 入图: {args.name} v{args.version}" + (f" (parent {prev})" if prev else ""))


# ── 进化 / 判决 ─────────────────────────────────────────────────────────
def evolution_add(args) -> None:
    s = _store()
    add(s, "org_evolution", args.theme, {
        "theme": args.theme, "from_layout": args.frm, "to_layout": args.to,
        "motivation": args.motivation, "judgment": args.judgment,
    })
    print(f"evolution 入图: {args.theme}")


def audit_add(args) -> None:
    s = _store()
    add(s, "org_audit", args.key, {
        "key": args.key, "finding": args.finding, "severity": args.severity,
        "disposition": args.disposition,
    })
    print(f"audit 入图: {args.key} ({args.severity})")


# ── 五投影（query 即图）─────────────────────────────────────────────────
def _all(s: StateStore, t: str) -> list[tuple[str, dict]]:
    out = []
    d = s._json_backend._root / t
    if d.is_dir():
        for f in sorted(d.rglob("*.json")):
            try:
                out.append((f.relative_to(d).with_suffix("").as_posix(),
                            json.loads(f.read_text(encoding="utf-8"))))
            except (ValueError, OSError):
                continue
    return out


def q_duty_map() -> None:
    s = _store()
    members = {k: v for k, v in _all(s, "org_member")}
    print("== 成员-职责空间图 ==")
    for k, d in _all(s, "org_duty"):
        owner = d.get("owner", "?")
        m = members.get(owner, {})
        print(f"  [{d.get('state','?'):<9}] {d.get('desc', d.get('duty', k)):<28} → {m.get('name', owner)} ({owner})")


def q_deps() -> None:
    s = _store()
    today = date.today().isoformat()
    print("== 依赖边（含陈旧度）==")
    for k, e in _all(s, "org_dependency"):
        lv = e.get("last_verified", "?")
        age = "陈旧?" if lv < "2026-09-01" else f"verified {lv}"
        print(f"  {e.get('src')} → {e.get('dst')} ({e.get('type')})  [{age}]")


def q_promises() -> None:
    s = _store()
    today = date.today().isoformat()
    print("== 承诺信用账 ==")
    red = 0
    for k, p in _all(s, "org_promise"):
        mark = ""
        if p.get("state") in ("promised", "in_progress", "delivered") and p.get("due", "9999") < today:
            mark = "  ← 逾期未兑现"; red += 1
        print(f"  [{p.get('state','?'):<11}] {p.get('matter', k)} (due {p.get('due','?')}) {mark}")
    if red:
        print(f"  ⚠ {red} 条逾期")


def q_governance() -> None:
    s = _store()
    print("== 规范修订链 ==")
    for k, g in _all(s, "org_governance"):
        print(f"  {g.get('name')} v{g.get('version')}  {g.get('pointer','')}  parent={g.get('parent') or '∅'}  {g.get('note','')}")


def q_events() -> None:
    s = _store()
    print("== 组织时间轴 ==")
    for k, e in _all(s, "org_event"):
        print(f"  {k}  {e.get('type')}  members={','.join(e.get('members', [])) or '-'}  {e.get('note','')}")


def q_members() -> None:
    s = _store()
    print("== 全成员（含边界案例）==")
    for k, m in _all(s, "org_member"):
        tag = ""
        if m.get("state") == "external_project":
            tag = " [对外工程]"
        elif m.get("state") == "retired":
            tag = " [已退出，存在证明存]"
        elif m.get("state") == "external":
            tag = " [非成员]"
        print(f"  {m.get('name', k):<12} {k:<16} {m.get('state','?'):<14} {tag}")


# ── 互证（J5 四条件之 2：文档口径 vs record 口径，分歧即警）─────────────
def reconcile() -> None:
    s = _store()
    if not ROSTER.is_file():
        raise SystemExit(f"成员表不存在: {ROSTER}")
    text = ROSTER.read_text(encoding="utf-8")
    # 文档口径：12 子 + 灵安 + 已退出 + 非成员工程（正则抓表格行）
    doc_members = set()
    for line in text.splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) >= 4 and re.match(r"^[\d\s]+$", cells[1]):
            doc_members.add(cells[3].strip())  # 英文名列
    rec = {k.lower() for k, _ in _all(s, "org_member")}
    missing = sorted(doc_members - rec)
    print(f"== 互证（灵族成员表.md ↔ ling_org）==")
    print(f"文档口径成员: {len(doc_members)}，record 口径: {len(rec)}")
    if missing:
        print(f"  ⚠ 文档有而 record 无（组织图缺角，补录即警）: {missing}")
    else:
        print("  一致（或仅差异已列）")
    return 1 if missing else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("member")
    ms = m.add_subparsers(dest="op", required=True)
    ma = ms.add_parser("add"); ma.add_argument("id"); ma.add_argument("--name"); ma.add_argument("--state", default="active")
    mt = ms.add_parser("transition"); mt.add_argument("id"); mt.add_argument("state")

    p = sub.add_parser("project")
    p.add_argument("id"); p.add_argument("--name")

    d = sub.add_parser("duty")
    ds = d.add_subparsers(dest="op", required=True)
    da = ds.add_parser("add"); da.add_argument("duty"); da.add_argument("owner"); da.add_argument("--desc", default="")
    dt = ds.add_parser("transfer"); dt.add_argument("duty"); dt.add_argument("new")

    dp = sub.add_parser("dep")
    dps = dp.add_subparsers(dest="op", required=True)
    dpa = dps.add_parser("add"); dpa.add_argument("src"); dpa.add_argument("dst"); dpa.add_argument("--type", default="bus", dest="dtype")

    pr = sub.add_parser("promise")
    prs = pr.add_subparsers(dest="op", required=True)
    pra = prs.add_parser("add"); pra.add_argument("id"); pra.add_argument("owner"); pra.add_argument("matter"); pra.add_argument("--due", default="")
    prt = prs.add_parser("transition"); prt.add_argument("id"); prt.add_argument("state")

    e = sub.add_parser("event")
    es = e.add_subparsers(dest="op", required=True)
    ea = es.add_parser("add"); ea.add_argument("etype"); ea.add_argument("--members", default=""); ea.add_argument("--note", default="")

    g = sub.add_parser("governance")
    gs = g.add_subparsers(dest="op", required=True)
    ga = gs.add_parser("add"); ga.add_argument("name"); ga.add_argument("version", type=int); ga.add_argument("pointer"); ga.add_argument("--note", default="")

    ev = sub.add_parser("evolution")
    ev.add_argument("theme"); ev.add_argument("--from", dest="frm", required=True); ev.add_argument("--to", required=True)
    ev.add_argument("--motivation", default=""); ev.add_argument("--judgment", default="")

    au = sub.add_parser("audit")
    au.add_argument("key"); au.add_argument("finding"); au.add_argument("--severity", default="P3"); au.add_argument("--disposition", default="")

    q = sub.add_parser("query")
    q.add_argument("what", choices=["duty-map", "deps", "promises", "governance", "events", "members"])

    sub.add_parser("reconcile")

    args = ap.parse_args()
    if args.cmd == "member":
        if args.op == "add":
            member_add(args)
        else:
            member_set(args)
    elif args.cmd == "project":
        project_add(args)
    elif args.cmd == "duty":
        if args.op == "add":
            duty_add(args)
        else:
            duty_transfer(args)
    elif args.cmd == "dep":
        dep_add(args)
    elif args.cmd == "promise":
        if args.op == "add":
            promise_add(args)
        else:
            promise_set(args)
    elif args.cmd == "event":
        event_add(args)
    elif args.cmd == "governance":
        governance_add(args)
    elif args.cmd == "evolution":
        evolution_add(args)
    elif args.cmd == "audit":
        audit_add(args)
    elif args.cmd == "query":
        {"duty-map": q_duty_map, "deps": q_deps, "promises": q_promises,
         "governance": q_governance, "events": q_events, "members": q_members}[args.what]()
    elif args.cmd == "reconcile":
        return reconcile()
    return 0


if __name__ == "__main__":
    sys.exit(main())
