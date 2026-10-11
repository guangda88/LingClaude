#!/usr/bin/env python3
"""datalog aggregator —— 飞轮闭合 P0#1（diagnosis §L/§M 执行序切片）。

把 ~/.lingclaude/datalog/YYYY-MM-DD.jsonl（只有写没有读的最大隐藏 sink，
每天 ~300 KB 沉没）聚合为按日快照，双写两处：
  1. data/arch_ledger/arch_m6_snapshot/<yyyymmdd>.json
     —— 填上 arch_ledger.py:42 T_SNAP 全仓零写入的空壳；
     —— 纳入 self_audit_trigger LEDGER_TYPES 后，新快照 = fingerprint 变化 → 触发返审。
  2. knowledge.db rules 表（name=datalog_snapshot:<yyyymmdd>, category=datalog_snapshot）
     —— 对齐 self_optimizer/state_store_ext.py health_counts 的 datalog_snapshots 计数，
        F0 四指标基线取数 0→N 闭环立现。

聚合维度（规格）：model / path / cached_pct（cached=in/(in+cached) 估算）；
附加 all_events（4 类事件每日计数）作为 F0 基线锚点。
保留策略：7 天滚动删除旧快照（T_SNAP JSON + KB rules 同步清理）。

用法:
  python3 scripts/datalog_aggregator.py                 # 聚合今天
  python3 scripts/datalog_aggregator.py --date 2026-09-21
  python3 scripts/datalog_aggregator.py --days 7        # 聚合最近 7 天
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lingclaude.core.state_store import StateStore  # noqa: E402

DATALOG_DIR = Path.home() / ".lingclaude" / "datalog"
SNAP_TYPE = "arch_m6_snapshot"          # 与 arch_ledger.py T_SNAP 一致
RETENTION_DAYS = 7
# 对齐 state_store_ext.DATALOG_SNAPSHOT_CATEGORY（= FeedbackCategory.TELEMETRY）
KB_CATEGORY = "telemetry"
KB_NAME_PREFIX = "datalog_snapshot:"


def _kb() -> "KnowledgeBase":  # noqa: F821
    from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase
    return KnowledgeBase()


def _ensure_snap_dirs() -> None:
    """写前建父目录（StateStore.save 内部吞写错误，缺目录会静默丢数据）。"""
    root = _store()._json_backend._root
    (root / SNAP_TYPE).mkdir(parents=True, exist_ok=True)


def _store() -> StateStore:
    return StateStore(backend="json", root=ROOT / "data" / "arch_ledger")


def _aggregate_day(day: str) -> dict | None:
    """聚合单日 datalog JSONL → 快照 payload。文件缺失返回 None。"""
    path = DATALOG_DIR / f"{day}.jsonl"
    if not path.exists():
        return None

    # model 维度: calls/latency/tokens(in/out/cached)/cached_pct
    models: dict[str, dict] = defaultdict(lambda: {
        "calls": 0, "latency_ms_total": 0.0, "tokens_in": 0,
        "tokens_out": 0, "tokens_cached": 0,
    })
    # path 维度: 调用计数
    paths: dict[str, int] = defaultdict(int)
    # 事件类型计数（F0 基线锚点）
    events: dict[str, int] = defaultdict(int)
    # 行为闸/降级告警的信号分布
    signals: dict[str, int] = defaultdict(int)
    nudge_count = 0
    block_count = 0
    l5_scores: list[float] = []
    bad_lines = 0

    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                bad_lines += 1
                continue
            et = ev.get("event_type", "<missing>")
            events[et] += 1

            if et == "model.call":
                m = ev.get("model", "unknown")
                cost = ev.get("cost") or {}
                rec = models[m]
                rec["calls"] += 1
                rec["latency_ms_total"] += float(ev.get("latency_ms") or 0)
                rec["tokens_in"] += int(cost.get("in") or 0)
                rec["tokens_out"] += int(cost.get("out") or 0)
                rec["tokens_cached"] += int(cost.get("cached") or 0)
                p = ev.get("path")
                if p:
                    paths[p] += 1
            elif et == "t0.behavior.check":
                checks = ev.get("checks") or {}
                for name, verdict in checks.items():
                    signals[f"t0:{name}:{verdict}"] += 1
                nudges = ev.get("nudges") or []
                nudge_count += len(nudges)
                if ev.get("should_block"):
                    block_count += 1
            elif et == "tool.degradation.alert":
                sig = ev.get("signal", "unknown")
                signals[f"degradation:{sig}"] += 1
            elif et == "l5.audit.round":
                score = ev.get("consistency_score")
                if isinstance(score, (int, float)):
                    l5_scores.append(float(score))

    # cached_pct = cached / (in + cached)，无分母时 None
    model_out = {}
    for m, rec in models.items():
        denom = rec["tokens_in"] + rec["tokens_cached"]
        model_out[m] = {
            "calls": rec["calls"],
            "latency_ms_avg": round(rec["latency_ms_total"] / rec["calls"], 1)
            if rec["calls"] else None,
            "tokens_in": rec["tokens_in"],
            "tokens_out": rec["tokens_out"],
            "tokens_cached": rec["tokens_cached"],
            "cached_pct": round(rec["tokens_cached"] / denom, 4) if denom else None,
        }

    return {
        "day": day,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_events": sum(events.values()),
        "bad_lines": bad_lines,
        "all_events": dict(events),
        "models": model_out,
        "paths": dict(paths),
        "signals": dict(signals),
        "t0_nudges": nudge_count,
        "t0_blocks": block_count,
        "l5_rounds": len(l5_scores),
        "l5_consistency_avg": round(sum(l5_scores) / len(l5_scores), 4)
        if l5_scores else None,
    }


def _write_kb(day: str, payload: dict) -> None:
    """KB rules 双写：让 facade health_counts 的 datalog_snapshots 计数非 0。"""
    from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase
    from lingclaude.self_optimizer.learner.models import (
        FeedbackCategory,
        LearnedRule,
        Pattern,
    )
    kb = KnowledgeBase()
    summary = (
        f"events={payload['total_events']} "
        + " ".join(f"{k}={v}" for k, v in sorted(payload["all_events"].items()))
        + f" t0_nudges={payload['t0_nudges']} blocks={payload['t0_blocks']}"
    )
    if payload.get("l5_consistency_avg") is not None:
        summary += f" l5_avg={payload['l5_consistency_avg']}"
    # 幂等由 id PRIMARY KEY + INSERT OR REPLACE 保证（同日重跑 = 覆盖更新）
    rule = LearnedRule(
        id=f"m6-{day.replace('-', '')}",
        name=f"{KB_NAME_PREFIX}{day}",
        description=summary[:500],
        category=FeedbackCategory.TELEMETRY,
        pattern=Pattern(),
        tools=(),
        frequency=payload["total_events"],
        confidence=1.0,      # 机器聚合事实，非推断
        quality_score=0.0,
    )
    r = kb.add_rule(rule)
    if not r.success:
        raise RuntimeError(f"KB add_rule failed: {r.error}")


def _cleanup_old(keep_days: int) -> list[str]:
    """7 天滚动删除：T_SNAP JSON + KB rules 同名条目。"""
    cutoff = (date.today() - timedelta(days=keep_days)).strftime("%Y%m%d")
    removed: list[str] = []
    snap_dir = ROOT / "data" / "arch_ledger" / SNAP_TYPE
    if snap_dir.is_dir():
        for f in sorted(snap_dir.glob("*.json")):
            if f.stem < cutoff:
                f.unlink()
                removed.append(f"T_SNAP:{f.stem}")
    try:
        from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase
        from lingclaude.self_optimizer.learner.models import FeedbackCategory
        kb = KnowledgeBase()
        r = kb.get_all_rules(category=FeedbackCategory.TELEMETRY, limit=10_000)
        if r.success:
            for rule in r.data or ():
                # name 形如 datalog_snapshot:YYYY-MM-DD → 比较数字日期
                stem = rule.name.rsplit(":", 1)[-1].replace("-", "")
                if stem and stem < cutoff:
                    kb.delete_rule(rule.id)
                    removed.append(f"KB:{rule.name}")
    except Exception:
        # KB 滚动清理失败不阻塞主流程（T_SNAP 文件清理已在上文完成）
        pass
    return removed


def main() -> int:
    ap = argparse.ArgumentParser(description="datalog 按日聚合 → m6_snapshot")
    ap.add_argument("--date", help="聚合指定日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--days", type=int, default=1,
                    help="从今天往前聚合 N 天（默认 1）")
    ap.add_argument("--cleanup-only", action="store_true",
                    help="只执行 7 天滚动删除，不聚合")
    args = ap.parse_args()

    if args.cleanup_only:
        removed = _cleanup_old(RETENTION_DAYS)
        print(f"cleanup removed {len(removed)}: {removed}")
        return 0

    days: list[str] = []
    if args.date:
        days = [args.date]
    else:
        today = date.today()
        days = [(today - timedelta(days=i)).isoformat() for i in range(args.days)]

    _ensure_snap_dirs()
    kb = _kb()
    written = 0
    for day in sorted(days):
        payload = _aggregate_day(day)
        if payload is None:
            print(f"skip {day}: no datalog file")
            continue
        # 1) T_SNAP JSON（fingerprint 感知）
        _store().save(SNAP_TYPE, day.replace("-", ""), payload)
        # 2) KB rules（facade 计数闭环）
        _write_kb(day, payload)
        written += 1
        print(f"ok {day}: events={payload['total_events']} "
              f"models={len(payload['models'])} paths={len(payload['paths'])}")

    removed = _cleanup_old(RETENTION_DAYS)
    print(f"done: written={written}, cleanup_removed={len(removed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
