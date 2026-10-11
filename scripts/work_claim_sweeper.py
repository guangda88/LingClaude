#!/usr/bin/env python3
"""work_claim_sweeper —— 过期 work_claim 锁的周期清理器（P0 #2 / N4 时效查）。

背景（自审 P0 #2，2026-09-23）：
- work_claim 锁 TTL 过期后无主动清理循环：全仓零 daemon/scheduler，
  TTL 过期只能等下一次 bind/holder/check_paths **被动发现**；
- 实测 9 条 JSON 锁全部 `expires_at` 过 3 天仍存留（state=released 但 timestamp 未清理）。

本脚本补上"主动清理循环"：
1. **过期 held 锁 → 强制释放**（候选铁律 8：持锁者失联自动失效）：
   state=held 且 expires_at < now → 标 state=released + released_by=sweeper，
   并在 work_claim_log 记 `expired_swept` 事件（可审计、可回放）。
2. **过期 released 锁 → 归档**（已释放但时间戳未清理的历史残留）：
   仅从 work_claim 移入 work_claim_log 审计记录（锁本体删留由调用方决定，
   默认保留 record 本体、只补 swept 事件，避免破坏"可回放"审计语义）。

用法：
    python3 scripts/work_claim_sweeper.py [--dry-run] [--ledger PATH]
    --dry-run 只报告不修改（默认 False，直接清理）

退出码：0 = 正常（无论是否有过期锁）；1 = 内部故障（J5：故障必须可见）。

铁律锚点：候选铁律 8（时效域）/ N4 三查（时效查）/ J5（失败模式显式）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from lingclaude.core.state_store import StateStore

CLAIM_TYPE = "work_claim"
LOG_TYPE = "work_claim_log"
LEDGER_DEFAULT = Path(__file__).resolve().parents[1] / "data" / "arch_ledger"


def _now() -> float:
    return time.time()


def sweep(store: StateStore, dry_run: bool = False) -> dict:
    """扫描 work_claim 全部 record，清理过期锁。

    返回统计：{scanned, swept_expired_held, archived_released, kept, errors}
    """
    stats = {"scanned": 0, "swept_expired_held": 0, "archived_released": 0,
             "kept": 0, "errors": 0}
    keys = store.list_keys(CLAIM_TYPE)
    for key in keys:
        stats["scanned"] += 1
        rec = store.load(CLAIM_TYPE, key)
        if not rec:
            continue
        state = rec.get("state")
        expires_at = rec.get("expires_at") or 0
        expired = expires_at < _now()
        if not expired:
            stats["kept"] += 1
            continue
        # ── 过期分支 ────────────────────────────────────────────────
        if state == "held":
            # 过期 held 锁：持锁者失联 → 强制释放（候选铁律 8 时效自动失效）
            stats["swept_expired_held"] += 1
            if not dry_run:
                rec.update({"state": "released", "released_at": _now(),
                            "released_by": "sweeper", "swept": True})
                store.save(CLAIM_TYPE, key, rec)
                _log(store, key, "expired_swept", rec, {
                    "note": "sweeper: 过期 held 锁强制释放（失联自动失效）",
                })
        elif state == "released":
            # 过期 released 锁：历史残留，补审计事件
            stats["archived_released"] += 1
            if not dry_run:
                _log(store, key, "released_stale_archived", rec, {
                    "note": "sweeper: 已释放过期锁补审计归档",
                })
        else:
            stats["kept"] += 1
    return stats


def _log(store: StateStore, key: str, event: str, rec: dict, extra: dict) -> None:
    """work_claim_log 事件入账（与 WorkClaim._record 同构）。"""
    log_id = f"{key}:{event}:{int(_now())}"
    store.save(LOG_TYPE, log_id, {
        "claim_key": key,
        "event": event,
        "member": rec.get("member", "?"),
        "path": rec.get("path", ""),
        **extra,
        "at": _now(),
    })


def main() -> int:
    ap = argparse.ArgumentParser(description="work_claim 过期锁清理器（N4 时效查）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只报告不修改")
    ap.add_argument("--ledger", default=str(LEDGER_DEFAULT),
                    help=f"arch_ledger 根目录（默认 {LEDGER_DEFAULT}）")
    args = ap.parse_args()

    ledger = Path(args.ledger)
    if not ledger.is_dir():
        print(f"ERROR: arch_ledger 不存在: {ledger}", file=sys.stderr)
        return 1

    try:
        store = StateStore(backend="json", root=ledger)
        stats = sweep(store, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 - J5：故障必须可见，不静默
        print(f"ERROR: sweeper 故障: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    mode = "DRY-RUN" if args.dry_run else "EXECUTED"
    print(f"[{mode}] work_claim_sweeper 统计:")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
