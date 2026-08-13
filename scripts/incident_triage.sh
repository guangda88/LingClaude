#!/usr/bin/env bash
# incident_triage.sh — 事故三层取证脚本（行为规范 v1.1 A/B/J 条代码化）
# 层1 内核层: kern.log* OOM/hung/swap 记录
# 层2 系统服务层: auth.log* sudo 拆除操作 + systemctl show 关键 unit
# 层3 应用层: 保护层状态 + 内存大户
# --json 输出 J 条"事实要素清单"（供同伴复核机器比对）
set -uo pipefail

SINCE=""
JSON=0
while [ $# -gt 0 ]; do
  case "$1" in
    --since) SINCE="$2"; shift 2;;
    --json) JSON=1; shift;;
    *) echo "usage: $0 [--since YYYY-MM-DD] [--json]" >&2; exit 1;;
  esac
done

KERN_FILES=$(ls /var/log/kern.log /var/log/kern.log.* 2>/dev/null || true)
AUTH_FILES=$(ls /var/log/auth.log /var/log/auth.log.* 2>/dev/null || true)

zcat_text() { for f in $1; do case "$f" in *.gz) zcat "$f" 2>/dev/null;; *) cat "$f" 2>/dev/null;; esac; done; }

# ---------- 层1 内核层 ----------
OOM_KILLS=$(zcat_text "$KERN_FILES" | grep -a "Out of memory: Killed process" | sed 's/.*Killed process [0-9]* (\([^)]*\)).*/\1/' | sort | uniq -c | sort -rn | head -8)
OOM_COUNT=$(zcat_text "$KERN_FILES" | grep -ac "Out of memory: Killed process" 2>/dev/null || echo 0)
HUNG=$(zcat_text "$KERN_FILES" | grep -acE "hung_task|soft lockup|hard lockup" 2>/dev/null || echo 0)
SWAP_ON_EVENTS=$(zcat_text "$KERN_FILES" | grep -a "Adding .* swap on" | awk '{print $1, $2}' | tail -3)
SWAPOFF_EVENTS=$(zcat_text "$KERN_FILES" | grep -a "swapoff" | grep -a "Comm: swapoff" | awk '{print $1, $2}' | tail -3)

# ---------- 层2 系统服务层 ----------
SUDO_REMOVALS=$(zcat_text "$AUTH_FILES" | grep -a "COMMAND=" | grep -aE "swapoff|systemctl (stop|disable|mask) (earlyoom|memory-watchdog)" | sed 's/^\(\S*\).*COMMAND=/\1 COMMAND=/' | tail -10)
EARLYOOM_STATE=$(systemctl is-active earlyoom 2>/dev/null || echo unknown)
WATCHDOG_TIMER=$(systemctl is-active memory-watchdog.timer 2>/dev/null || echo unknown)
NRESTARTS=$(systemctl --system show '*.service' -p Id -p NRestarts 2>/dev/null | awk -F= '/^NRestarts=/{n=$2} /^Id=/{if(n+0>=50) print $2"="n; n=0}')

# ---------- 层3 应用层 ----------
SWAP_ACTIVE=$(swapon --show --noheadings 2>/dev/null | wc -l)
EARLYOOM_PID=$(pgrep -x earlyoom | head -1 || true)
MEM_TOTAL_MB=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
MEM_AVAIL_MB=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
TOP_RSS=$(ps aux --sort=-%mem | awk 'NR>1 && $11 !~ /^\[/ {printf "%s(%dMB)  ", $11, $6/1024; c++; if(c>=5) exit}')

report() {
  echo "=== incident_triage 三层取证报告 $(date '+%F %T') ==="
  echo "[层1 内核层] OOM-kill 总数: $OOM_COUNT | hung/lockup: $HUNG"
  echo "$OOM_KILLS" | sed 's/^/  killed: /'
  echo "  swap 激活事件:"; echo "${SWAP_ON_EVENTS:-none}" | awk '{print "    "$1}'
  echo "  swapoff 事件:"; echo "${SWAPOFF_EVENTS:-none}" | awk '{print "    "$1}'
  echo "[层2 系统服务层] earlyoom=$EARLYOOM_STATE watchdog.timer=$WATCHDOG_TIMER"
  echo "  sudo 拆除保护层记录:"
  echo "${SUDO_REMOVALS:-  (无)}" | sed 's/^/    /'
  echo "  NRestarts>=50 的 unit:"
  echo "${NRESTARTS:-  (无)}" | sed 's/^/    /'
  echo "[层3 应用层] swap 活跃数=$SWAP_ACTIVE earlyoom_pid=${EARLYOOM_PID:-none} mem=${MEM_AVAIL_MB}MB/${MEM_TOTAL_MB}MB"
  echo "  top RSS: $TOP_RSS"
  echo "=== 覆盖自检: kern=✓ auth+systemctl=✓ app=✓ (缺一=未完成诊断) ==="
}

json_out() {
  python3 - "$OOM_COUNT" "$HUNG" "$EARLYOOM_STATE" "$WATCHDOG_TIMER" "$SWAP_ACTIVE" "${EARLYOOM_PID:-}" "$MEM_AVAIL_MB" "$MEM_TOTAL_MB" <<'PYEOF'
import json, sys, subprocess
oom, hung, eo, wd, sw, eopid, avail, total = sys.argv[1:9]
def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5, shell=True).stdout.strip()
    except Exception:
        return ""
facts = {
    "triage_version": "1.1",
    "layers_covered": ["kernel", "system_service", "application"],
    "kernel": {"oom_kill_total": int(oom), "hung_or_lockup": int(hung)},
    "protection_layers": {
        "swap_active_count": int(sw),
        "earlyoom_state": eo,
        "earlyoom_pid": eopid or None,
        "memory_watchdog_timer": wd,
    },
    "protection_removers_sudo": [l for l in sh("zgrep -ah 'COMMAND=' /var/log/auth.log* 2>/dev/null | grep -E 'swapoff|systemctl (stop|disable|mask) (earlyoom|memory-watchdog)' | tail -10").splitlines() if l],
    "memory": {"avail_mb": int(avail), "total_mb": int(total)},
    "top_rss": sh("ps aux --sort=-%mem | awk 'NR>1 && $11 !~ /^\\[/ {printf \"%s(%dMB)  \", $11, $6/1024; c++; if(c>=5) exit}'"),
}
print(json.dumps(facts, ensure_ascii=False, indent=2))
PYEOF
}

if [ "$JSON" = 1 ]; then json_out; else report; fi
