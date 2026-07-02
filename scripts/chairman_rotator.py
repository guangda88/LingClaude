#!/usr/bin/env python3
"""
chairman_rotator.py — 主持人轮值提醒

设计原则 (灵元 1.0):
  - 灵克 owner 范围
  - 灵族方向例会 #N 主持人轮值表
  - 周一 9:00 提醒本周主持
  - 9 天前再次提醒 + 检查 agenda 是否起草

轮值表 (v0.1, 6/27 会议 #1 后确立):
  #1 灵克 (lingclaude)  ✅ done
  #2 灵通 (lingflow)    ⏳ T-9 (7/2)
  #3 灵研 (lingresearch)
  #4 灵知 (lingzhi)
  #5 灵犀 (lingxi)
  #6 灵信 (lingmessage)
  #7 灵极优 (lingminopt)
  #8 灵创 (lingcreate)
  #9 灵扬 (lingyang)
  #10 灵网 (lingweb)
  #11 智桥 (zhibridge)
  #12 灵通+ (lingflow_plus)
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO = Path("/home/ai/lingclaude")
ROTATION_FILE = REPO / "scripts" / ".chairman_rotation.json"

ROTATION = [
    ("#1",  "灵克 (lingclaude)",   "2026-06-27", "done"),
    ("#2",  "灵通 (lingflow)",     "2026-07-11", "pending"),
    ("#3",  "灵研 (lingresearch)", "2026-07-25", "future"),
    ("#4",  "灵知 (lingzhi)",      "2026-08-08", "future"),
    ("#5",  "灵犀 (lingxi)",       "2026-08-22", "future"),
    ("#6",  "灵信 (lingmessage)",  "2026-09-05", "future"),
    ("#7",  "灵极优 (lingminopt)", "2026-09-19", "future"),
    ("#8",  "灵创 (lingcreate)",   "2026-10-03", "future"),
    ("#9",  "灵扬 (lingyang)",     "2026-10-17", "future"),
    ("#10", "灵网 (lingweb)",      "2026-10-31", "future"),
    ("#11", "智桥 (zhibridge)",    "2026-11-14", "future"),
    ("#12", "灵通+ (lingflow_plus)","2026-11-28", "future"),
]


def get_pending_chairman(now: datetime) -> tuple:
    """返回 T-9 内最近一个 pending 主持"""
    for tag, name, date_str, status in ROTATION:
        if status != "pending":
            continue
        d = datetime.fromisoformat(date_str)
        days = (d - now).total_seconds() / 86400
        if -1 <= days <= 9:
            return (tag, name, date_str, days)
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    pending = get_pending_chairman(now)
    if not pending:
        print("无 pending 主持 (T-9 窗口内)")
        return
    tag, name, date_str, days = pending
    msg = f"""【轮值提醒】灵族方向例会 {tag} · {name}

会议日期: {date_str} (T-{days:.1f} 天)

**请准备**:
1. 阅读上次会议纪要 (docs/lacp/MEETING_MINUTES_*.md)
2. 起草本会议 agenda (docs/lacp/MEETING_AGENDA_{date_str.replace("-","")}.md)
3. 应用 MEETING_PROTOCOL v1.1 (R1 散会/R2 throttle/R3 议题 4 改革/R4 15min/R5 二分法/R6 收敛)
4. 议题 4: 会前 24h @ 全体提交架构进展
5. 主持工具: meeting_inviter.py / meeting_idle_handler.py

**灵克辅助**:
- AI-01 trace actor 速查表已交付
- MEETING_PROTOCOL v1.1 已发布
- 17 AI 状态对账已就绪 (handover v22.3)

—— chairman_rotator.py
"""
    print(msg)


if __name__ == "__main__":
    main()
