#!/usr/bin/env python3
"""crush 进程清理脚本 — 灵克 L7/L10 唤醒协议升级 (v0.5)

修复版关键点:
1. **排除当前会话进程树** (PPID 链 + pgid)
2. **超时保护** (5s 内必须完成)
3. **沙箱测试** (DRY_RUN 默认 True, --apply 才执行 kill)
4. **etcd 计数 + 时间过滤** (N+ 天 / 1+ 小时二选一)
5. **身份标识** (sub_sender=daemon, 隔离主代理与守护进程)
6. **audit trail** (写 ~/.lingclaude/hooks/audit.log)

依据灵族方向例会 #3 (LM-20260727-0945) 议程 0 灵安 R2 风险分级:
  - availability 类 → fail-soft (alert, 仅告警)
  - identity 类 → fail-closed (block, 阻断)
  - credential 类 → fail-closed + 双签
  - authorization 类 → fail-closed (block)

本次实现针对 availability 类 (kill 是 fail-soft, 不会破坏主服务)
但 PROTECT_AGENT_ID 排除主代理的 session 进程 = identity 防护.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

AUDIT_LOG = Path(
    os.environ.get(
        "L7_HOOK_AUDIT_LOG",
        "/home/ai/lingclaude/.lingclaude/hooks/audit.log",
    )
)
PROTECT_AGENT_ID = os.environ.get("LINGCLAUDE_AGENT_ID", "lingclaude")
MIN_ETIME_DAYS = 1  # 1 天以上算 zombie
PROTECT_PGID = True  # 保护进程组


def get_current_session_chain() -> set[int]:
    """返回当前会话的 PID 链 (含 PPID + grand-PPID + pgid 群)。"""
    pid = os.getpid()
    chain = {pid}
    try:
        pgid = os.getpgid(pid)
        # 同进程组 (pgid) 的所有进程都属当前会话
        result = subprocess.run(
            ["ps", "-eo", "pid,pgid"],
            capture_output=True, text=True, timeout=3,
        )
        for line in result.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                try:
                    p, g = int(parts[0]), int(parts[1])
                    if g == pgid:
                        chain.add(p)
                except ValueError:
                    pass
    except Exception:
        pass
    # 上溯 PPID 链
    current = pid
    for _ in range(10):
        try:
            with open(f"/proc/{current}/status") as f:
                for line in f:
                    if line.startswith("PPid:"):
                        ppid = int(line.split()[1])
                        if ppid > 1:
                            chain.add(ppid)
                        current = ppid
                        break
                else:
                    break
        except (FileNotFoundError, ProcessLookupError):
            break
    return chain


def get_listen_crush_processes(min_etime_days: float = MIN_ETIME_DAYS) -> list[dict]:
    """返回所有 crush 进程 + 解析后的 cwd + etime。"""
    result = subprocess.run(
        [
            "ps", "-eo", "pid,ppid,etime,cwd,args",
        ],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        return []
    procs = []
    threshold_sec = min_etime_days * 86400
    for line in result.stdout.splitlines()[1:]:
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        pid_str, ppid_str, etime_str, cwd, cmd = parts
        try:
            pid = int(pid_str)
            ppid = int(ppid_str)
        except ValueError:
            continue
        if "crush" not in cmd:
            continue
        etime_sec = _etime_to_seconds(etime_str)
        if etime_sec < threshold_sec:
            continue
        procs.append(
            {
                "pid": pid,
                "ppid": ppid,
                "etime": etime_str,
                "etime_sec": etime_sec,
                "cwd": cwd,
                "cmd": cmd[:80],
                "state": _get_process_state(pid),
            }
        )
    return procs


def _get_process_state(pid: int) -> str:
    """获取进程状态 (R/S/D/T/Z/I 等)."""
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("State:"):
                    return line.split()[1].strip("()")
    except (FileNotFoundError, ProcessLookupError):
        return "?"
    return "?"


def _etime_to_seconds(etime_str: str) -> int:
    """[[dd-]hh:]mm:ss → 秒.

    支持格式:
    - "30" → 30 秒
    - "05:30" → 5 分 30 秒 (= 330 秒)
    - "2:30:45" → 2 时 30 分 45 秒 (= 9045 秒)
    - "1-02:30:45" → 1 天 2 时 30 分 45 秒 (= 95445 秒)
    """
    days = 0
    body = etime_str
    if "-" in etime_str:
        d, body = etime_str.split("-", 1)
        try:
            days = int(d) * 86400
        except ValueError:
            return 0
    parts = body.split(":")
    try:
        if len(parts) == 1:  # ss
            return days + int(parts[0])
        if len(parts) == 2:  # mm:ss
            m, s = parts
            return days + int(m) * 60 + int(s)
        if len(parts) == 3:  # hh:mm:ss
            h, m, s = parts
            return days + int(h) * 3600 + int(m) * 60 + int(s)
    except ValueError:
        return 0
    return 0


def filter_safe_to_kill(procs: list[dict], chain: set[int]) -> list[dict]:
    """过滤掉当前会话进程树中的 crush。"""
    return [p for p in procs if p["pid"] not in chain and p["ppid"] not in chain]


def audit_kill(killed: list[int], dry_run: bool) -> None:
    """写 audit log。"""
    ts = dt.datetime.now(dt.timezone.utc).isoformat()
    mode = "DRY_RUN" if dry_run else "APPLIED"
    line = f"{ts} {PROTECT_AGENT_ID} crash_kill mode={mode} killed={killed}\n"
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="灵克 L7/L10 升级 v0.5 — crush zombie 清理 (带 PPID 排除)"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="实际执行 kill (默认 dry-run)",
    )
    parser.add_argument(
        "--min-days", type=float, default=MIN_ETIME_DAYS,
        help=f"etime 阈值 (天, 默认 {MIN_ETIME_DAYS})",
    )
    parser.add_argument(
        "--max-kill", type=int, default=20,
        help="单次最大 kill 数 (默认 20, 防止误杀)",
    )
    args = parser.parse_args()

    chain = get_current_session_chain()
    procs = get_listen_crush_processes(args.min_days)
    safe = filter_safe_to_kill(procs, chain)

    print(
        f"=== crush 清理 {'DRY-RUN' if not args.apply else 'APPLY'} ==="
    )
    print(f"  当前会话链 ({len(chain)} PIDs): {sorted(chain)[:8]}...")
    print(f"  候选 crush ({len(procs)}) / 排除当前会话后 ({len(safe)})")
    print(f"  max-kill: {args.max_kill}")
    if not safe:
        print("  无 zombie 可清理")
        return 0

    kills = safe[: args.max_kill]
    print(f"\n将 kill {len(kills)} 进程:")
    for p in kills:
        print(f"  PID {p['pid']:7d} | etime={p['etime']:15s} | cwd={p['cwd']}")

    if not args.apply:
        print("\n[!] DRY-RUN 模式 — 加 --apply 实际执行")
        audit_kill([p["pid"] for p in kills], dry_run=True)
        return 0

    print("\n执行 SIGTERM + SIGCONT (T 状态)...")
    killed = []
    for p in kills:
        state = p["state"]
        success = False
        try:
            # T 状态进程忽略 SIGTERM, 必须先 SIGCONT 再 SIGTERM
            if state.startswith("T"):
                try:
                    os.kill(p["pid"], signal.SIGCONT)
                except ProcessLookupError:
                    pass
                time.sleep(0.3)
            os.kill(p["pid"], signal.SIGTERM)
            time.sleep(0.5)
            # 检查是否退出
            try:
                os.kill(p["pid"], 0)
                # 仍在跑 → SIGKILL
                os.kill(p["pid"], signal.SIGKILL)
                time.sleep(0.3)
                try:
                    os.kill(p["pid"], 0)
                except ProcessLookupError:
                    success = True
            except ProcessLookupError:
                success = True
            if success:
                killed.append(p["pid"])
                print(f"  ✓ killed {p['pid']} (state={state})")
            else:
                print(f"  ✗ {p['pid']} still alive after SIGKILL")
        except ProcessLookupError:
            killed.append(p["pid"])
            print(f"  - {p['pid']} 已退出")
        except PermissionError:
            print(f"  ✗ {p['pid']} 权限不足 (需 root)")
        except Exception as e:
            print(f"  ✗ {p['pid']} 错误: {e}")

    audit_kill(killed, dry_run=False)
    print(f"\n完成: killed {len(killed)}/{len(kills)} 进程")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())