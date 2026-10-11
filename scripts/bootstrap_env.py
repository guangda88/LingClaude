#!/usr/bin/env python3
"""bootstrap_env.py — 灵克新环境自检与引导（W1 部署复杂度补救 2026-10-10）

用法:
  python3 scripts/bootstrap_env.py            # 人类可读报告
  python3 scripts/bootstrap_env.py --json     # 机读（CI/远端采集）
  python3 scripts/bootstrap_env.py --strict   # 有缺失时 exit 2
  python3 scripts/bootstrap_env.py --print-remediation  # 打印宿主修复命令(不执行)

设计边界:
  - 只读探测: 不自动启动/安装任何服务（宿主操作留给人工或 systemd）
  - 服务清单与 scripts/health_inspect.py:42 对齐（单一口径）
  - netns 感知: 沙箱(--unshare-net)内端口探测失真, 自动降级为"仅配置检查"
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import time

# ---- 服务清单（口径对齐 scripts/health_inspect.py:42）----
SERVICES = {
    "proxy3": {
        "port": 8765, "http_probe": "/v1/models",  # 8765 无 /health(404)，应用层健康=/v1/models
        "start": "bash /home/ai/llm-proxy/start_proxy3.sh",
        "systemd_hint": "systemctl --user start proxy3 (或 watchdog 5min 自动拉起)",
    },
    "lingmemory-mcp": {
        "port": 9530, "http_probe": None,
        "start": "见 lingmemory/ 目录 systemd unit",
        "systemd_hint": "systemctl --user start lingmemory-mcp",
    },
    "webui": {
        "port": 23458, "http_probe": None,
        "start": "atomcode webui (loopback-only)",
        "systemd_hint": "systemctl --user start lingk-webui (或 start lingk.target 四服务全量)",
    },
    "codearts-bridge": {
        "port": 4110, "http_probe": "/health",
        "start": "codearts-bridge (proxy3 码道桥)",
        "systemd_hint": "systemctl --user start codearts-bridge",
    },
}

BINARIES = ["python3", "git", "node", "npm"]
KEY_ENV = ["/home/ai/.ling_keys.env"]
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # /home/ai/lingclaude


def in_sandbox() -> bool:
    """bwrap --unshare-net 沙箱内端口探测失真（health_inspect.py:107 已知现象）。
    启发式: 无法读 /proc/1/comm 为 systemd 或 hostname 为沙箱样式 → 谨慎判沙箱。"""
    try:
        with open("/proc/1/comm") as f:
            if f.read().strip() != "systemd":
                return True
    except OSError:
        return True
    return False


def tcp_probe(port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def http_probe(port: int, path: str, timeout: float = 2.0) -> str:
    try:
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                     headers={"X-Agent-Id": "bootstrap_env"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return f"HTTP {r.status}"
    except Exception as e:  # noqa: BLE001 — 探活报告需吞所有异常
        return f"FAIL {type(e).__name__}"


def main() -> int:
    ap = argparse.ArgumentParser(description="灵克环境自检与引导")
    ap.add_argument("--json", action="store_true", help="机读输出")
    ap.add_argument("--strict", action="store_true", help="缺失即 exit 2")
    ap.add_argument("--print-remediation", action="store_true",
                    help="对缺失项打印宿主可复制的修复命令(仍只读不执行)")
    args = ap.parse_args()

    sandbox = in_sandbox()
    report: dict = {"timestamp": time.strftime("%F %T"), "sandbox_netns": sandbox,
                    "binaries": {}, "env_keys": {}, "services": {}, "missing": []}

    for b in BINARIES:
        p = shutil.which(b)
        report["binaries"][b] = p or "MISSING"
        if not p:
            report["missing"].append(f"binary:{b}")

    for k in KEY_ENV:
        ok = os.path.exists(k)
        report["env_keys"][k] = "OK" if ok else "MISSING"
        if not ok:
            report["missing"].append(f"env:{k}")

    for name, cfg in SERVICES.items():
        entry: dict = {"port": cfg["port"], "start": cfg["start"]}
        if sandbox:
            entry["tcp"] = "SKIPPED(netns)"
            entry["http"] = "SKIPPED(netns)"
            entry["note"] = "沙箱内端口探测失真，请在宿主复核"
        else:
            up = tcp_probe(cfg["port"])
            entry["tcp"] = "UP" if up else "DOWN"
            entry["http"] = http_probe(cfg["port"], cfg["http_probe"]) if (up and cfg["http_probe"]) else "-"
            if not up:
                report["missing"].append(f"service:{name}:{cfg['port']}")
        entry["systemd_hint"] = cfg["systemd_hint"]
        report["services"][name] = entry

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"灵克环境自检 @ {report['timestamp']}  (netns沙箱={sandbox})")
        print("\n[二进制依赖]")
        for b, p in report["binaries"].items():
            print(f"  {b:<10} {p}")
        print("\n[凭据/环境]")
        for k, v in report["env_keys"].items():
            print(f"  {k:<30} {v}")
        print("\n[服务拓扑]")
        for name, e in report["services"].items():
            print(f"  {name:<16} :{e['port']:<6} tcp={e['tcp']:<14} http={e['http']}")
            if e["tcp"] == "DOWN":
                print(f"    ↳ 启动: {e['start']}")
                print(f"    ↳ 或:   {e['systemd_hint']}")
        if sandbox:
            print("\n⚠ netns 沙箱模式: 端口结论不可信, 仅配置项有效")
        print(f"\n缺失项 {len(report['missing'])} 个: {report['missing'] or '无'}")

    if args.print_remediation and report["missing"]:
        print("\n[修复命令(宿主复制执行, 本脚本不执行任何变更)]")
        for m in report["missing"]:
            if m == "env:/home/ai/.ling_keys.env":
                print("  cp /home/ai/.ling_keys.env.bak /home/ai/.ling_keys.env  "
                      "# 或按 AGENTS.md 凭据节重建")
            elif m.startswith("service:"):
                name = m.split(":")[1]
                print(f"  bash {REPO}/scripts/install_lingk_systemd.sh   # 装配 {name} (lingk.target 聚合)")
            elif m.startswith("binary:"):
                b = m.split(":")[1]
                print(f"  # 缺 {b}: 见 docs/INDEX.md '部署/环境' 桶或发行版包管理器安装")
        print("  # 全部修复后复核: python3 scripts/bootstrap_env.py --strict  (exit 0 = 4/4 齐)")

    if args.strict and report["missing"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
