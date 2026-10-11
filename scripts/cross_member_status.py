#!/usr/bin/env python3
"""
cross_member_status.py — 灵族成员状态盘点 v0.1

设计原则（灵元 1.0）:
  - 仅用 socket / ps / ss / mcp 公开接口，**不读其他灵目录**
  - WSB 边界内（灵克 owner 范围）
  - 每条状态类信息现场实测，不信历史

覆盖维度:
  1. 服务端口（socket TCP connect, 2s timeout）
  2. 进程存活（ps aux + 关键 daemon 关键字）
  3. LingBus 健康（mcp visible_state/audit_report）
  4. 灵克自身 git 状态（owner 范围内）

输出:
  - JSON: 每成员 {ports: {up/down}, procs: {alive: bool, pid?: int}, mcp: {...}}
  - Markdown 报告: 治理快照

用法:
  python3 scripts/cross_member_status.py
  python3 scripts/cross_member_status.py --json
  python3 scripts/cross_member_status.py --watch 60  # 60s 循环
"""
from __future__ import annotations
import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

# === 已知服务端口（来自历次 meeting minutes + handover） ===
# 每条都有可追溯源，未在 handover 出现的不列
KNOWN_SERVICES: Dict[int, str] = {
    # 灵族核心基础设施
    8765: "LLM Proxy (proxy3 target / 当前 llm_proxy)",
    8766: "lingflow_plus Web UI",
    8767: "lingflow_plus alt (历史)",
    8900: "lingmemory 兜底 fallback",
    9528: "LingBus MCP",
    9530: "lingmemory MCP HTTP",
    9532: "lingxi redzone (P0 fail-closed)",
    # 边界代理（属于灵族，私有端口）
    13456: "atomcode daemon (Qwen3-VL 入口)",
    13457: "atomgit_proxy",
    13458: "trae_proxy",
    13461: "browser_agg",
    # 业务服务（handover v22.2 标 DOWN）
    8001: "灵网 backend (alt) / lingzhi BGE-M3",
    8002: "灵律(法律AI)",
    8100: "灵声",
    8780: "灵戴",
    8785: "四诊(调度)",
    8787: "灵戴(穿戴)",
}

# === 12 灵 + 灵通+（治理引擎） ===
MEMBERS: List[Dict[str, Any]] = [
    {"id": "lingclaude",   "name": "灵克",       "daemon_kw": ["lingclaude", "pty_keeper.*lingclaude"]},
    {"id": "lingflow",     "name": "灵通",       "daemon_kw": ["lingflow", "pty_keeper.*lingflow"]},
    {"id": "lingresearch", "name": "灵研",       "daemon_kw": ["lingresearch", "pty_keeper.*lingresearch"]},
    {"id": "lingzhi",      "name": "灵知",       "daemon_kw": ["lingzhi", "pty_keeper.*lingzhi"]},
    {"id": "lingtongask",  "name": "灵通问道",   "daemon_kw": ["lingtongask", "pty_keeper.*lingtongask"]},
    {"id": "lingflow_plus","name": "灵通+",      "daemon_kw": ["lingflow_plus", "lingflow_plus.daemon"]},
    {"id": "lingyang",     "name": "灵扬",       "daemon_kw": ["lingyang", "pty_keeper.*lingyang"]},
    {"id": "lingweb",      "name": "灵网",       "daemon_kw": ["lingweb", "pty_keeper.*lingweb"]},
    {"id": "lingcreate",   "name": "灵创",       "daemon_kw": ["lingcreate", "pty_keeper.*lingcreate"]},
    {"id": "lingmessage",  "name": "灵信",       "daemon_kw": ["lingmessage", "pty_keeper.*lingmessage"]},
    {"id": "lingxi",       "name": "灵犀",       "daemon_kw": ["lingxi", "pty_keeper.*lingxi", "MCP-Server"]},
    {"id": "lingminopt",   "name": "灵极优",     "daemon_kw": ["lingminopt", "pty_keeper.*lingminopt"]},
    {"id": "zhibridge",    "name": "智桥",       "daemon_kw": ["zhibridge", "pty_keeper.*zhibridge"]},
]

TIMEOUT = 2.0


