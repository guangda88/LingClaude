#!/usr/bin/env python3
"""NanoJev evals 层：决策流打标器（Jev evals 打标沉淀对应物）。

闭环定位（2026-10-27）:
    决策流 decisions_YYYYMM.jsonl 此前只写不读（7 处引用全是 record_decision，
    iter_entries 零生产调用）。本脚本是其第一个生产消费者：
        读决策流 → 打标 → 落打标台账 evals_YYYYMM.jsonl → （可选）喂 corrections。

打标规则（诚实基线——只打置信度分型，不打对错）:
    uncertain        verdict=gray，或 p ∈ [0.35, 0.65]   （高价值：系统自己没把握）
    likely_overkill  verdict=deny 且 p < 0.3              （低置信度还拦截，疑似误杀候选）
    confident_allow  verdict=allow 且 p ≥ 0.65            （高把握放行）
    confident_deny   verdict=deny 且 p ≥ 0.3              （有依据拦截）
    注：目前决策判定无 ground truth 回流（success_gate 判定后无后续验证闭环），
    打「对/错」标签会是编造——待 done 域闭环建立后，可在此升级为对错标注。

corrections 喂食（默认关，--feed-corrections N 开启）:
    取 uncertain 标签按 p 升序前 N 条，经 DataFlywheel.log_correction 落库：
    source="evals_labeler"（与 turn_learner/system_backfill 区分）、
    confidence=0.6（rule_conf_governor F5 语义=「可用但未经证实」档）。
    纪律：corrections 流本是用户纠正圣域（T9 语义门槛），机器样本必须显式
    开关 + source 可溯源准入，不得混入用户原话。

幂等:
    水位 data/arch_ledger/evals_watermark.json 记 last_ts，重跑只处理新增；
    台账按 (decision_ts, domain, action, verdict) 去重跳过。

用法:
    python3 scripts/evals_labeler.py                       # 打标新增（默认近7天）
    python3 scripts/evals_labeler.py --dry-run             # 只报告不写
    python3 scripts/evals_labeler.py --feed-corrections 5  # 喂5条最不确定样本
    python3 scripts/evals_labeler.py --month 202610 --since-days 30
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LEDGER_DIR = _PROJECT_ROOT / "data" / "arch_ledger"
_WATERMARK = _LEDGER_DIR / "evals_watermark.json"

# 置信度分型边界（与 docstring 规则同步）
_P_UNCERTAIN_LO, _P_UNCERTAIN_HI = 0.35, 0.65
_P_OVERKILL = 0.3


def _month_file(month: str) -> Path:
    return _LEDGER_DIR / f"decisions_{month}.jsonl"


def _current_month() -> str:
    return datetime.now().strftime("%Y%m")


def label_of(entry: dict) -> str:
    """置信度分型打标（见模块 docstring 规则表）。"""
    verdict = str(entry.get("verdict", ""))
    try:
        p = float(entry.get("p", 0.5))
    except (TypeError, ValueError):
        p = 0.5
    if verdict == "gray" or (_P_UNCERTAIN_LO <= p <= _P_UNCERTAIN_HI):
        return "uncertain"
    if verdict == "deny":
        return "likely_overkill" if p < _P_OVERKILL else "confident_deny"
    return "confident_allow" if verdict == "allow" else f"unlabeled_{verdict or 'none'}"


def _load_watermark() -> float:
    try:
        return float(json.loads(_WATERMARK.read_text(encoding="utf-8")).get("last_ts", 0.0))
    except Exception:  # noqa: BLE001 — 无水位/损坏 → 从头扫（幂等靠台账去重兜底）
        return 0.0


def _save_watermark(ts: float) -> None:
    _WATERMARK.write_text(
        json.dumps({"last_ts": ts, "updated": datetime.now().isoformat()}, ensure_ascii=False),
        encoding="utf-8",
    )


def _existing_keys(out_path: Path) -> set[tuple]:
    keys: set[tuple] = set()
    if not out_path.exists():
        return keys
    for line in out_path.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
            keys.add((d.get("decision_ts"), d.get("domain"), d.get("action"), d.get("verdict")))
        except json.JSONDecodeError:
            continue
    return keys


def iter_decision_entries(month: str, since_ts: float, limit: int) -> list[dict]:
    """读决策流新增条目（decision_log 的记录为单行 JSON，容错跳过坏行）。"""
    path = _month_file(month)
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if float(d.get("ts", 0.0)) < since_ts:
            continue
        out.append(d)
        if len(out) >= limit:
            break
    return out


def feed_corrections(entries: list[dict], n: int, db_path: str | None) -> int:
    """把最不确定的 n 条喂 DataFlywheel corrections（source 可溯源，conf=0.6）。"""
    uncertain = sorted(
        (e for e in entries if label_of(e) == "uncertain"),
        key=lambda e: float(e.get("p", 0.5)),
    )[:n]
    if not uncertain:
        return 0
    from lingclaude.core.data_flywheel import CorrectionEntry, DataFlywheel

    fw = DataFlywheel(db_path=db_path)
    fed = 0
    try:
        for e in uncertain:
            res = fw.log_correction(
                CorrectionEntry(
                    original_error=(
                        f"decision_uncertain domain={e.get('domain')} "
                        f"action={e.get('action')} verdict={e.get('verdict')} "
                        f"p={e.get('p')} reason={str(e.get('reason'))[:120]}"
                    ),
                    correction=(
                        f"[evals_labeler] 低把握决策待复核: {e.get('domain')}/"
                        f"{e.get('action')} p={e.get('p')} session={e.get('session_id')}"
                    ),
                    source="evals_labeler",
                    confidence=0.6,
                    applied_at=datetime.now().isoformat(),
                    experiment_id=None,
                )
            )
            if res.is_ok:
                fed += 1
    finally:
        fw.close()
    return fed


def main() -> int:
    ap = argparse.ArgumentParser(description="NanoJev 决策流打标器")
    ap.add_argument("--month", default=_current_month(), help="YYYYMM（默认当月）")
    ap.add_argument("--since-days", type=int, default=7, help="只处理近 N 天（0=全量）")
    ap.add_argument("--limit", type=int, default=5000, help="单次最大处理条数")
    ap.add_argument("--feed-corrections", type=int, default=0, metavar="N",
                    help="喂 N 条最不确定样本进 corrections（默认 0=关）")
    ap.add_argument("--db", default=None, help="DataFlywheel db 路径（默认真实库）")
    ap.add_argument("--dry-run", action="store_true", help="只报告不写台账/水位")
    args = ap.parse_args()

    since = _load_watermark()
    if args.since_days > 0 and since == 0.0:
        since = time.time() - args.since_days * 86400
    entries = iter_decision_entries(args.month, since, args.limit)
    if not entries:
        print(f"[evals] 无新增决策（month={args.month} since_ts={since:.0f}）")
        return 0

    keys = _existing_keys(_LEDGER_DIR / f"evals_{args.month}.jsonl")
    labeled: list[dict] = []
    skipped = 0
    for e in entries:
        key = (e.get("ts"), e.get("domain"), e.get("action"), e.get("verdict"))
        if key in keys:
            skipped += 1
            continue
        labeled.append(
            {
                "ts": time.time(),
                "ts_iso": datetime.now().isoformat(),
                "decision_ts": e.get("ts"),
                "domain": e.get("domain"),
                "action": e.get("action"),
                "verdict": e.get("verdict"),
                "p": e.get("p"),
                "label": label_of(e),
                "session_id": e.get("session_id"),
                "reason": str(e.get("reason"))[:200],
                # 2026-10-29 扩容第二轴：fast_route 自带 meta.outcome(accepted/fallback)
                # ——唯一真实 ground truth 种子（atomcode 发现），随打标落账供后续
                # 正确性核对/ECE 前置分析；其余域 outcome 缺失=诚实留空，不编造。
                "outcome": (e.get("meta") or {}).get("outcome"),
            }
        )

    stats = Counter(x["label"] for x in labeled)
    print(f"[evals] 新增 {len(labeled)} 条 / 去重跳过 {skipped} 条 / month={args.month}")
    for k, v in stats.most_common():
        print(f"  {k:16s} {v}")

    if args.dry_run:
        print("[evals] dry-run，不写台账/水位")
        return 0

    out_path = _LEDGER_DIR / f"evals_{args.month}.jsonl"
    with out_path.open("a", encoding="utf-8") as f:
        for x in labeled:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    _save_watermark(max(float(e.get("ts", 0.0)) for e in entries))
    print(f"[evals] 台账已追加 {out_path.name}，水位已推进")

    if args.feed_corrections > 0:
        fed = feed_corrections(labeled, args.feed_corrections, args.db)
        print(f"[evals] corrections 喂食 {fed}/{args.feed_corrections} 条（source=evals_labeler）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
