#!/usr/bin/env python3
"""
atomcode_crash_analysis.py — atomcode daemon 二次 crash 根因调查

设计原则 (灵元 1.0):
  - 灵克 owner 范围 (只读 .crush + .lingclaude + atomcode log)
  - 不动 atomcode binary (属灵犀 owner)
  - 输出报告给会议 #2 议程 4 用

调查维度:
  1. log 末尾: 最近 error/exception
  2. OOM: 是否 RSS > host memory
  3. config: --idle-timeout 是否合理
  4. port conflict: 是否被其他进程占用
  5. crash interval: 第一次死 vs 第二次死的间隔

用法:
  python3 scripts/atomcode_crash_analysis.py
"""
from __future__ import annotations
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ATOMCODE_LOG = Path("/tmp/atomcode_daemon.log")
ATOMCODE_BIN = Path("/home/ai/.local/bin/atomcode")
ATOMCODE_PORT = 13456


def read_log_tail(n=50) -> str:
    if not ATOMCODE_LOG.exists():
        return f"(log not found: {ATOMCODE_LOG})"
    try:
        out = subprocess.run(
            ["tail", "-n", str(n), str(ATOMCODE_LOG)],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout
    except Exception as e:
        return f"(read failed: {e})"


def find_errors(log_text: str) -> list:
    """提取 log 中 ERROR/Exception/FATAL 关键行"""
    patterns = [
        r".*ERROR.*",
        r".*Exception.*",
        r".*FATAL.*",
        r".*OOM.*",
        r".*killed.*",
        r".*traceback.*",
        r".*Traceback.*",
    ]
    found = []
    for line in log_text.splitlines():
        for p in patterns:
            if re.match(p, line, re.IGNORECASE):
                found.append(line[:200])
                break
    return found[-20:]  # 最近 20 条


def check_port_owner() -> dict:
    """查 :13456 是否被其他进程占用"""
    try:
        out = subprocess.run(
            ["ss", "-tlnp", f"sport = :{ATOMCODE_PORT}"],
            capture_output=True, text=True, timeout=5,
        )
        return {"ss_output": out.stdout[:500]}
    except Exception as e:
        return {"error": str(e)}


def check_atomcode_rss() -> dict:
    """查 atomcode daemon 进程 RSS"""
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid,rss,vsz,cmd"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        matched = [l for l in out.splitlines() if "atomcode daemon" in l]
        return {"matches": matched[:5]}
    except Exception as e:
        return {"error": str(e)}


def check_system_mem() -> dict:
    try:
        with open("/proc/meminfo") as f:
            lines = {}
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    lines[parts[0]] = parts[1].strip().split()[0]
        return {
            "MemTotal_gb": round(int(lines.get("MemTotal", 0)) / 1048576, 1),
            "MemAvail_gb": round(int(lines.get("MemAvailable", 0)) / 1048576, 1),
            "SwapTotal_gb": round(int(lines.get("SwapTotal", 0)) / 1048576, 1),
            "SwapFree_gb": round(int(lines.get("SwapFree", 0)) / 1048576, 1),
        }
    except Exception as e:
        return {"error": str(e)}


def main():
    print(f"=== atomcode crash 根因调查 — {datetime.now(timezone.utc).isoformat()} ===\n")
    # 1. log 末尾
    log = read_log_tail(50)
    errors = find_errors(log)
    print("[1] atomcode_daemon.log 末尾 ERROR/Exception:")
    if errors:
        for e in errors:
            print(f"  - {e}")
    else:
        print("  (无 ERROR/Exception/FATAL)")
    # 2. 进程 RSS
    rss = check_atomcode_rss()
    print(f"\n[2] atomcode 进程 RSS:")
    for m in rss.get("matches", []):
        print(f"  {m}")
    # 3. 系统内存
    mem = check_system_mem()
    print(f"\n[3] 系统内存:")
    for k, v in mem.items():
        print(f"  {k}: {v}")
    # 4. 端口占用
    port = check_port_owner()
    print(f"\n[4] :{ATOMCODE_PORT} 端口占用:")
    print(f"  {port.get('ss_output', port.get('error', '?'))[:300]}")
    # 5. log 全文末尾
    print(f"\n[5] atomcode_daemon.log 末尾 20 行:")
    print("-" * 40)
    print("\n".join(log.splitlines()[-20:]))
    print("-" * 40)
    # 6. 结论
    print(f"\n=== 建议 ===")
    if not errors:
        print("  log 中无 ERROR, 推测:")
        print("  - 进程被外部 kill (e.g. OOM killer, 用户手动)")
        print("  - 配置问题 (--idle-timeout=3600 是否触发?)")
        print("  - 端口冲突被外力打断")
    else:
        print(f"  发现 {len(errors)} 条 ERROR, 见上")


if __name__ == "__main__":
    main()