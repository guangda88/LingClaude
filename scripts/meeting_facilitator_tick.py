#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
灵克讨论小组主持守护 — 每 5 min 自动化 v3
- 用 LingBus HTTP MCP API (端口 9528) 替代本地 sqlite (thread 不落本地 db)
- 拉每个成员 pending, 按 thread_id 聚合, 计算每成员在某 thread 的最后活跃时间
- 扫自己发起的 thread 内新 post_reply

usage: python3 meeting_facilitator_tick.py
"""

import json
import os
import urllib.request
from datetime import datetime, timezone

LINGBUS_URL = "http://127.0.0.1:9528/mcp"
STATE_FILE = "/tmp/meeting_state.json"
LOG_FILE = "/tmp/meeting_facilitator.jsonl"

ME = "lingclaude"
ORIGINAL_THREAD = "190a4d71c6594286ab70efba9b2d7d74"
SUPPLEMENT_THREAD = "f9885d2db8ed49c480469a5af4a68082"
MONITORED_THREADS = [ORIGINAL_THREAD, SUPPLEMENT_THREAD]
RECIPIENTS = [
    "lingminopt", "lingxi", "lingmessage", "lingzhi", "lingan",
    "lingflow", "lingresearch", "atomcode"
]
REMIND_AFTER = 300    # 5 min
ESCALATE_AFTER = 900  # 15 min


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def now_dt():
    return datetime.now(timezone.utc)


def call_lingbus(method, params=None, req_id=1):
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    try:
        req = urllib.request.Request(LINGBUS_URL, data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode(errors="replace")
            for line in raw.splitlines():
                if line.startswith("data: "):
                    return json.loads(line[6:]).get("result")
            return None
    except Exception as e:
        return {"_error": str(e)}


def parse_ts(ts_str):
    if not ts_str:
        return None
    try:
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        return datetime.fromisoformat(ts_str)
    except Exception:
        return None


def get_pending_for(member, limit=200):
    res = call_lingbus("tools/call", {
        "name": "get_pending_messages",
        "arguments": {"member": member, "limit": limit}
    })
    if isinstance(res, dict) and "content" in res:
        try:
            text = res["content"][0]["text"]
            return json.loads(text)
        except Exception:
            return []
    return []


def ack(message_id):
    return call_lingbus("tools/call", {
        "name": "ack_message",
        "arguments": {"message_id": message_id, "member": ME}
    })


def post_reply(thread_id, recipient, subject, body):
    return call_lingbus("tools/call", {
        "name": "post_reply",
        "arguments": {
            "thread_id": thread_id,
            "recipient": recipient,
            "sender": ME,
            "subject": subject,
            "body": body
        }
    })


def load_state():
    default = {
        "last_tick": None,
        "ticks": 0,
        "member_last_active": {r: None for r in RECIPIENTS},
        "thread_last_msg_ts": {t: None for t in MONITORED_THREADS},
        "thread_msg_count": {t: 0 for t in MONITORED_THREADS},
        "acked_msg_ids": [],
        "new_replies_pending": [],
        "reminded_recipients": [],
        "summary": None,
        "_version": 3
    }
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                loaded = json.load(f)
            if loaded.get("_version") != 3:
                return default
            return loaded
        except Exception:
            pass
    return default


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def log_event(event):
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps({"ts": now_iso(), **event}) + "\n")


def main():
    state = load_state()
    state["ticks"] += 1
    state["last_tick"] = now_iso()
    ndt = now_dt()
    acked = set(state.get("acked_msg_ids", []))

    # === 主数据源修正（2026-08-18 事故教训）===
    # LingBus 语义: pending(member=X) = X 的收件箱（不是发件箱）
    # 成员的 post_reply 落在 主持人(lingclaude) 的 pending 里
    # 因此"谁发言了"必须查主持人自己的 pending, 过滤 sender != ME
    my_msgs = get_pending_for(ME, limit=200)

    # 1. 从主持人收件箱提取成员发言（正确语义）
    member_last_active = {r: None for r in RECIPIENTS}
    thread_msgs = {t: [] for t in MONITORED_THREADS}
    for m in my_msgs:
        sender = m.get("sender")
        tid = m.get("thread_id")
        t = parse_ts(m.get("queued_at") or m.get("timestamp", ""))
        if sender in member_last_active and t:
            if member_last_active[sender] is None or t > member_last_active[sender]:
                member_last_active[sender] = t
        if tid in thread_msgs and t:
            thread_msgs[tid].append({
                "sender": sender,
                "recipient": m.get("recipient"),
                "ts": t.isoformat(),
                "message_id": m.get("message_id"),
                "subject": m.get("subject", "")
            })

    # 2. 各成员 pending 仅用于"已读状态"统计（语义正确用途）
    pending_summary = {}
    for r in RECIPIENTS:
        msgs = get_pending_for(r, limit=50)
        pending_summary[r] = {
            "unread_inbox": len(msgs),
            "unread_dsh": sum(1 for m in msgs if m.get("thread_id") in MONITORED_THREADS)
        }

    # 2. 计算 thread 消息数（这里简化为: 当前 pending 中收集的 + 已 ack 的）
    # 注: pending 只包含未 ack 的, 所以 msg_count = 当前数量 + 历史 ack 数
    # 为简化, 我们仅展示 pending 中可见的活跃消息
    thread_info = {}
    new_replies_pending = []
    for tid in MONITORED_THREADS:
        msgs = thread_msgs[tid]
        # 找最新消息时间
        latest_ts = max((m["ts"] for m in msgs), default=None)
        # 找新 post_reply (sender != ME, ts > 上次扫描时间)
        last_scan = state["thread_last_msg_ts"].get(tid)
        last_scan_dt = parse_ts(last_scan) if last_scan else None
        new_replies = []
        for m in msgs:
            if m["sender"] == ME:
                continue
            t = parse_ts(m["ts"])
            if last_scan_dt is None or t > last_scan_dt:
                new_replies.append(m)
                if m["message_id"] not in acked:
                    new_replies_pending.append({"thread_id": tid, **m})
        thread_info[tid] = {
            "pending_msg_count": len(msgs),
            "latest_msg_ts": latest_ts,
            "new_replies_since_last_scan": len(new_replies)
        }
        if latest_ts:
            state["thread_last_msg_ts"][tid] = latest_ts

    # 3. 汇总
    member_silent = {}
    for r in RECIPIENTS:
        last = member_last_active[r]
        silent = (ndt - last).total_seconds() if last else None
        member_silent[r] = {"last_active_ts": last.isoformat() if last else None, "silent_seconds": int(silent) if silent is not None else None}

    online = sum(1 for m in member_silent.values() if m["silent_seconds"] is not None and m["silent_seconds"] < REMIND_AFTER)
    silent_5 = sum(1 for m in member_silent.values() if m["silent_seconds"] is not None and REMIND_AFTER <= m["silent_seconds"] < ESCALATE_AFTER)
    silent_15 = sum(1 for m in member_silent.values() if m["silent_seconds"] is not None and m["silent_seconds"] >= ESCALATE_AFTER)
    never_active = sum(1 for m in member_silent.values() if m["silent_seconds"] is None)

    summary = {
        "online_count": online,
        "silent_5min": silent_5,
        "silent_15min": silent_15,
        "never_active": never_active,
        "new_replies_pending": len(new_replies_pending),
        "thread_info": thread_info,
        "pending_summary": pending_summary
    }

    state["member_last_active"] = member_silent
    state["new_replies_pending"] = new_replies_pending
    state["summary"] = summary
    save_state(state)

    log_event({"event": "tick", "tick": state["ticks"], "summary": summary})

    print(json.dumps({
        "tick": state["ticks"],
        "ts": now_iso(),
        "summary": summary,
        "members": member_silent,
        "thread_info": thread_info,
        "new_replies_pending": new_replies_pending
    }, indent=2, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()