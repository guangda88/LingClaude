#!/usr/bin/env python3
"""exemption_review.py — 豁免台账复审初筛器（103 条 active 复审提前启动，2026-10-02）。

背景：exemption_review_calendar 排程 10-10~10-17 人工三问（理由仍成立？范围可
收窄？能否摘除？）。本脚本把**机器可判**的部分先判掉，人工队列收缩：

  D1 dead       源文件已不存在            → 建议摘除（守卫不再命中）
  D2 inert      lines==[]（行级空集=永不命中）→ 建议摘除（死重量）
  D3 shrink     部分行号越界（文件变短）    → 建议收缩至有效行集
  D4 overdue    review_due < 今日且无复核痕迹 → 逾期标记（进人工队列置顶）
  H  human      其余 → 人工三问队列（理由语义判断，机器不代劳）

用法：
  python3 scripts/exemption_review.py scan                 # 只读初筛报告
  python3 scripts/exemption_review.py apply --dry-run      # 展示将写入的复核痕迹
  python3 scripts/exemption_review.py apply                # 写 last_review 痕迹入台账
  python3 scripts/exemption_review.py apply --resolve dead,inert   # 连摘除一起做

纪律：
  - apply 只写复核痕迹（last_review 字段），**不改 reason/lines**——
    语义判断（D1/D2 是否真可摘）仍需人工确认后才动条目本体；
  - --resolve 只处理机器判定零歧义类（dead/inert），shrink 必须人工过目；
  - 全程写变更日志到 stdout，台账本身入 git 可回滚。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# journal 链：豁免写操作路由到 arch_ledger（2026-10-02 P0 修复）
sys.path.insert(0, str(ROOT / "scripts"))
from arch_ledger import (  # noqa: E402
    debt_resolve,
    exemption_delete_record,
    exemption_update_fields,
)
LEDGER = ROOT / "data" / "arch_ledger" / "arch_exemption"
# 源码根：台账 file 字段相对 lingclaude/ 包根（core/xxx → lingclaude/core/xxx）
SRC_ROOTS = (ROOT / "lingclaude", ROOT)


def _source_path(rel: str) -> Path | None:
    for base in SRC_ROOTS:
        p = base / rel
        if p.is_file():
            return p
    return None


def iter_entries() -> list[Path]:
    """全部豁免台账文件（含目录桶 M1:core/）。"""
    out: list[Path] = []
    if not LEDGER.is_dir():
        return out
    for p in sorted(LEDGER.rglob("*.json")):
        if p.name == "exemption_review_calendar.json":
            continue
        out.append(p)
    return out


def _entry_kind(path_rel: Path) -> str:
    """台账种类 → 消费语义。G11b=活体豁免匹配（_g11b_exempted）；
    M1:/M3:=生命周期档（tripartite.py 回收候选源，不再做守卫匹配）。"""
    name = path_rel.name
    if name.startswith("G11b:"):
        return "live"
    return "record"


def triage_one(p: Path, today: str) -> dict:
    """单条初筛。返回 {path, verdict, detail, entry}。

    消费方感知判定（2026-10-02 修正）：
      - live（G11b）: dead/inert → 判 remove-json（守卫已不命中，档可摘）
      - record（M1/M3）: dead+state=active → stale-record（state 该翻 removed，
        否则 tripartite 报 observing 伪态）；inert+active → paidoff-record
        （state 该翻 resolved，query_engine 先例）；**永不删档**（回收证据链）
    """
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — 坏台账从严：进人工队列
        return {"path": p, "verdict": "human", "detail": f"台账损坏: {e}", "entry": None}

    rel = d.get("file", "")
    lines = d.get("lines")
    due = d.get("review_due", "")
    kind = _entry_kind(p)
    verdicts: list[str] = []
    details: list[str] = []

    state = d.get("state", "") or "pending"
    _TERMINAL = {"migrated", "recycled", "removed", "resolved"}  # tripartite 映射自洽的终态

    if not rel:
        if kind == "record" and d.get("module"):
            # module 级豁免（无 file 字段的另一种 schema，如 plugin_lifecycle
            # 装配器家族载体）——存在性启发式不适用，归人工，机器不越权。
            return {"path": p, "verdict": "human",
                    "detail": f"module 级豁免（module={d['module']}），语义三问待人工",
                    "entry": d}
        verdicts.append("malformed" if kind == "record" else "dead")
        details.append("file 字段为空且无 module（malformed 记录）")

    src = _source_path(rel) if rel else None
    dead = rel and src is None
    if dead:
        if kind == "live":
            verdicts.append("dead")
            details.append(f"源文件不存在: {rel} → 豁免档可摘除")
        elif state in _TERMINAL:
            pass  # 终态 + 源亡 = 自洽归宿（migrated/recycled/removed 本就意味源不在），不动
        else:
            verdicts.append("stale")
            details.append(
                f"源文件不存在但 state={state}（tripartite 会报 observing 伪态，该翻 removed）"
            )
    elif src is not None:
        try:
            n_lines = len(src.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError as e:
            n_lines = -1
            details.append(f"源文件读取失败: {e}")
        if isinstance(lines, list) and not lines:
            if kind == "live":
                verdicts.append("inert")
                details.append("lines=[] 行级空集，守卫永不命中（档可摘除）")
            elif state in _TERMINAL:
                pass  # resolved + lines=[] = 清偿已记账，自洽
            else:
                verdicts.append("paidoff")
                details.append(f"lines=[] 清偿完毕但 state={state}（该翻 resolved）")
        elif isinstance(lines, list) and n_lines > 0:
            oob = [n for n in lines if isinstance(n, int) and n > n_lines]
            if oob:
                verdicts.append("shrink")
                keep = [n for n in lines if isinstance(n, int) and n <= n_lines]
                details.append(
                    f"越界 {len(oob)} 行（文件现 {n_lines} 行），可收缩至 {len(keep)} 行"
                )

    if due and due < today and d.get("last_review", "") < due:
        verdicts.append("overdue")
        details.append(f"review_due={due} 已逾期")

    order = ["stale", "paidoff", "dead", "inert", "malformed", "shrink", "overdue"]
    verdict = next((v for v in order if v in verdicts), "human")
    if not details:
        details.append("机器项全过，理由语义待人工三问")
    return {"path": p, "verdict": verdict, "detail": "; ".join(details), "entry": d}


def scan() -> list[dict]:
    today = time.strftime("%Y-%m-%d")
    results = []
    for p in iter_entries():
        results.append(triage_one(p, today))
    return results


def report(results: list[dict]) -> str:
    buckets: dict[str, list[dict]] = {}
    for r in results:
        buckets.setdefault(r["verdict"], []).append(r)
    out = [f"豁免台账初筛：共 {len(results)} 条"]
    for v in ("dead", "inert", "stale", "paidoff", "shrink", "malformed", "overdue", "human"):
        items = buckets.get(v, [])
        out.append(f"\n[{v}] {len(items)} 条")
        for r in items[:40]:
            name = r["path"].name
            out.append(f"  - {name}: {r['detail']}")
        if len(items) > 40:
            out.append(f"  ...（其余 {len(items) - 40} 条略）")
    return "\n".join(out)


_STATE_FLIP = {"stale": "removed", "paidoff": "resolved"}


def apply(results: list[dict], resolve: set[str], dry: bool) -> int:
    """resolve：dead,inert=摘除 live 豁免档；stale,paidoff=翻 record state 字段。

    record 类**永不删档**（tripartite 回收证据链消费，删档毁证据）。
    """
    changed = 0
    today = time.strftime("%Y-%m-%d")
    for r in results:
        v = r["verdict"]
        if v == "human":
            continue
        p, d = r["path"], r["entry"]
        if d is None:
            continue
        if v in ("dead", "inert") and v in resolve:
            if dry:
                print(f"[dry] 将摘除 live 豁免档 {p.name}（{v}: {r['detail']}）")
            else:
                exemption_delete_record(d["guard"], d["file"])
                print(f"[resolved] 摘除 live 豁免档 {p.name}（{v}: {r['detail']}）")
            changed += 1
            continue
        if v in _STATE_FLIP and v in resolve:
            new_state = _STATE_FLIP[v]
            if dry:
                print(f"[dry] {p.name}: state {d.get('state')} → {new_state}")
            else:
                exemption_update_fields(
                    d["guard"], d["file"],
                    state=new_state,
                    state_flip_note=f"exemption_review 初筛 {today}: {r['detail']}",
                )
                print(f"[resolved] {p.name}: state → {new_state}")
            changed += 1
            continue
        if d.get("last_review") == today:
            continue
        if dry:
            print(f"[dry] 将标记 last_review={today} → {p.name}（{v}）")
        else:
            exemption_update_fields(
                d["guard"], d["file"],
                last_review=today,
                last_review_note=f"机器初筛 {v}: {r['detail']}；语义三问待人工",
            )
            print(f"[marked] {p.name} → last_review={today}（{v}）")
        changed += 1
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["scan", "apply"])
    ap.add_argument("--resolve", default="", help="逗号分隔：dead,inert（摘除机器零歧义类）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    results = scan()
    print(report(results))
    if args.cmd == "apply":
        resolve = {s.strip() for s in args.resolve.split(",") if s.strip()}
        n = apply(results, resolve, args.dry_run)
        print(f"\napply: {n} 条台账变更{'（dry-run 未落盘）' if args.dry_run else ''}")
        # a11f2ec 清偿：journal 链修复路径已激活（apply 成功写 journal 即偿债）
        if not args.dry_run and n > 0:
            debt_resolve("exemption-journal-gap")
    return 0


if __name__ == "__main__":
    sys.exit(main())
