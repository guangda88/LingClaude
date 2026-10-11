#!/usr/bin/env python3
"""Process resource guard — INC-2026-0729-PROCESS-LEAK

L7/L10 工程化 v0.5 P3 实例 (灵克 own scope).

依据:
- atomcode 7/29 07:46 通报 (thread 24e5bb75-...): P3 资源监控扩展
- L7/L10 v0.5 D7: crush zombie cleanup + process guard 资源监控
- 灵克 B 路径: 灵克实施 P3 + cron 化, 灵极优只 review 不写

检测维度:
  1. CPU 高占用 (>80% 持续 10min+)
  2. 长跑进程 (匹配 pattern + >30min)
  3. T 状态 (Ctrl-Z 遗留, 关键 zombie 源)
  4. defunct 进程 (PPID=1, init 未 reap)
  5. 当前会话链保护 (防自杀)

用法:
  python3 process_guard.py check      # 只检查, 输出报告 + rc
  python3 process_guard.py clean      # 检查 + 自动 kill long-run / T-state
  python3 process_guard.py preflight  # 推理脚本启动前 (rc=2 = 拒启动)

L7/L10 框架:
- gate_type: availability
- fail-soft (alert) — 不阻塞主代理
- 与 crush_zombie_cleanup 互补 (cleanup 杀 zombie, guard 预防)
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# 阈值 (env 可覆盖)
CPU_WARN_PCT = float(os.environ.get("PROC_GUARD_CPU_WARN", "80"))
CPU_CRIT_PCT = float(os.environ.get("PROC_GUARD_CPU_CRIT", "95"))
DUR_WARN_MIN = int(os.environ.get("PROC_GUARD_DUR_WARN", "10"))
DUR_CRIT_MIN = int(os.environ.get("PROC_GUARD_DUR_CRIT", "30"))
LONG_RUN_PATTERNS = [
    p.strip() for p in os.environ.get(
        "PROC_GUARD_PATTERNS",
        "llama-cli,peval,bench_all_cpu,bench.sh,test-crash-recovery",
    ).split(",") if p.strip()
]
ATOM_PATTERN = "atomcode"  # 单独标记, 与普通 long-run 区分 (daemon)

# Audit log
AUDIT_LOG = Path(
    os.environ.get("PROC_GUARD_AUDIT", "/home/ai/lingclaude/.lingclaude/hooks/audit.log")
)


def get_session_chain() -> set[int]:
    """当前会话 PID 链 (含 pgid 同组 + 10 层 PPID 链)."""
    pid = os.getpid()
    chain = {pid}
    try:
        pgid = os.getpgid(pid)
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


def etime_to_seconds(etime: str) -> int:
    """[[dd-]hh:]mm:ss → 秒."""
    days = 0
    body = etime
    if "-" in etime:
        d, body = etime.split("-", 1)
        try:
            days = int(d) * 86400
        except ValueError:
            return 0
    parts = body.split(":")
    try:
        if len(parts) == 3:
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
            return days + h * 3600 + m * 60 + s
        if len(parts) == 2:
            m, s = int(parts[0]), int(parts[1])
            return days + m * 60 + s
        if len(parts) == 1:
            return days + int(parts[0])
    except (ValueError, IndexError):
        return 0
    return 0


def list_all_procs() -> list[dict]:
    """ps 一次取全字段, 避免多次 fork."""
    result = subprocess.run(
        ["ps", "-eo", "pid,ppid,stat,etime,cmd"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        return []
    procs = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        pid_str, ppid_str, stat, etime, cmd = parts
        try:
            pid = int(pid_str)
            ppid = int(ppid_str)
        except ValueError:
            continue
        procs.append({
            "pid": pid,
            "ppid": ppid,
            "stat": stat,
            "etime": etime,
            "etime_sec": etime_to_seconds(etime),
            "cmd": cmd[:120],
        })
    return procs


def check_t_state(procs: list[dict], chain: set[int], min_min: int = DUR_CRIT_MIN) -> list[dict]:
    """T 状态进程 = Ctrl-Z 遗留 (关键 zombie 源)."""
    findings = []
    for p in procs:
        if p["pid"] in chain or p["ppid"] in chain:
            continue
        if not p["stat"].startswith("T"):
            continue
        if p["etime_sec"] < min_min * 60:
            continue
        findings.append(p)
    return findings


def check_defunct(procs: list[dict], chain: set[int]) -> list[dict]:
    """defunct 进程 (PPID=1, init 未 reap) = zombie child."""
    findings = []
    for p in procs:
        if p["pid"] in chain or p["ppid"] in chain:
            continue
        if "<defunct>" not in p["cmd"]:
            continue
        findings.append(p)
    return findings


def check_long_run(
    procs: list[dict], chain: set[int], min_min: int = DUR_CRIT_MIN
) -> tuple[list[dict], list[dict]]:
    """匹配 pattern + >30min. 分 (正常 daemon, 普通 long-run)."""
    normal, abnormal = [], []
    for p in procs:
        if p["pid"] in chain or p["ppid"] in chain:
            continue
        minutes = p["etime_sec"] // 60
        if minutes < min_min:
            continue
        # atomcode daemon 单独标记 (运行 OK, 但子 zombie 需检查)
        if ATOM_PATTERN in p["cmd"] and "defunct" not in p["cmd"]:
            normal.append(p)
            continue
        matched = any(pat in p["cmd"] for pat in LONG_RUN_PATTERNS)
        if matched:
            abnormal.append(p)
    return normal, abnormal


def check_high_cpu(threshold_pct: float = CPU_WARN_PCT) -> list[dict]:
    """单次快照 CPU > 阈值 进程. 简化: %CPU 列."""
    result = subprocess.run(
        ["ps", "-eo", "pid,pcpu,cmd"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        return []
    findings = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            cpu = float(parts[1])
        except ValueError:
            continue
        if cpu >= threshold_pct:
            findings.append({"pid": pid, "cpu_pct": cpu, "cmd": parts[2][:100]})
    return findings


def kill_pids(pids: list[int], signal_num: int = signal.SIGTERM) -> list[int]:
    """杀 PID 列表. 返回成功的."""
    killed = []
    for p in pids:
        try:
            os.kill(p, signal_num)
            killed.append(p)
        except (ProcessLookupError, PermissionError):
            pass
    return killed


def audit_log(findings: dict, killed: list[int]) -> None:
    """写 audit log. 注意: 即使 0 也写, 便于 30 min 周期验证."""
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    line = (
        f"{ts} lingclaude process_guard "
        f"t_state={len(findings['t_state'])} "
        f"defunct={len(findings['defunct'])} "
        f"long_run_abnormal={len(findings['long_run_abnormal'])} "
        f"long_run_normal={len(findings['long_run_normal'])} "
        f"high_cpu={len(findings['high_cpu'])} "
        f"killed={killed}\n"
    )
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


NRESTARTS_SNAPSHOT = Path(
    os.environ.get("PROC_GUARD_NRESTARTS_SNAP", "/home/ai/lingclaude/.lingclaude/nrestarts_snapshot.json")
)


def check_service_storms(threshold_abs: int = 50, threshold_delta: int = 10) -> list[str]:
    """服务重启风暴检测 (INCIDENT_20260807: zhibridge 42743 次重启是放大器).

    绝对值 >= threshold_abs 标记历史风暴 unit;
    与上次快照对比 delta >= threshold_delta 标记进行中风暴;
    zhibridge 专项: active 但 NRestarts 增长 = TTY 修复倒退 (atomcode P1-c).
    返回告警描述列表 (空 = 健康).
    """
    import json

    alerts: list[str] = []
    try:
        r = subprocess.run(
            ["systemctl", "--system", "show", "*.service", "-p", "Id", "-p", "NRestarts", "-p", "ActiveState"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            return [f"systemctl show 读取失败 rc={r.returncode}"]
    except Exception as e:
        return [f"systemctl show 异常 {e}"]

    current: dict[str, tuple[int, str]] = {}
    n = 0
    state = ""
    for line in r.stdout.splitlines():
        if line.startswith("NRestarts="):
            n = int(line.split("=", 1)[1] or 0)
        elif line.startswith("ActiveState="):
            state = line.split("=", 1)[1]
        elif line.startswith("Id="):
            unit = line.split("=", 1)[1]
            current[unit] = (n, state)
            n = 0
            state = ""

    for unit, (cnt, st) in current.items():
        if cnt >= threshold_abs:
            alerts.append(f"{unit}: NRestarts={cnt} (历史风暴, state={st})")

    try:
        prev = json.loads(NRESTARTS_SNAPSHOT.read_text()) if NRESTARTS_SNAPSHOT.exists() else {}
    except Exception:
        prev = {}
    for unit, (cnt, st) in current.items():
        delta = cnt - int(prev.get(unit, cnt))
        if delta >= threshold_delta:
            alerts.append(f"{unit}: NRestarts +{delta} 自上次巡检 (进行中风暴!)")
    zb = current.get("zhibridge.service")
    if zb and zb[1] == "active":
        delta = zb[0] - int(prev.get("zhibridge.service", zb[0]))
        if delta > 0:
            alerts.append(f"zhibridge.service: active 但 NRestarts +{delta} (TTY 修复倒退?, atomcode P1-c)")

    try:
        NRESTARTS_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        NRESTARTS_SNAPSHOT.write_text(json.dumps({u: c for u, (c, _) in current.items()}))
    except Exception:
        pass
    return alerts


def check_protection_layers() -> list[str]:
    """保护层存活检查 (INCIDENT_20260807: swapoff/stop earlyoom 手动拆除后无人知晓).

    三项保护层任一缺失 -> 返回缺失描述列表 (空 = 全健康).
    """
    missing = []
    try:
        r = subprocess.run(
            ["swapon", "--show", "--noheadings"],
            capture_output=True, text=True, timeout=3,
        )
        if not r.stdout.strip():
            missing.append("swap: 无活跃 swap (8/1 swapoff 事故重演风险)")
    except Exception as e:
        missing.append(f"swap: 检查失败 {e}")
    try:
        r = subprocess.run(
            ["pgrep", "-x", "earlyoom"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode != 0:
            missing.append("earlyoom: 未运行 (8/2 stop 事故重演风险)")
    except Exception as e:
        missing.append(f"earlyoom: 检查失败 {e}")
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "memory-watchdog.timer"],
            capture_output=True, text=True, timeout=3,
        )
        if r.stdout.strip() != "active":
            missing.append(f"memory-watchdog.timer: {r.stdout.strip() or 'inactive'}")
    except Exception as e:
        missing.append(f"memory-watchdog.timer: 检查失败 {e}")
    return missing


def report(findings: dict) -> int:
    """打印报告. 返回 rc (0 健康, 1 警告)."""
    print("=== process_guard report ===")
    for key, label in [
        ("t_state", "T 状态 (Ctrl-Z 遗留)"),
        ("defunct", "defunct 进程 (zombie child)"),
        ("long_run_abnormal", f"long-run > {DUR_CRIT_MIN}min (匹配 pattern, 异常)"),
        ("long_run_normal", f"long-run > {DUR_CRIT_MIN}min (atomcode daemon, 正常)"),
        ("high_cpu", f"高 CPU >= {CPU_WARN_PCT}%"),
    ]:
        items = findings[key]
        print(f"  {label}: {len(items)}")
        for p in items[:3]:
            extra = ""
            if "cpu_pct" in p:
                extra = f" | {p['cpu_pct']:.0f}% CPU"
            etime = p.get("etime", "n/a")
            pid = p.get("pid", "?")
            cmd = p.get("cmd", "?")
            print(f"    PID {pid:7d} | etime={etime:15s} | {cmd}{extra}")
    layers = findings.get("protection_layers", [])
    print(f"  保护层缺失 (swap/earlyoom/watchdog.timer): {len(layers)}")
    for m in layers:
        print(f"    [!] {m}")
    storms = findings.get("service_storms", [])
    print(f"  服务重启风暴 (NRestarts): {len(storms)}")
    for m in storms:
        print(f"    [!] {m}")
    return 1 if (findings["t_state"] or findings["defunct"] or findings["long_run_abnormal"]) else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="只检查 (always rc=0, 检查完成即成功)")
    sub.add_parser("clean", help="检查 + kill long_run_abnormal + T-state")
    sub.add_parser("preflight", help="推理前 (rc=2 拒启动)")
    parser.add_argument("--max-kill", type=int, default=20, help="clean 模式最大 kill 数")
    args = parser.parse_args()

    chain = get_session_chain()
    procs = list_all_procs()
    findings = {
        "t_state": check_t_state(procs, chain),
        "defunct": check_defunct(procs, chain),
        "long_run_normal": [],
        "long_run_abnormal": [],
        "high_cpu": check_high_cpu(),
        "protection_layers": check_protection_layers(),
        "service_storms": check_service_storms(),
    }
    n1, n2 = check_long_run(procs, chain)
    findings["long_run_normal"] = n1
    findings["long_run_abnormal"] = n2

    report(findings)

    killed = []
    if args.cmd == "clean":
        # 杀 T 状态 (SIGCONT → SIGTERM → SIGKILL)
        for p in findings["t_state"][: args.max_kill]:
            try:
                os.kill(p["pid"], signal.SIGCONT)
                time.sleep(0.2)
                os.kill(p["pid"], signal.SIGTERM)
                time.sleep(0.5)
                os.kill(p["pid"], 0)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                os.kill(p["pid"], signal.SIGKILL)
                killed.append(p["pid"])
            except (ProcessLookupError, PermissionError):
                pass
        # 杀 long_run_abnormal
        killed += kill_pids([p["pid"] for p in findings["long_run_abnormal"][: args.max_kill]])
    # preflight: 只检查不 kill, 拒启动靠 rc=2 表达

    # 保护层缺失: 任何模式都打印醒目告警 (check 模式 rc 仍为 0, timer 一致性)
    if findings["protection_layers"]:
        print("\n[!!] CRITICAL 保护层被拆除, 参考 INCIDENT_20260807:")
        for m in findings["protection_layers"]:
            print(f"     - {m}")
    if findings.get("service_storms"):
        print("\n[!!] 服务重启风暴告警:")
        for m in findings["service_storms"]:
            print(f"     - {m}")
    audit_log(findings, killed)

    # 关键: check 模式 永远 rc=0 (timer 一致性)
    #         preflight 模式 rc=2 仅当有异常 (拒启动)
    #         clean 模式 rc=0 (清理成功即 OK)
    if args.cmd == "preflight" and (findings["t_state"] or findings["defunct"] or findings["long_run_abnormal"]):
        n = len(findings["t_state"]) + len(findings["defunct"]) + len(findings["long_run_abnormal"])
        print(f"\n[!!] preflight 拒启动: {n} 待清理")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())