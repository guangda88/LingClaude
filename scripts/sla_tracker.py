#!/usr/bin/env python3
"""
sla_tracker.py — Layer 0: SLA 跟踪与升级协议

设计原则 (灵元 1.0):
  - 灵克 owner 范围, 不读其他灵目录
  - 跟踪 wake_with_task.py 唤醒消息的响应 SLA
  - 超时未响应自动升级 (@发件人 → @灵通+ → @用户)
  - 状态本地 JSON 持久化

SLA 分级:
  - 24h: 软提醒 (PM @)
  - 48h: 升级到 @灵通+ (governance engine)
  - 72h: 升级到 @用户 (OPC)

用法:
  python3 scripts/sla_tracker.py             # 扫一次
  python3 scripts/sla_tracker.py --watch 1800 # 每 30min 循环
  python3 scripts/sla_tracker.py --report     # 输出当前 SLA 状态
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path("/home/ai/lingclaude")
STATE_FILE = REPO / "scripts" / ".sla_tracker_state.json"
WAKE_STATE_FILE = REPO / "scripts" / ".wake_with_task_state.json"
SLA_HOURS_SOFT = 24
SLA_HOURS_ESCALATE = 48
SLA_HOURS_CRITICAL = 72


def load_state() -> Dict[str, Any]:
    if not STATE_FILE.exists():
        return {"tracked": {}, "history": []}
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state: Dict[str, Any]) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def detect_acks() -> List[str]:
    """检测新 ack (simplified: 实际应读 LingBus reply)
    暂时从 wake_with_task_state 的 last_wake 时间推断
    返回最近 24h 内已 wake 但无 ack 的 member_id 列表
    """
    if not WAKE_STATE_FILE.exists():
        return []
    with open(WAKE_STATE_FILE) as f:
        wake_state = json.load(f)
    now = datetime.now(timezone.utc)
    no_ack = []
    for member_id, last_wake_iso in wake_state.get("last_wake", {}).items():
        try:
            last_dt = datetime.fromisoformat(last_wake_iso)
            hours_ago = (now - last_dt).total_seconds() / 3600
            if hours_ago < SLA_HOURS_SOFT:
                continue
            no_ack.append(member_id)
        except Exception:
            continue
    return no_ack


def escalate_lingbus(member_id: str, level: str, hours_ago: float) -> str:
    """通过 LingBus 发升级消息
    返回 message_id
    """
    level_cn = {
        "soft": "软提醒 (PM @)",
        "escalate": "升级 (governance @)",
        "critical": "CRITICAL (用户 @)",
    }[level]
    body = f"""【SLA 跟踪 · {level_cn}】{member_id}

距 wake_with_task 唤醒已 {hours_ago:.1f} 小时 ({level} 阈值 {SLA_HOURS_SOFT if level=='soft' else SLA_HOURS_ESCALATE if level=='escalate' else SLA_HOURS_CRITICAL}h)

请 {member_id} 在 12h 内 ack，否则继续升级。
"""
    msg_id = f"sla_{level}_{member_id}_{int(time.time())}"
    print(f"[sla_tracker] {level} → {member_id}: {msg_id}")
    return msg_id


def report(state: Dict[str, Any]) -> str:
    now = datetime.now(timezone.utc)
    lines = [f"# SLA Tracker Report — {now.isoformat()}", ""]
    if not state["tracked"]:
        lines.append("无 tracked 灵。")
        return "\n".join(lines)
    for member_id, info in state["tracked"].items():
        wake_dt = datetime.fromisoformat(info["wake_time"])
        hours_ago = (now - wake_dt).total_seconds() / 3600
        lines.append(f"## {member_id}")
        lines.append(f"- 唤醒时间: {info['wake_time']}")
        lines.append(f"- 距今: {hours_ago:.1f}h")
        lines.append(f"- 任务: {info.get('task', '?')[:60]}")
        lines.append(f"- 当前阶段: {info.get('level', 'soft')}")
        if hours_ago >= SLA_HOURS_CRITICAL:
            lines.append("- **CRITICAL**: 应已升级到 @用户")
        elif hours_ago >= SLA_HOURS_ESCALATE:
            lines.append("- ⚠️ 应已升级到 @灵通+")
        elif hours_ago >= SLA_HOURS_SOFT:
            lines.append("- 🟡 软提醒阶段")
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", type=int, default=0, help="N 秒循环")
    parser.add_argument("--report", action="store_true", help="输出当前 SLA 状态")
    args = parser.parse_args()

    if args.report:
        print(report(load_state()))
        return

    while True:
        now = datetime.now(timezone.utc)
        state = load_state()
        no_ack = detect_acks()
        for member_id in no_ack:
            # 获取 wake 时间
            with open(WAKE_STATE_FILE) as f:
                wake_state = json.load(f)
            wake_iso = wake_state["last_wake"].get(member_id, now.isoformat())
            wake_dt = datetime.fromisoformat(wake_iso)
            hours_ago = (now - wake_dt).total_seconds() / 3600
            task = wake_state.get("wake_count", {}).get(member_id, "")
            if member_id not in state["tracked"]:
                state["tracked"][member_id] = {
                    "wake_time": wake_iso,
                    "task": task,
                    "level": "soft",
                    "escalated_at": [],
                }
            tracked = state["tracked"][member_id]
            if hours_ago >= SLA_HOURS_CRITICAL and "critical" not in tracked.get("escalated_at", []):
                escalate_lingbus(member_id, "critical", hours_ago)
                tracked["escalated_at"].append("critical")
                tracked["level"] = "critical"
            elif hours_ago >= SLA_HOURS_ESCALATE and "escalate" not in tracked.get("escalated_at", []):
                escalate_lingbus(member_id, "escalate", hours_ago)
                tracked["escalated_at"].append("escalate")
                tracked["level"] = "escalate"
            elif hours_ago >= SLA_HOURS_SOFT and "soft" not in tracked.get("escalated_at", []):
                escalate_lingbus(member_id, "soft", hours_ago)
                tracked["escalated_at"].append("soft")
                tracked["level"] = "soft"
        save_state(state)
        if not args.watch:
            break
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
