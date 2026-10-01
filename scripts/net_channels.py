#!/usr/bin/env python3
"""net_channels.py — 网络通道矩阵探针（防"无网"误诊）

背景（2026-10-01 双次误诊复盘）：bash 沙箱 curl 全灭被误诊为
「DNS 硬墙 / 基础设施级封锁」，实为 P3 安全模型下沙箱无网 by design；
同机主进程（git push、web_search/web_fetch、外部 agent）网络全部正常。

纪律：判定「无网/断网/被墙」前必跑本脚本；只对实测过的通道下结论，
禁止从单通道失败跳到基础设施层结论（≥2 独立通道实测支持才允许升级断言）。

用法：
  python3 scripts/net_channels.py           # 全量
  python3 scripts/net_channels.py --quick   # 只测 bash 直连 + proxy3

退出码恒为 0（诊断工具不因网络失败而失败），结论看输出矩阵的自动判读。
"""
from __future__ import annotations

import argparse
import datetime
import os
import shutil
import socket
import subprocess
import sys
import time

# 内网 Gitea 是关键判别点：它也不通 → 当前上下文整体无网（沙箱）；
# 它通而公网不通 → 有网但公网被过滤（SNI/出口策略）。
_GITEA = "https://zhinenggitea.iepose.cn"
_PUBLIC = "https://github.com"
_PROXY3_HOST, _PROXY3_PORT = "127.0.0.1", 8765


def _http_probe(url: str, timeout: float = 4.0) -> str:
    """curl 探测，按失败速度区分『瞬断(无网)』与『超时(有网被滤)』。"""
    if shutil.which("curl") is None:
        return "err(no-curl)"
    t0 = time.monotonic()
    try:
        r = subprocess.run(
            ["curl", "-m", str(int(timeout)), "-so", "/dev/null",
             "-w", "%{http_code}", url],
            capture_output=True, text=True, timeout=timeout + 2,
        )
    except subprocess.TimeoutExpired:
        return f"timeout(>{timeout:.0f}s)"
    dt = time.monotonic() - t0
    code = (r.stdout or "").strip()
    if r.returncode == 0 and code.isdigit():
        return f"ok(http {code}, {dt:.2f}s)"
    if r.returncode == 28:
        return f"timeout({dt:.2f}s)"
    if dt < 0.5:
        return f"NO-NET(瞬断 {dt:.2f}s)"  # 沙箱无网特征：TCP/DNS 秒失败
    return f"fail(rc={r.returncode}, {dt:.2f}s)"


def _proxy3_port_probe(timeout: float = 1.5) -> str:
    try:
        with socket.create_connection((_PROXY3_HOST, _PROXY3_PORT),
                                      timeout=timeout):
            return f"ok({_PROXY3_HOST}:{_PROXY3_PORT} 可达)"
    except OSError as e:
        return f"down({e.__class__.__name__})"


def _proxy3_process() -> str:
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().decode(errors="replace")
        except OSError:
            continue
        if "proxy3" in cmd:
            return f"运行中(pid {pid})"
    return "未发现进程"


def main() -> int:
    ap = argparse.ArgumentParser(description="网络通道矩阵探针")
    ap.add_argument("--quick", action="store_true",
                    help="只测 bash 直连 + proxy3，跳过公网探测")
    args = ap.parse_args()

    print(f"== 网络通道矩阵 @ "
          f"{datetime.datetime.now().isoformat(timespec='seconds')} ==")
    rows: list[tuple[str, str]] = []

    rows.append(("bash 直连·内网 Gitea(判别点)", _http_probe(_GITEA)))
    rows.append(("bash 直连·公网 GitHub",
                 "skipped(--quick)" if args.quick else _http_probe(_PUBLIC)))
    rows.append(("proxy3 端口", _proxy3_port_probe()))
    rows.append(("proxy3 进程", _proxy3_process()))
    rows.append(("主进程通道(push/web_fetch)",
                 "不可从沙箱实测(by design)——git push / web_search / "
                 "web_fetch 走主进程网络栈"))
    rows.append(("外部 agent(cc/codex/opencode/crush)",
                 "由主进程拉起自带网络；需调研时经主进程分派"))

    w = max(len(k) for k, _ in rows) + 2
    for k, v in rows:
        print(f"[{k}]".ljust(w + 2) + v)

    gitea = rows[0][1]
    print("\n== 自动判读 ==")
    if "NO-NET" in gitea:
        print("· 内网 Gitea 也瞬断 → 当前上下文是隔离沙箱(P3 by design)。"
              "\n  「无网」仅对 bash 成立，不代表机器无网；"
              "跨通道改用 web_fetch / 外部 agent，push 交主进程。")
    elif gitea.startswith("ok"):
        print("· 内网通而公网不通 → 有网但公网被过滤(SNI/出口策略)，"
              "不是『无网』。")
    else:
        print("· 网络正常或临时抖动；若业务仍报断网，换通道复核后再下结论。")
    print("纪律：单通道失败 ≠ 基础设施结论；升级断言需 ≥2 独立通道实测支持。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
