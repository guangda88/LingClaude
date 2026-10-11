#!/usr/bin/env python3
"""
lingos_tier2.py — lingOS Tier 2: Orchestrate 编排触发

设计原则 (灵元 1.0):
  - Tier 1 self-heal (lingos_tier1.py): 不变
  - Tier 2 orchestrate (本脚本): 触发其他 Layer 0 脚本
    - wake_with_task.py: 每 4h 检查一次 (避免过度唤醒)
    - sla_tracker.py: 每 1h 检查一次 (更频繁, 实时性优先)
  - 不直接发 LingBus 消息, 只触发其他脚本
  - 不做调度决策 (不替用户决定谁该做什么任务)

设计哲学 (本脚本):
  - NEVER 替 wake/sla 直接发消息 (那是它们的工作)
  - NEVER 自己做任务分发 (那是 wake_with_task 的工作)
  - 只在 tier1 健康 + 未触发冷却时触发

用法:
  python3 scripts/lingos_tier2.py             # 扫一次
  python3 scripts/lingos_tier2.py --watch 600 # 每 10min 循环
  python3 scripts/lingos_tier2.py --dry-run

部署目标: /home/ai/lingos/lingos_tier2.py (需 WSB 授权)
  systemd timer: lingos-tier2.timer (10min, 与 probe 错开)
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

LINGOS_HOME = Path("/home/ai/lingos")
REPO = Path("/home/ai/lingclaude")

WAKE_SCRIPT = REPO / "scripts" / "wake_with_task.py"
SLA_SCRIPT = REPO / "scripts" / "sla_tracker.py"

# 触发冷却
WAKE_COOLDOWN_MIN = 240   # 4h 内不重复触发 wake
SLA_COOLDOWN_MIN = 60     # 1h 内不重复触发 sla
TIER1_HEALTH_REQUIRED = True  # Tier 1 必须健康才触发 Tier 2


def check_tier1_health() -> bool:
    """检查 tier1 最近 30 min 是否 healthy"""
    snapshot = LINGOS_HOME / "snapshots.jsonl"
    if not snapshot.exists():
        return False
    try:
        with open(snapshot) as f:
            lines = [l for l in f.readlines() if l.strip()][-3:]
        if not lines:
            return False
        last_ts = json.loads(lines[-1]).get("ts", "")
        last_dt = datetime.strptime(last_ts, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        age_min = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
        return age_min < 30
    except Exception:
        return False


def check_tier1_actions() -> Dict[str, Any]:
    """查 tier1 最近的 actions 记录"""
    log = LINGOS_HOME / "tier1_actions.jsonl"
    if not log.exists():
        return {"last_action_ts": "", "recent": []}
    with open(log) as f:
        lines = [l for l in f.readlines() if l.strip()][-10:]
    last_ts = json.loads(lines[-1]).get("ts", "") if lines else ""
    return {"last_action_ts": last_ts, "recent_count": len(lines)}


def last_trigger_ts(script_name: str) -> str:
    """查 lingos_tier2 自己记录的最后触发时间"""
    log = LINGOS_HOME / "tier2_triggers.jsonl"
    if not log.exists():
        return ""
    try:
        with open(log) as f:
            for line in reversed(f.readlines()):
                if not line.strip():
                    continue
                entry = json.loads(line)
                if entry.get("script") == script_name:
                    return entry.get("ts", "")
    except Exception:
        pass
    return ""


def log_trigger(script_name: str, args: list, result: str) -> None:
    log = LINGOS_HOME / "tier2_triggers.jsonl"
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "script": script_name,
        "args": args,
        "result": result[:100],
    }
    with open(log, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def trigger_script(script_path: Path, args: list, dry_run: bool) -> str:
    """触发其他脚本, timeout 60s"""
    if not script_path.exists():
        return f"script not found: {script_path}"
    if dry_run:
        return f"DRY-RUN: would run {script_path.name} {' '.join(args)}"
    try:
        result = subprocess.run(
            ["python3", str(script_path)] + args,
            capture_output=True, text=True, timeout=60,
        )
        return f"rc={result.returncode}, stdout={result.stdout[:80]}"
    except subprocess.TimeoutExpired:
        return "TIMEOUT (>60s)"
    except Exception as e:
        return f"ERROR: {e}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", type=int, default=0, help="N 秒循环")
    parser.add_argument("--dry-run", action="store_true", help="只查不做")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)

    actions = []
    # 0. Tier 1 健康检查
    tier1_health = check_tier1_health()
    tier1_actions = check_tier1_actions()
    if TIER1_HEALTH_REQUIRED and not tier1_health:
        print(f"[tier2] ABORT: tier1 not healthy (snapshot stale >30min)")
        return

    # 1. wake_with_task 触发判断
    last_wake = last_trigger_ts("wake_with_task")
    should_wake = True
    if last_wake:
        try:
            last_dt = datetime.fromisoformat(last_wake)
            if (now - last_dt).total_seconds() < WAKE_COOLDOWN_MIN * 60:
                should_wake = False
        except Exception:
            pass
    if should_wake:
        result = trigger_script(WAKE_SCRIPT, ["--dry-run"] if args.dry_run else [], args.dry_run)
        actions.append(f"wake_with_task: {result}")
        log_trigger("wake_with_task", [], result)

    # 2. sla_tracker 触发判断
    last_sla = last_trigger_ts("sla_tracker")
    should_sla = True
    if last_sla:
        try:
            last_dt = datetime.fromisoformat(last_sla)
            if (now - last_dt).total_seconds() < SLA_COOLDOWN_MIN * 60:
                should_sla = False
        except Exception:
            pass
    if should_sla:
        result = trigger_script(SLA_SCRIPT, ["--report"] if args.dry_run else [], args.dry_run)
        actions.append(f"sla_tracker: {result}")
        log_trigger("sla_tracker", [], result)

    if actions:
        print(f"[tier2] {len(actions)} actions:")
        for a in actions:
            print(f"  - {a}")
    else:
        next_wake_in = WAKE_COOLDOWN_MIN
        next_sla_in = SLA_COOLDOWN_MIN
        if last_wake:
            try:
                next_wake_in = WAKE_COOLDOWN_MIN - (now - datetime.fromisoformat(last_wake)).total_seconds() / 60
            except Exception:
                pass
        if last_sla:
            try:
                next_sla_in = SLA_COOLDOWN_MIN - (now - datetime.fromisoformat(last_sla)).total_seconds() / 60
            except Exception:
                pass
        print(f"[tier2] {now.isoformat()} all in cooldown "
              f"(wake in {next_wake_in:.0f}min, sla in {next_sla_in:.0f}min)")

    if not args.watch:
        break_loop = True
    else:
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
