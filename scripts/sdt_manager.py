#!/usr/bin/env python3
"""
Self-driven task manager for lingclaude.
Usage:
    python3 sdt_manager.py list                  # 列出所有已注册自驱任务
    python3 sdt_manager.py show <task_id>        # 查看任务详情
    python3 sdt_manager.py record <task_id> <result>  # 记录执行结果
    python3 sdt_manager.py due                   # 列出当前应执行的任务
    python3 sdt_manager.py validate              # 校验注册表完整性
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

TASKS_FILE = Path(__file__).parent.parent / ".lingclaude" / "self_driven_tasks.json"

def load_tasks():
    if not TASKS_FILE.exists():
        print(f"ERROR: {TASKS_FILE} not found")
        sys.exit(1)
    return json.loads(TASKS_FILE.read_text())

def save_tasks(data):
    data["updated_at"] = datetime.utcnow().isoformat() + "Z"
    TASKS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))

def cmd_list():
    data = load_tasks()
    if not data["tasks"]:
        print("No registered self-driven tasks.")
        return
    print(f"{'ID':<16} {'Name':<20} {'Category':<22} {'Freq':<8} {'Status':<12} {'Runs':<5} {'Last'}")
    print("-" * 100)
    for t in data["tasks"]:
        rt = t.get("runtime", {})
        last = rt.get("last_run", "-")
        if last != "-":
            last = last[:16]
        print(f"{t['task_id']:<16} {t['name']:<20} {t['category']:<22} {t['frequency']:<8} {rt.get('status', '?'):<12} {rt.get('total_runs', 0):<5} {last}")

def cmd_show(task_id):
    data = load_tasks()
    for t in data["tasks"]:
        if t["task_id"] == task_id:
            print(json.dumps(t, indent=2, ensure_ascii=False))
            return
    print(f"Task {task_id} not found")

def cmd_record(task_id, result):
    data = load_tasks()
    for t in data["tasks"]:
        if t["task_id"] == task_id:
            rt = t.setdefault("runtime", {})
            rt["last_run"] = datetime.utcnow().isoformat() + "Z"
            rt["last_result"] = result
            rt["total_runs"] = rt.get("total_runs", 0) + 1
            if result == "success":
                rt["consecutive_runs"] = rt.get("consecutive_runs", 0) + 1
            else:
                rt["consecutive_runs"] = 0
            gov = t.get("governance", {})
            max_c = gov.get("max_consecutive", 7)
            if rt["consecutive_runs"] >= max_c:
                rt["status"] = "paused_review"
                print(f"WARNING: {task_id} hit max_consecutive={max_c}, auto-paused for review")
            save_tasks(data)
            print(f"Recorded {result} for {task_id} (total: {rt['total_runs']}, consecutive: {rt['consecutive_runs']})")
            return
    print(f"Task {task_id} not found")

def cmd_due():
    data = load_tasks()
    now = datetime.utcnow()
    freq_map = {"daily": 1, "6h": 0.25, "weekly": 7, "idle": 999}
    for t in data["tasks"]:
        rt = t.get("runtime", {})
        if not rt.get("enabled", True):
            continue
        if rt.get("status") != "active":
            continue
        freq = t.get("frequency", "daily")
        interval_days = freq_map.get(freq, 1)
        last_run = rt.get("last_run")
        if not last_run:
            print(f"DUE  {t['task_id']:<16} {t['name']:<20} (never run)")
            continue
        last_dt = datetime.fromisoformat(last_run.replace("Z", "+00:00")).replace(tzinfo=None)
        elapsed = (now - last_dt).total_seconds() / 86400
        if elapsed >= interval_days:
            hours_ago = elapsed * 24
            print(f"DUE  {t['task_id']:<16} {t['name']:<20} (last: {hours_ago:.1f}h ago, freq: {freq})")

def cmd_validate():
    data = load_tasks()
    required_fields = ["task_id", "name", "description", "category", "frequency", "prompt", "boundary", "completion_criteria", "governance"]
    required_boundary = ["no_publish", "no_deploy"]
    required_gov = ["approved_by", "approved_at"]
    errors = []
    for t in data["tasks"]:
        tid = t.get("task_id", "UNKNOWN")
        for f in required_fields:
            if f not in t:
                errors.append(f"{tid}: missing field '{f}'")
        b = t.get("boundary", {})
        for f in required_boundary:
            if f not in b:
                errors.append(f"{tid}: boundary missing '{f}'")
        g = t.get("governance", {})
        for f in required_gov:
            if f not in g:
                errors.append(f"{tid}: governance missing '{f}'")
        if not t.get("task_id", "").startswith("SDT-"):
            errors.append(f"{tid}: task_id must match SDT-xxx-NNN")
    if errors:
        print(f"VALIDATION FAILED ({len(errors)} errors):")
        for e in errors:
            print(f"  - {e}")
    else:
        print(f"OK: {len(data['tasks'])} tasks validated")

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd == "list":
        cmd_list()
    elif cmd == "show" and len(sys.argv) >= 3:
        cmd_show(sys.argv[2])
    elif cmd == "record" and len(sys.argv) >= 4:
        cmd_record(sys.argv[2], sys.argv[3])
    elif cmd == "due":
        cmd_due()
    elif cmd == "validate":
        cmd_validate()
    else:
        print(__doc__)

if __name__ == "__main__":
    main()
