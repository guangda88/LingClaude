#!/usr/bin/env python3
"""
lingos_tier1.py — lingOS Tier 1: Self-heal 自我修复

设计原则 (灵元 1.0):
  - Tier 0 observe (现有 probe.py): 不变
  - Tier 1 self-heal (本脚本): 只动自己 (probe 状态 + snapshots 清理 + alert_state)
  - Tier 2 orchestrate: lingos_tier2.py
  - Tier 3 safe-start: 需治理决议 (会议 #2 议程 4)
  - Tier 4 danger: 永不实现

设计哲学 (本脚本):
  - NEVER kill 其他进程 (本脚本唯一允许的 "动" 是 process exit/re-spawn 自己的 probe 子进程)
  - NEVER modify 其他文件 (只动 snapshots.jsonl, alert_state.json, reminders.json)
  - NEVER touch 配置 (config.yaml 由人类修改)

用法:
  python3 scripts/lingos_tier1.py             # 扫一次
  python3 scripts/lingos_tier1.py --watch 600 # 每 10min 循环
  python3 scripts/lingos_tier1.py --dry-run   # 只查不做

部署目标: /home/ai/lingos/lingos_tier1.py (需 WSB 授权)
  systemd timer: lingos-tier1.timer (5min, 与 probe 错开 2.5min)
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
from typing import Any, Dict, Optional

LINGOS_HOME = Path("/home/ai/lingos")
SNAPSHOT_PATH = LINGOS_HOME / "snapshots.jsonl"
ALERT_STATE_PATH = LINGOS_HOME / "alert_state.json"
REMINDERS_PATH = LINGOS_HOME / "reminders.json"
PROBE_SCRIPT = LINGOS_HOME / "probe.py"
PROBE_TIMER = "lingos-probe.timer"  # systemd 单元名

# 自我修复阈值
SNAPSHOT_STALE_MIN = 15      # 快照 > 15 min 没更新 = probe 可能挂
SNAPSHOT_MAX_MB = 100        # snapshots.jsonl > 100 MB = 截断
ALERT_STATE_MAX_KB = 500     # alert_state.json > 500 KB = 备份 + 重置
PROBE_GRACE_SEC = 5          # 拉起 probe 前等 5s


def check_probe_fresh() -> Dict[str, Any]:
    """检查 probe 是否还在正常写 snapshot"""
    if not SNAPSHOT_PATH.exists():
        return {"alive": False, "reason": "snapshot missing"}
    try:
        with open(SNAPSHOT_PATH) as f:
            lines = [l for l in f.readlines() if l.strip()][-5:]
        if not lines:
            return {"alive": False, "reason": "snapshot empty"}
        last_ts = json.loads(lines[-1]).get("ts", "")
        if not last_ts:
            return {"alive": False, "reason": "no ts"}
        last_dt = datetime.strptime(last_ts, "%Y-%m-%dT%H:%M:%S")
        # probe.py 用 time.strftime 不带 tz, 是本地时间 (CST/UTC+8)
        # 假设服务器时区是 CST, 转换为 UTC 比较
        from datetime import timedelta
        CST_OFFSET = timedelta(hours=8)
        last_dt_utc = (last_dt - CST_OFFSET).replace(tzinfo=timezone.utc)
        age_min = (datetime.now(timezone.utc) - last_dt_utc).total_seconds() / 60
        return {
            "alive": age_min < SNAPSHOT_STALE_MIN,
            "age_min": round(age_min, 1),
            "last_ts": last_ts,
            "reason": "fresh" if age_min < SNAPSHOT_STALE_MIN else f"stale {age_min:.1f}min",
        }
    except Exception as e:
        return {"alive": False, "reason": f"parse error: {e}"}


def check_snapshot_size() -> Dict[str, Any]:
    if not SNAPSHOT_PATH.exists():
        return {"size_mb": 0, "needs_truncate": False}
    size_mb = SNAPSHOT_PATH.stat().st_size / (1024 * 1024)
    return {
        "size_mb": round(size_mb, 2),
        "needs_truncate": size_mb > SNAPSHOT_MAX_MB,
    }


def check_alert_state_size() -> Dict[str, Any]:
    if not ALERT_STATE_PATH.exists():
        return {"size_kb": 0, "needs_rotate": False}
    size_kb = ALERT_STATE_PATH.stat().st_size / 1024
    return {
        "size_kb": round(size_kb, 2),
        "needs_rotate": size_kb > ALERT_STATE_MAX_KB,
    }


def restart_probe(dry_run: bool) -> str:
    """拉起 probe
    用 systemd restart (若可用), 否则 spawn 子进程
    返回 status 字符串
    """
    if dry_run:
        return "DRY-RUN: would restart probe"
    # 优先 systemd
    try:
        result = subprocess.run(
            ["systemctl", "restart", PROBE_TIMER.replace(".timer", ".service")],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return f"systemd restart OK: {result.stdout[:50]}"
    except Exception as e:
        pass
    # 降级: 手动 spawn (用 nohup + setsid)
    try:
        proc = subprocess.Popen(
            ["nohup", "python3", str(PROBE_SCRIPT)],
            cwd=str(LINGOS_HOME),
            stdout=open(LINGOS_HOME / "tier1_spawn.log", "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        time.sleep(PROBE_GRACE_SEC)
        return f"spawned probe pid={proc.pid}"
    except Exception as e:
        return f"restart failed: {e}"


def truncate_snapshots(dry_run: bool) -> str:
    """截断 snapshots.jsonl, 保留最近 50%"""
    if dry_run:
        return "DRY-RUN: would truncate snapshots"
    try:
        with open(SNAPSHOT_PATH) as f:
            lines = f.readlines()
        keep = lines[len(lines) // 2:]
        backup = SNAPSHOT_PATH.with_suffix(f".jsonl.bak.{int(time.time())}")
        SNAPSHOT_PATH.rename(backup)
        with open(SNAPSHOT_PATH, "w") as f:
            f.writelines(keep)
        return f"truncated: {len(lines)}→{len(keep)} lines, backup={backup.name}"
    except Exception as e:
        return f"truncate failed: {e}"


def rotate_alert_state(dry_run: bool) -> str:
    """备份 + 重置 alert_state.json"""
    if dry_run:
        return "DRY-RUN: would rotate alert_state"
    try:
        if ALERT_STATE_PATH.exists():
            backup = ALERT_STATE_PATH.with_suffix(f".json.bak.{int(time.time())}")
            ALERT_STATE_PATH.rename(backup)
        with open(ALERT_STATE_PATH, "w") as f:
            json.dump({"active_keys": [], "ts": datetime.now(timezone.utc).isoformat()}, f)
        return f"rotated, backup={backup.name}"
    except Exception as e:
        return f"rotate failed: {e}"


def send_lingbus_log(actions: list) -> str:
    """记录动作到 LingBus (via lingmessage 文件系统)"""
    # 占位: 实际应调 mcp__ling-term-mcp__open_thread
    log = LINGOS_HOME / "tier1_actions.jsonl"
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "actions": actions,
    }
    with open(log, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return f"logged to {log.name}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", type=int, default=0, help="N 秒循环")
    parser.add_argument("--dry-run", action="store_true", help="只查不做")
    args = parser.parse_args()

    while True:
        actions = []
        # 1. probe 健康
        probe = check_probe_fresh()
        if not probe["alive"]:
            result = restart_probe(args.dry_run)
            actions.append(f"probe_restart: {result} (reason: {probe.get('reason')})")
        # 2. snapshots 大小
        snap = check_snapshot_size()
        if snap["needs_truncate"]:
            result = truncate_snapshots(args.dry_run)
            actions.append(f"truncate_snapshots: {result} ({snap['size_mb']}MB > {SNAPSHOT_MAX_MB}MB)")
        # 3. alert_state 大小
        alst = check_alert_state_size()
        if alst["needs_rotate"]:
            result = rotate_alert_state(args.dry_run)
            actions.append(f"rotate_alert_state: {result} ({alst['size_kb']}KB > {ALERT_STATE_MAX_KB}KB)")
        # 4. 报告
        if actions:
            send_lingbus_log(actions)
            print(f"[tier1] {len(actions)} actions taken:")
            for a in actions:
                print(f"  - {a}")
        else:
            print(f"[tier1] {datetime.now(timezone.utc).isoformat()} all healthy "
                  f"(probe_age={probe.get('age_min','?')}min, snap={snap['size_mb']}MB, "
                  f"alert_state={alst['size_kb']}KB)")
        if not args.watch:
            break
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
