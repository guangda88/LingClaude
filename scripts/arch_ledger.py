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
  python3 scripts/arch_ledger.py verify           # P2② journal 哈希链离线校验
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
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


# ── P2② journal 哈希链（2026-10-02）──
# 动机：台账 record 文件是覆盖写（只留最新态），「只翻 state 不删档」纪律
# 依赖人自觉 + 事后对账。journal 以 append-only 行 + 单向哈希链把该纪律
# 升级为数据结构：每行 = {seq, ts, op, type, key, payload, entry_digest}，
# entry_digest = sha256(prev_digest + 规范化行内容)。链断/行改/行删皆可验。
# 存量档不回填，链从启用点起算（旧档 = pre-chain）。


def _journal_path() -> Path:
    return _store()._json_backend._root / "journal.jsonl"


def _canonical_line(entry: dict) -> str:
    """journal 行规范化：键排序 + 紧凑分隔符，跨进程复算一致。"""
    return json.dumps(entry, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def _read_journal() -> list[dict]:
    p = _journal_path()
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except ValueError:
            out.append({"corrupt": True, "raw": ln[:200]})
    return out


def _append_journal(op: str, record_type: str, key: str, payload: dict) -> str:
    """追加一行进 journal 并盖章哈希链（fail-soft：链失败不阻断台账操作）。"""
    try:
        entries = _read_journal()
        chain_head = ""
        for e in entries:
            if not e.get("corrupt"):
                chain_head = e.get("entry_digest", "")
        entry = {
            "seq": len(entries), "ts": _now(), "op": op,
            "type": record_type, "key": key, "payload": payload,
        }
        entry["entry_digest"] = hashlib.sha256(
            (chain_head + _canonical_line(entry)).encode("utf-8")).hexdigest()
        p = _journal_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(_canonical_line(entry) + "\n")
        return entry["entry_digest"]
    except Exception:
        # 哈希链是审计增强层，绝不为它阻断台账主操作（豁免/债务写入必须活）。
        return ""


def verify_journal() -> int:
    """离线校验 journal 链完整性。返回 0=全绿，1=断链/篡改，2=无 journal。"""
    entries = _read_journal()
    if not entries:
        print("journal 不存在或为空：链未启用（pre-chain 存量不回填）")
        return 2
    prev = ""
    seq = 0
    ok = True
    for e in entries:
        seq += 1
        if e.get("corrupt"):
            print(f"× seq {seq}: 行损坏（半行写入或手改）")
            ok = False
            continue
        expected_payload = {k: v for k, v in e.items()
                            if k not in ("entry_digest",)}
        digest = hashlib.sha256(
            (prev + _canonical_line(expected_payload)).encode("utf-8")
        ).hexdigest()
        if digest != e.get("entry_digest"):
            print(f"× seq {seq} (op={e.get('op')} key={e.get('key')}): "
                  f"digest 不匹配——行内容被改或链断裂")
            ok = False
        if e.get("seq") != seq - 1:
            print(f"× seq {seq}: 序号错位（期望 {seq - 1}，实际 {e.get('seq')}）"
                  f"——疑似删行/插行")
            ok = False
        prev = e.get("entry_digest", "")
    if ok:
        print(f"journal 链校验通过：{len(entries)} 行全绿")
        return 0
    return 1


def debt_add(slug: str, location: str, reason: str, due: str, kind: str = "hardcoded_direct",
             resolve_hint: str = "") -> None:
    date.fromisoformat(due)  # 校验格式，fail fast
    s = _store()
    if s.load(T_DEBT, slug):
        raise SystemExit(f"债务已存在: {slug}（先 resolve 或换 slug）")
    _ensure_dirs(T_DEBT, slug)
    payload = {
        "kind": kind, "location": location, "reason": reason,
        "due": due, "resolve_hint": resolve_hint,
        "state": "open", "created": _now(),
    }
    s.save(T_DEBT, slug, payload)
    _append_journal("debt_add", T_DEBT, slug, payload)
    print(f"debt 入册: {slug} (due {due})")


def debt_resolve(slug: str) -> None:
    s = _store()
    rec = s.load(T_DEBT, slug)
    if not rec:
        raise SystemExit(f"债务不存在: {slug}")
    rec["state"] = "resolved"
    rec["resolved_at"] = _now()
    s.save(T_DEBT, slug, rec)
    _append_journal("debt_resolve", T_DEBT, slug, rec)
    print(f"debt 清偿: {slug}")


def exemption_add(guard: str, rel_path: str, reason: str, lines: list[int] | None = None) -> None:
    key = f"{guard}:{rel_path}"
    s = _store()
    payload = {
        "guard": guard, "file": rel_path, "reason": reason,
        "granted": date.today().isoformat(), "state": "active",
        # 铁律 4 债务语法：豁免须带复审账期（守卫 test_exemptions_not_past_review 强制），
        # 否则入册即触发「豁免无 review_due」红。默认 30 天复审周期，与存量豁免同款。
        "review_due": (date.today() + timedelta(days=30)).isoformat(),
    }
    if lines:
        payload["lines"] = lines  # 行级豁免（M2/M3）；缺省=整文件（M1）
    _ensure_dirs(T_EXEMPT, key)
    s.save(T_EXEMPT, key, payload)
    _append_journal("exemption_add", T_EXEMPT, key, payload)
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
    _append_journal("exemption_remove", T_EXEMPT, key, rec)
    print(f"exemption 移除: {key}")



def exemption_delete_record(guard: str, rel_path: str) -> None:
    """标记豁免档为已删除（p.unlink() 的 journal-aware 替选）。

    与 exemption_remove() 等价，但命名更贴近 exemption_review 的语义——
    exemption_review 语义是「摘除 live 档」，不等同于「移除豁免身份」（后者可能留痕）。
    本函数把文件 state 翻为 removed 并写 journal，与 exemption_remove() 同链。
    """
    exemption_remove(guard, rel_path)


def exemption_update_fields(guard: str, rel_path: str, **fields: str) -> None:
    """部分更新豁免档字段（journal-aware）。

    用途：exemption_review.py 的 state flip / last_review 写操作，
    原来直接 p.write_text() 跳过了 journal 链；本函数补全该路径。

    Args:
        guard: 守卫 ID（e.g. "G11b"）
        rel_path: 相对于 lingclaude/ 的路径（e.g. "core/hook_registry.py"）
        **fields: 要合并进记录的额外字段（e.g. state="removed", last_review="2026-10-02"）
    """
    key = f"{guard}:{rel_path}"
    s = _store()
    rec = s.load(T_EXEMPT, key)
    if not rec:
        raise SystemExit(f"豁免记录不存在: {key}（exemption_review 与台账不一致）")
    rec.update(fields)
    s.save(T_EXEMPT, key, rec)
    _append_journal("exemption_update_fields", T_EXEMPT, key, rec)
    print(f"exemption 字段更新: {key} ← {fields}")

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

    v = sub.add_parser("verify", help="P2② journal 哈希链离线校验")
    v.add_argument("what", nargs="?", default="journal")

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
    elif args.cmd == "verify":
        return verify_journal()
    return 0


if __name__ == "__main__":
    sys.exit(main())