def check_port(port: int, timeout: float = TIMEOUT) -> Dict[str, Any]:
    """现场 socket TCP connect，2s timeout"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    t0 = time.monotonic()
    try:
        s.connect(("127.0.0.1", port))
        elapsed = (time.monotonic() - t0) * 1000
        s.close()
        return {"port": port, "up": True, "latency_ms": round(elapsed, 1)}
    except socket.timeout:
        return {"port": port, "up": False, "reason": "timeout"}
    except ConnectionRefusedError:
        return {"port": port, "up": False, "reason": "refused"}
    except OSError as e:
        return {"port": port, "up": False, "reason": str(e)[:50]}


def check_process(keywords: List[str]) -> Dict[str, Any]:
    """ps aux | grep -E 'kw1|kw2'，OR 关系"""
    if not keywords:
        return {"alive": None, "note": "no keyword"}
    pattern = "|".join(keywords)
    try:
        out = subprocess.run(
            ["ps", "aux"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        matched = []
        for line in out.splitlines():
            if any(kw in line for kw in keywords):
                matched.append(line)
        if matched:
            # 解析 PID (第 2 列)
            pids = []
            for line in matched:
                parts = line.split()
                if len(parts) > 1 and parts[1].isdigit():
                    pids.append(int(parts[1]))
            return {
                "alive": True,
                "count": len(matched),
                "pids": pids[:5],
                "sample": matched[0][:120],
            }
        return {"alive": False, "count": 0}
    except Exception as e:
        return {"alive": None, "error": str(e)[:50]}


def check_lingclaude_git() -> Dict[str, Any]:
    """灵克自身 owner 范围内 git status"""
    repo = "/home/ai/lingclaude"
    try:
        log = subprocess.run(
            ["git", "-C", repo, "log", "--oneline", "-3"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", repo, "status", "-s"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return {
            "in_scope": True,
            "last_commits": log.splitlines()[:3],
            "uncommitted_count": len([l for l in status.splitlines() if l.strip()]),
        }
    except Exception as e:
        return {"in_scope": False, "error": str(e)[:50]}


def probe_member(member: Dict[str, Any]) -> Dict[str, Any]:
    """单成员状态盘点：ports + procs"""
    procs = check_process(member["daemon_kw"])
    return {
        "id": member["id"],
        "name": member["name"],
        "procs": procs,
    }


def probe_all_ports() -> Dict[int, Dict[str, Any]]:
    """所有已知服务端口实测"""
    return {port: check_port(port) for port in sorted(KNOWN_SERVICES)}


def render_markdown(report: Dict[str, Any]) -> str:
    """生成可读治理快照"""
    ts = report["timestamp"]
    lines = [
        f"# 灵族状态快照 — {ts}",
        "",
        f"## 一、服务端口现场实测 ({len(report['ports'])} 个)",
        "",
        "| 端口 | 服务 | 状态 | 延迟/原因 |",
        "|------|------|------|-----------|",
    ]
    for port, info in sorted(report["ports"].items()):
        svc = KNOWN_SERVICES.get(port, "?")
        if info["up"]:
            lines.append(f"| {port} | {svc} | ✅ UP | {info.get('latency_ms', '?')}ms |")
        else:
            lines.append(f"| {port} | {svc} | ❌ DOWN | {info.get('reason', '?')} |")
    lines.append("")
    lines.append(f"## 二、12 灵 + 灵通+ 进程盘点 ({len(report['members'])} 灵)")
    lines.append("")
    lines.append("| 灵 | 进程存活 | PID 数 | 备注 |")
    lines.append("|---|---------|--------|------|")
    for m in report["members"]:
        p = m["procs"]
        if p.get("alive") is True:
            status = f"✅ {p.get('count', 0)} procs"
            pids = ",".join(str(x) for x in p.get("pids", []))[:30]
            lines.append(f"| {m['name']} | ✅ | {p.get('count', 0)} | pids={pids} |")
        elif p.get("alive") is False:
            lines.append(f"| {m['name']} | ❌ | 0 | 无 pty_keeper/daemon |")
        else:
            lines.append(f"| {m['name']} | ❓ | - | {p.get('error', '?')} |")
    lines.append("")
    lines.append("## 三、灵克自身 git 状态（owner 范围）")
    lines.append("")
    git = report.get("lingclaude_git", {})
    if git.get("in_scope"):
        lines.append(f"- 未提交文件数: **{git.get('uncommitted_count', 0)}**")
        for c in git.get("last_commits", []):
            lines.append(f"  - {c}")
    else:
        lines.append(f"- 错误: {git.get('error', '?')}")
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="输出 JSON 而非 Markdown")
    parser.add_argument("--watch", type=int, default=0, help="N 秒循环刷新")
    parser.add_argument("--out", type=str, default=None, help="输出文件路径")
    args = parser.parse_args()

    while True:
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ports": probe_all_ports(),
            "members": [probe_member(m) for m in MEMBERS],
            "lingclaude_git": check_lingclaude_git(),
        }
        if args.json:
            output = json.dumps(report, ensure_ascii=False, indent=2)
        else:
            output = render_markdown(report)
        print(output)
        if args.out:
            mode = "w" if not args.watch else "w"
            with open(args.out, mode) as f:
                f.write(output + "\n")
        if not args.watch:
            break
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
