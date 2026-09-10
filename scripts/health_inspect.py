#!/usr/bin/env python3
"""lingclaude 服务健康巡检 (SDT-lc-002) — 2026-09-09 巡检机制修复产物

检查项（2026-09-09 起）:
  1. lingxi node 进程数/RSS 基线（2026-09-09 事故: 65 个残留累计 2GB 无监控）
  2. lingclaude-bus-poll.service 存活 + /var/tmp 快照新鲜度（/tmp wrapper 事故修复）
  3. 关键端口: 8765 proxy3 / 9530 灵忆 MCP / 13458 trae_proxy / 13460 webui
  4. lingclaude-daemon-watch.service 存活
  5. 活跃交互会话 RSS

增强（2026-09-11, opencode 盲区 #1）:
  端口探活从「ss 在听」升级为「TCP connect RTT 探测」— 端口 LISTEN≠服务可用
  （进程僵死时端口照样 LISTEN）。三次采样报 min/med/max, 任一成功=可达;
  告警文案区分「未监听」与「监听但不可达」两种故障形态。
  注: P50/P99 需连续采样窗口, 巡检点位采样无统计意义, 只报 min/med/max。
  持续延迟分布监控需 proxy 侧 metrics exporter（后续项）。

用法:
  python3 health_inspect.py          # 巡检, 追加日志, 异常 rc=1
  cron: */30 * * * * (见 crontab)

日志: logs/health_inspect.log
"""
from __future__ import annotations

import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

LOG = Path("/home/ai/lingclaude/logs/health_inspect.log")

# 2026-09-09 清理后基线: 8 个进程 / ~413MB。告警阈值: 进程>20 或 RSS>1200MB
LINGXI_MAX_PROCS = 20
LINGXI_MAX_RSS_MB = 1200

PORTS = {"8765": "proxy3", "9530": "lingmemory-mcp", "13458": "trae-proxy", "13460": "webui"}
SNAPSHOT = Path("/var/tmp/lingbus_pending_lingclaude.json")

PORT_PROBE_ROUNDS = 3
PORT_PROBE_TIMEOUT_S = 2.0


def sh(cmd: str) -> str:
    try:
        # nosec B602 — sh() 调用点全部为硬编码字面量命令(ps/ss/grep 运维探针),无插值输入
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return ""


def _tcp_probe(port: int) -> float | None:
    """单次 TCP connect 探测 127.0.0.1:<port>, 返回 RTT 秒; 失败返回 None。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(PORT_PROBE_TIMEOUT_S)
    t0 = time.monotonic()
    try:
        s.connect(("127.0.0.1", port))
        return time.monotonic() - t0
    except Exception:
        return None
    finally:
        s.close()


def probe_port_rtt(port: str, rounds: int = PORT_PROBE_ROUNDS) -> dict:
    """TCP connect 多次采样。任一成功=可达; 返回采样统计供巡检文案使用。"""
    samples: list[float] = []
    for _ in range(max(1, rounds)):
        rtt = _tcp_probe(int(port))
        if rtt is not None:
            samples.append(rtt)
    result: dict = {"reachable": bool(samples), "samples_ms": [round(x * 1000, 1) for x in samples]}
    if samples:
        result["min_ms"] = min(samples) * 1000
        result["med_ms"] = statistics.median(samples) * 1000
        result["max_ms"] = max(samples) * 1000
    return result


def _listening_ports() -> list[str]:
    return sh("ss -tln").splitlines()


def inspect() -> tuple[list[str], list[str]]:
    oks: list[str] = []
    alerts: list[str] = []
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")

    # 1. lingxi 进程基线
    out = sh("ps -eo rss,args | grep 'lingxi/dist/cli.js' | grep -v grep")
    procs = [ln for ln in out.splitlines() if ln.strip()]
    rss_mb = sum(int(ln.split()[0]) for ln in procs if ln.split()) // 1024
    if len(procs) > LINGXI_MAX_PROCS or rss_mb > LINGXI_MAX_RSS_MB:
        alerts.append(f"lingxi 进程 {len(procs)} 个 / {rss_mb}MB 超基线(>{LINGXI_MAX_PROCS}个或>{LINGXI_MAX_RSS_MB}MB)")
    else:
        oks.append(f"lingxi {len(procs)}个/{rss_mb}MB 正常")

    # 2. bus-poll 服务 + 快照新鲜度
    active = sh("systemctl --user is-active lingclaude-bus-poll.service").strip()
    if active != "active":
        alerts.append(f"lingclaude-bus-poll.service 状态={active or 'unknown'}(应为 active)")
    else:
        oks.append("bus-poll active")
    if SNAPSHOT.exists():
        age_min = (time.time() - SNAPSHOT.stat().st_mtime) / 60
        if age_min > 15:
            alerts.append(f"bus 快照过期 {age_min:.0f} 分钟 (>15)")
        else:
            oks.append(f"bus 快照新鲜 ({age_min:.0f}min)")
    else:
        alerts.append("bus 快照文件不存在")

    # 3. 端口（RTT 探测增强: LISTEN≠可用）
    listening = _listening_ports()
    for port, name in PORTS.items():
        r = probe_port_rtt(port)
        if r["reachable"]:
            oks.append(
                f"端口 {port}({name}) 可达 rtt min/med/max="
                f"{r['min_ms']:.1f}/{r['med_ms']:.1f}/{r['max_ms']:.1f}ms"
            )
        elif any(f":{port} " in ln or f":{port}\t" in ln for ln in listening):
            alerts.append(f"端口 {port}({name}) 监听但 connect 全失败(进程可能僵死)")
        else:
            alerts.append(f"端口 {port}({name}) 未监听")

    # 4. daemon watch
    dw = sh("systemctl --user is-active lingclaude-daemon-watch.service").strip()
    if dw != "active":
        alerts.append(f"daemon-watch 状态={dw or 'unknown'}")
    else:
        oks.append("daemon-watch active")

    # 5. 活跃交互会话
    out = sh("ps -eo rss,args | grep 'lingclaude run -i' | grep -v grep")
    sessions = [ln for ln in out.splitlines() if ln.strip()]
    if sessions:
        rss_mb = sum(int(ln.split()[0]) for ln in sessions if ln.split()) // 1024
        oks.append(f"交互会话 {len(sessions)} 个 / {rss_mb}MB")

    stamp = f"[{ts}] OK={len(oks)} ALERT={len(alerts)}"
    lines = [stamp] + [f"  ok  : {o}" for o in oks] + [f"  ALERT: {a}" for a in alerts]
    with LOG.open("a") as f:
        f.write("\n".join(lines) + "\n")
    return oks, alerts


if __name__ == "__main__":
    _, _alerts = inspect()
    print("\n".join(_alerts) if _alerts else "all ok")
    sys.exit(1 if _alerts else 0)
