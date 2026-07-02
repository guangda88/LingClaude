#!/usr/bin/env python3
"""
meeting_inviter.py — Layer 0: 会前 24h 自动 @ 全体

设计原则 (灵元 1.0):
  - 灵克 owner 范围
  - 会前 24h 自动召集: 列出议题 + 时间 + 主持人
  - 读 docs/lacp/MEETING_AGENDA_*.md 提取议题
  - 通过 LingBus @ 12 灵

用法:
  python3 scripts/meeting_inviter.py --agenda docs/lacp/MEETING_AGENDA_20260711.md --hours-before 24
"""
from __future__ import annotations
import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional

REPO = Path("/home/ai/lingclaude")

MEMBERS = [
    "lingflow", "lingclaude", "lingresearch", "lingzhi",
    "lingtongask", "lingflow_plus", "lingyang", "lingweb",
    "lingcreate", "lingmessage", "lingxi", "lingminopt", "zhibridge",
]


def extract_agenda(agenda_path: Path) -> dict:
    """从 agenda md 提取: title, time, host, topics"""
    if not agenda_path.exists():
        return {"error": f"agenda not found: {agenda_path}"}
    text = agenda_path.read_text()
    info = {"title": "", "time": "", "host": "", "topics": []}
    for line in text.splitlines()[:30]:
        if line.startswith("# "):
            info["title"] = line[2:].strip()
        m = re.match(r".*时间[:：]\s*(.+)", line)
        if m and not info["time"]:
            info["time"] = m.group(1).strip()
        m = re.match(r".*主持[:：]\s*(.+)", line)
        if m and not info["host"]:
            info["host"] = m.group(1).strip()
    for line in text.splitlines():
        m = re.match(r"^##\s*议程\s*(\d+)[:：]?\s*(.+)", line)
        if m:
            info["topics"].append(f"{m.group(1)}. {m.group(2).strip()[:60]}")
    return info


def build_invite_body(info: dict, hours_before: int) -> str:
    topics_text = "\n".join(f"- {t}" for t in info.get("topics", [])[:10])
    return f"""【会议召集 · T-{hours_before}h】

📅 {info.get('time', '时间待定')}
👤 主持: {info.get('host', '待定')}
📋 {info.get('title', '灵族会议')}

**议程**:
{topics_text}

**期望准备**:
- 议题 4 (架构进展): 会前 24h 提交 ≤200 字 Markdown 到 docs/lacp/
- 其他议题: 1h 内快速浏览
- 缺席: 15min 沉默自动降级 (MEETING_PROTOCOL v1.1 R4)

—— 自动召集脚本 (meeting_inviter.py)
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agenda", required=True, help="议题文档路径")
    parser.add_argument("--hours-before", type=int, default=24)
    args = parser.parse_args()
    info = extract_agenda(Path(args.agenda))
    if "error" in info:
        print(info["error"])
        sys.exit(1)
    body = build_invite_body(info, args.hours_before)
    print(body)
    # TODO: 实际调 mcp__ling-term-mcp__open_thread


if __name__ == "__main__":
    main()
