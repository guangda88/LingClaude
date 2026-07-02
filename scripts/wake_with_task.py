#!/usr/bin/env python3
"""
wake_with_task.py — Layer 0: 带任务的唤醒协议

设计原则 (灵元 1.0):
  - 灵克 owner 范围, 不读其他灵目录
  - 通过 LingBus MCP (lingmessage) 发唤醒消息
  - 唤醒消息必带具体任务（不是"请自检"）
  - 任务从本地 ai_task_queue.json 读取

用法:
  python3 scripts/wake_with_task.py              # 扫一次
  python3 scripts/wake_with_task.py --watch 3600  # 每 1h 循环
  python3 scripts/wake_with_task.py --dry-run    # 只查不发

任务格式 (ai_task_queue.json):
  {
    "lingflow": "请 ack AI-02 proxy21 3 插片 manifest 进度",
    "lingzhi": "请 ack AI-11 lingzhi-rag-search 是否在 6/30 deadline 前完成",
    ...
  }
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
from typing import Any, Dict, List, Optional

REPO = Path("/home/ai/lingclaude")
TASK_QUEUE = REPO / "scripts" / "ai_task_queue.json"
STATE_FILE = REPO / "scripts" / ".wake_with_task_state.json"
IDLE_THRESHOLD_HOURS = 4.0  # 灵 idle >4h 触发唤醒


def load_task_queue() -> Dict[str, str]:
    if not TASK_QUEUE.exists():
        return {}
    with open(TASK_QUEUE) as f:
        return json.load(f)


def load_state() -> Dict[str, Any]:
    if not STATE_FILE.exists():
        return {"last_wake": {}, "wake_count": {}}
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state: Dict[str, Any]) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_member_idle_hours(member_id: str) -> Optional[float]:
    """通过 ps + pty_keeper 检测某灵最后活跃时间
    返回 idle 小时数；None 表示无法判断
    """
    pty_pattern = f"pty_keeper.*\\b{member_id}\\b"
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid,etimes,cmd"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        for line in out.splitlines()[1:]:
            if pty_pattern in line:
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    return int(parts[1]) / 3600.0
        return None
    except Exception:
        return None


def send_lingbus_wake(member_id: str, member_name: str, task: str) -> str:
    """通过 LingBus 发唤醒消息（带具体任务）
    返回 message_id
    """
    # 通过 subprocess 调用 mcp__ling-term-mcp__open_thread 不可行（不是 CLI 工具）
    # 改用直接写 lingmessage 文件系统（如果可达）
    # 实际：脚本只生成"待发送"消息，主流程通过外部 cron 触发 mcp 工具
    # 这里返回"queued"字符串
    body = f"""【灵克 启动带任务唤醒协议 · {datetime.now(timezone.utc).isoformat()}】

致 {member_name} ({member_id})：

您已 idle {IDLE_THRESHOLD_HOURS}+ 小时。根据 SESSION_LIFECYCLE_PROTOCOL v2.0，触发自动唤醒。

**本次携带任务**：
{task}

**期望响应**：
- 24h 内 ack 本消息
- 48h 内完成/推进任务
- 阻塞：明确说明（需要谁/需要什么授权）

**如不响应**：
- 48h 后 sla_tracker.py 升级到 @灵通+
- 72h 后 @用户 (OPC)

—— 灵克 (lingclaude) · 自动唤醒协议
"""
    # TODO: 实际调用 mcp__ling-term-mcp__open_thread
    # 这里仅记录日志
    msg_id = f"queued_{member_id}_{int(time.time())}"
    print(f"[wake_with_task] {msg_id} → {member_name}")
    print(f"  task: {task[:80]}")
    return msg_id


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", type=int, default=0, help="N 秒循环")
    parser.add_argument("--dry-run", action="store_true", help="只查不发")
    args = parser.parse_args()

    while True:
        task_queue = load_task_queue()
        state = load_state()
        now_iso = datetime.now(timezone.utc).isoformat()
        woke = 0
        for member_id, task in task_queue.items():
            idle_h = get_member_idle_hours(member_id)
            if idle_h is None:
                print(f"[{now_iso}] {member_id}: 无法判断 idle (无 pty_keeper)")
                continue
            if idle_h < IDLE_THRESHOLD_HOURS:
                print(f"[{now_iso}] {member_id}: idle {idle_h:.1f}h (<{IDLE_THRESHOLD_HOURS}h, 跳过)")
                continue
            # 已 wake 过但 <6h 内, 跳过
            last_wake = state["last_wake"].get(member_id, "")
            if last_wake:
                try:
                    last_dt = datetime.fromisoformat(last_wake)
                    if (datetime.now(timezone.utc) - last_dt).total_seconds() < 6 * 3600:
                        print(f"[{now_iso}] {member_id}: 6h 内已唤醒, 跳过")
                        continue
                except Exception:
                    pass
            member_name = MEMBER_NAMES.get(member_id, member_id)
            print(f"[{now_iso}] {member_id} ({member_name}): idle {idle_h:.1f}h → 唤醒")
            if not args.dry_run:
                send_lingbus_wake(member_id, member_name, task)
                state["last_wake"][member_id] = now_iso
                state["wake_count"][member_id] = state["wake_count"].get(member_id, 0) + 1
                woke += 1
        if state != load_state():  # 状态有变才写
            save_state(state)
        print(f"[{now_iso}] 唤醒 {woke} 位灵")
        if not args.watch:
            break
        time.sleep(args.watch)


MEMBER_NAMES = {
    "lingflow":     "灵通",
    "lingclaude":   "灵克",
    "lingresearch": "灵研",
    "lingzhi":      "灵知",
    "lingtongask":  "灵通问道",
    "lingflow_plus":"灵通+",
    "lingyang":     "灵扬",
    "lingweb":      "灵网",
    "lingcreate":   "灵创",
    "lingmessage":  "灵信",
    "lingxi":       "灵犀",
    "lingminopt":   "灵极优",
    "zhibridge":    "智桥",
}


if __name__ == "__main__":
    main()
