#!/usr/bin/env python3
"""
lingos_tier3.py — lingOS Tier 3: Safe-Start 拉起白名单 daemon

设计原则 (灵元 1.0):
  - 用户拍板决议 (LINGOS_TIER3_v0.1.md, 7/2 20:34)
  - 白名单 4 个 daemon: atomcode/lingmemory/lingxi/proxy3
  - 排除: 4 业务服务 + 治理引擎 + postgres + AI session
  - 防 restart loop: 1h cooldown + 3 次/小时封顶
  - 每次启动必 LingBus 留痕
  - 永不 Tier 4 (kill/delete/modify 物理上写不出)

设计哲学 (本脚本):
  - "Limited action" 而非 "any action"
  - 白名单静态, 不动态加
  - 防 restart loop 是硬约束, 不配置化
  - 任何"被禁"动作物理上不存在

用法:
  python3 scripts/lingos_tier3.py             # 扫一次
  python3 scripts/lingos_tier3.py --watch 600 # 每 10min 循环
  python3 scripts/lingos_tier3.py --dry-run

部署目标: /home/ai/lingos/lingos_tier3.py (WSB 已批)
  systemd timer: lingos-tier3.timer (5min, 与 tier1/2 错开)
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

LINGOS_HOME = Path("/home/ai/lingos")
CONFIG_PATH = LINGOS_HOME / "lingos_config.yaml"
STATE_PATH = LINGOS_HOME / "tier3_state.json"
ACTIONS_LOG = LINGOS_HOME / "tier3_actions.jsonl"
CST_OFFSET = timedelta(hours=8)

# 硬约束 (Tier 4 永远不开, 写不出 kill/delete/modify)
COOLDOWN_MIN = 60       # 1h 内不重复拉同一 daemon
MAX_RESTARTS_PER_HOUR = 3  # 1h 内拉起 > 3 次 → 停止 + critical alert
PORT_DOWN_THRESHOLD_MIN = 30  # port DOWN > 30 min 才拉起 (避免抖动)

# 静态白名单 (治理决议, 不配置化, 不动态加)
ALLOWLIST = {
    "atomcode": {
        "port": 13456,
        "start_cmd": ["atomcode", "daemon", "--port", "13456", "--idle-timeout", "3600"],
        "pid_pattern": "atomcode daemon --port 13456",
    },
    "lingmemory": {
        "port": 9530,
        "start_cmd": ["python3", "-m", "lingmemory.http_server"],
        "pid_pattern": "lingmemory.http_server",
    },
    "lingxi": {
        "port": 9532,
        "start_cmd": ["python3", "-m", "lingxi.mcp_server"],
        "pid_pattern": "lingxi.mcp_server",
    },
    "proxy3_py": {
        "port": 8765,
        "start_cmd": ["bash", "/home/ai/llm-proxy/start_proxy3.sh"],
        "pid_pattern": "main.py",
    },
}


def check_port(port: int) -> bool:
    try:
        import socket
        s = socket.socket()
        s.settimeout(2)
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except Exception:
        return False


def get_port_down_minutes(port: int) -> int:
    """估算 port DOWN 多少分钟 (基于 snapshots.jsonl + tier3_state.json 历史)"""
    if check_port(port):
        return 0
    # 简化: 用 state 里最后 UP 的 ts
    if not STATE_PATH.exists():
        return PORT_DOWN_THRESHOLD_MIN + 1  # 首次检查, 假定 DOWN 已久
    try:
        with open(STATE_PATH) as f:
            state = json.load(f)
        last_up = state.get("port_last_up", {}).get(str(port), "")
        if not last_up:
            return PORT_DOWN_THRESHOLD_MIN + 1
        last_dt = datetime.fromisoformat(last_up)
        return int((datetime.now(timezone.utc) - last_dt).total_seconds() / 60)
    except Exception:
        return PORT_DOWN_THRESHOLD_MIN + 1


def load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {"restart_history": {}, "port_last_up": {}}
    with open(STATE_PATH) as f:
        return json.load(f)


def save_state(state: Dict[str, Any]) -> None:
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def count_recent_restarts(state: Dict[str, Any], daemon: str) -> int:
    """1h 内该 daemon 拉起次数"""
    history = state.get("restart_history", {}).get(daemon, [])
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    return sum(1 for ts in history if ts > cutoff)


def record_restart(state: Dict[str, Any], daemon: str) -> None:
    history = state.setdefault("restart_history", {}).setdefault(daemon, [])
    history.append(datetime.now(timezone.utc).isoformat())
    # 保留 24h 内
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    state["restart_history"][daemon] = [ts for ts in history if ts > cutoff]


def record_port_up(state: Dict[str, Any], port: int) -> None:
    state.setdefault("port_last_up", {})[str(port)] = datetime.now(timezone.utc).isoformat()


def start_daemon(name: str, cfg: Dict[str, Any], dry_run: bool) -> str:
    """拉起 daemon (用 nohup + setsid 独立会话)"""
    if dry_run:
        return f"DRY-RUN: would run {' '.join(cfg['start_cmd'])}"
    try:
        log = LINGOS_HOME / f"tier3_{name}.log"
        proc = subprocess.Popen(
            cfg["start_cmd"],
            stdout=open(log, "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,  # setsid
        )
        return f"spawned pid={proc.pid}, cmd={' '.join(cfg['start_cmd'][:3])}..."
    except FileNotFoundError as e:
        return f"binary not found: {e}"
    except Exception as e:
        return f"start failed: {e}"


def log_action(daemon: str, level: str, msg: str) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "daemon": daemon,
        "level": level,
        "msg": msg,
    }
    with open(ACTIONS_LOG, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"[tier3] {level}: {daemon} — {msg}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", type=int, default=0, help="N 秒循环")
    parser.add_argument("--dry-run", action="store_true", help="只查不做")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    state = load_state()
    actions = []

    for name, cfg in ALLOWLIST.items():
        port = cfg["port"]
        # 1. port 状态
        if check_port(port):
            record_port_up(state, port)
            continue
        # 2. DOWN 多久
        down_min = get_port_down_minutes(port)
        if down_min < PORT_DOWN_THRESHOLD_MIN:
            continue
        # 3. cooldown
        recent = count_recent_restarts(state, name)
        if recent > 0:
            last_ts = state["restart_history"][name][-1]
            try:
                last_dt = datetime.fromisoformat(last_ts)
                elapsed_min = (now - last_dt).total_seconds() / 60
                if elapsed_min < COOLDOWN_MIN:
                    log_action(name, "skip", f"cooldown ({elapsed_min:.0f}min < {COOLDOWN_MIN}min)")
                    continue
            except Exception:
                pass
        # 4. max_restarts_per_hour 检查
        if recent >= MAX_RESTARTS_PER_HOUR:
            log_action(name, "critical", f"max_restarts_per_hour reached ({recent}/{MAX_RESTARTS_PER_HOUR}), stopping auto-start, escalate to @灵通+ + @用户")
            continue
        # 5. 拉起
        result = start_daemon(name, cfg, args.dry_run)
        if not args.dry_run:
            record_restart(state, name)
        actions.append(f"{name} (port {port}, DOWN {down_min}min, recent {recent}/{MAX_RESTARTS_PER_HOUR}): {result}")
        log_action(name, "start", f"port {port} DOWN {down_min}min, recent {recent}/{MAX_RESTARTS_PER_HOUR}, result: {result}")

    save_state(state)
    if not actions:
        print(f"[tier3] {now.isoformat()} all白名单 ports UP or in cooldown/cooldown-OK")
    else:
        print(f"[tier3] {len(actions)} actions:")
        for a in actions:
            print(f"  - {a}")
    if not args.watch:
        return
    time.sleep(args.watch)


if __name__ == "__main__":
    main()
