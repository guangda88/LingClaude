#!/usr/bin/env python3
"""工作区并发快照 — Kelly「世界即通信总线」的机械化（R6）。

背景（docs/SYSTEMS_THEORY_SYNTHESIS.md 附录 B）：多成员共享工作区，2026-09-01
会话内半成品改动两次被并发 commit 扫动。与其靠"发消息互相同步"，不如直接读
共享世界本身——把 HEAD + 工作区状态快照留档，前后对照即可检测并发扫动。

用法:
    python scripts/prechange_snapshot.py record             # 记录快照（含与上一条的对比告警）
    python scripts/prechange_snapshot.py record --label "修 auth 前"
    python scripts/prechange_snapshot.py check              # 只对比不记录

判据：HEAD 未动（没有新提交）而 status/diffstat 变了 ⇒ 工作区被并发修改。
注意这是信号不是判决——你自己 fork 出来的改动也一样触发，所以要配 --label 用。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WATCH_PATH = REPO_ROOT / ".lingclaude" / "workspace_watch.json"
HISTORY_CAP = 50


def _git(args: list[str]) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    return proc.stdout if proc.returncode == 0 else f"(git error: {proc.stderr.strip()[:120]})"


def take_snapshot() -> dict:
    status = _git(["status", "--porcelain"])
    diffstat = _git(["diff", "--stat"]).strip().splitlines()
    return {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "head": _git(["rev-parse", "HEAD"]).strip(),
        "dirty_count": len([ln for ln in status.splitlines() if ln.strip()]),
        "status": status,
        "diffstat_tail": diffstat[-1] if diffstat else "",
    }


def load_history() -> list[dict]:
    if WATCH_PATH.exists():
        try:
            return json.loads(WATCH_PATH.read_text(encoding="utf-8")).get("history", [])
        except (json.JSONDecodeError, OSError):
            pass
    return []


def diff_signal(prev: dict | None, cur: dict) -> list[str]:
    """HEAD 未动而工作区变化 → 并发改动信号（Kelly：读世界，别只等消息）。"""
    if prev is None:
        return []
    signals: list[str] = []
    if prev.get("head") == cur.get("head") and prev.get("status") != cur.get("status"):
        prev_files = set(prev.get("status", "").splitlines())
        cur_files = set(cur.get("status", "").splitlines())
        new = sorted(cur_files - prev_files)
        gone = sorted(prev_files - cur_files)
        if new:
            signals.append(f"HEAD 未动，新增改动 {len(new)} 项（并发成员改动的典型信号）: {new[:5]}")
        if gone:
            signals.append(f"HEAD 未动，消失 {len(gone)} 项改动（可能被并发 commit 收走或清理）: {gone[:5]}")
    return signals


def cmd_record(label: str) -> int:
    history = load_history()
    prev = history[-1] if history else None
    snap = take_snapshot()
    snap["label"] = label
    signals = diff_signal(prev, snap)
    history.append(snap)
    WATCH_PATH.parent.mkdir(parents=True, exist_ok=True)
    WATCH_PATH.write_text(
        json.dumps({"history": history[-HISTORY_CAP:]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[record] 快照 #{len(history)}（{snap['ts']} head={snap['head'][:8]} dirty={snap['dirty_count']}）{('— ' + label) if label else ''}")
    for s in signals:
        print(f"  ⚠️ {s}", file=sys.stderr)
    return 0


def cmd_check() -> int:
    history = load_history()
    prev = history[-1] if history else None
    cur = take_snapshot()
    signals = diff_signal(prev, cur)
    if prev is None:
        print("[check] 无历史快照，先 record 一次")
        return 0
    print(f"[check] 上次快照 {prev.get('ts')}（label={prev.get('label', '')}）→ 现在 head={cur['head'][:8]} dirty={cur['dirty_count']}")
    for s in signals:
        print(f"  ⚠️ {s}", file=sys.stderr)
    return 0 if not signals else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="工作区并发快照（记录/对照）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_rec = sub.add_parser("record", help="记录快照并对比上一条")
    p_rec.add_argument("--label", default="", help="备注（如任务名/阶段）")
    sub.add_parser("check", help="只对比不记录")
    args = parser.parse_args(argv)
    if args.cmd == "record":
        return cmd_record(args.label)
    if args.cmd == "check":
        return cmd_check()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
