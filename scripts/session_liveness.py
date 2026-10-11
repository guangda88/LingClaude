#!/usr/bin/env python3
"""会话进程活性判定 — 监督者专用，替代"纯 CPU 采样"误报法。

背景：2026-09-06 事故。监督者用 5 秒 CPU 采样把正在等 LLM 响应的活跃会话
误判为"纯挂机"并建议 kill，导致对话任务丢失。

原理：I/O 密集型 agent（等网络/流式输出）CPU 近零但 **write_bytes 持续增长**，
且其持有的会话/日志文件 mtime 不断刷新。本脚本采样：
  1. /proc/<pid>/io 的 write_bytes 增量（窗口内）
  2. 进程 fd 指向的真实文件的 mtime 新鲜度
  3. TCP ESTABLISHED 连接数（LLM 流式/Bus 长连接）

判定：
  ACTIVE —— 任一信号命中（写增量>0 或 fd 文件 3 分钟内有刷新 或 有 ESTABLISHED 连接）
  IDLE   —— 全部信号为否（此时"挂机"结论才成立）

用法: python3 scripts/session_liveness.py <pid> [--window 5]
"""
from __future__ import annotations

import argparse
import os
import sys
import time


def _io_write_bytes(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/io", encoding="ascii") as f:
            for line in f:
                if line.startswith("write_bytes:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return -1


def _established_count(pid: int) -> int:
    try:
        out = os.popen(f"ss -tnp 2>/dev/null | grep 'pid={pid},' | grep -c ESTAB").read()
        return int(out.strip() or 0)
    except Exception:
        return 0


def _recent_fd_mtime(pid: int, window: float) -> list[str]:
    fresh: list[str] = []
    now = time.time()
    try:
        for fd in os.listdir(f"/proc/{pid}/fd"):
            try:
                target = os.readlink(f"/proc/{pid}/fd/{fd}")
            except OSError:
                continue
            if not target.startswith("/") or "(deleted)" in target:
                continue
            try:
                age = now - os.stat(target).st_mtime
            except OSError:
                continue
            if age <= window * 4:
                fresh.append(f"{target}({age:.0f}s前)")
    except OSError:
        pass
    return fresh[:5]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pid", type=int)
    parser.add_argument("--window", type=float, default=5.0, help="采样窗口秒数")
    args = parser.parse_args()
    pid = args.pid

    if not os.path.exists(f"/proc/{pid}"):
        print(f"VERDICT=GONE（pid {pid} 不存在）")
        return 1

    w0 = _io_write_bytes(pid)
    t0 = time.time()
    time.sleep(args.window)
    w1 = _io_write_bytes(pid)
    wrote = max(0, w1 - max(0, w0))
    estab = _established_count(pid)
    fresh = _recent_fd_mtime(pid, args.window)

    signals = {
        "写增量": f"{wrote} bytes/{args.window:.0f}s",
        "ESTABLISHED连接": estab,
        "fd文件近刷新": fresh or "无",
    }
    active = wrote > 0 or estab > 0 or bool(fresh)
    verdict = "ACTIVE（活跃——禁止 kill）" if active else "IDLE（真空闲——可处置）"
    print(f"VERDICT={verdict}")
    for k, v in signals.items():
        print(f"  {k}: {v}")
    return 0 if active else 2


if __name__ == "__main__":
    sys.exit(main())
