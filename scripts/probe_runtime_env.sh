#!/usr/bin/env bash
# 运行时环境一次性探测 — 长任务（全量回归/批量任务）启动前必跑
# 教训来源: 2026-09-17 全量回归 20 小时复盘 (docs/POSTMORTEM_REGRESSION_20260917.md)
# 用法: bash scripts/probe_runtime_env.sh [报告路径，默认 /tmp/runtime_env_report.txt]
# 原则: 情报靠探测获得，不靠进程阵亡换

REPORT="${1:-/tmp/runtime_env_report.txt}"
: > "$REPORT"

log() { echo "$*" | tee -a "$REPORT"; }

log "=== 运行时环境探测 $(date '+%F %T') ==="

# ---------------------------------------------------------------- 1. earlyoom
# 本轮回归头号杀手: OOM 时直接 SIGKILL 大内存进程（pytest 首当其冲）
log ""
log "--- earlyoom ---"
if pgrep -x earlyoom > /dev/null 2>&1; then
    EARLYOOM_PID=$(pgrep -x earlyoom | head -1)
    log "状态: ⚠️ 运行中 (pid ${EARLYOOM_PID}) — 大内存长任务有被杀风险"
    EARLYOOM_ARGS=$(tr '\0' ' ' < "/proc/${EARLYOOM_PID}/cmdline" 2>/dev/null)
    log "参数: ${EARLYOOM_ARGS:-（默认阈值）}"
    log "nice: $(ps -o ni= -p "${EARLYOOM_PID}" 2>/dev/null || echo '?')"
    log "对策: 批量任务用串行小批 + 内存护栏; 或临时调高阈值 (需 root)"
else
    log "状态: 未运行"
fi

# ---------------------------------------------------------------- 2. 内存/swap
# swap 高驻留 → 线程栈分配失败 → xdist worker 'can't start new thread'
# 注意: 中文 locale 下 free 表头是"内存：/交换："而非 Mem:/Swap: (本轮实测踩坑)
#       → LC_ALL=C 强制英文 + 按行号取值双保险
log ""
log "--- 内存 (MB) ---"
FREE_OUT=$(LC_ALL=C free -m)
echo "$FREE_OUT" | tee -a "$REPORT"
SWAP_TOTAL=$(echo "$FREE_OUT" | awk 'NR==3{print $2}')
SWAP_USED=$(echo "$FREE_OUT" | awk 'NR==3{print $3}')
MEM_AVAIL=$(echo "$FREE_OUT" | awk 'NR==2{print $7}')
# 兜底校验: 取值失败必须炸出来, 不许静默 (本轮教训: 2>/dev/null 掩盖取值失败)
for VAR_NAME in SWAP_TOTAL SWAP_USED MEM_AVAIL; do
    eval "VAL=\${$VAR_NAME:-}"
    case "$VAL" in ''|*[!0-9]*) log "⚠️ 探测缺陷: ${VAR_NAME} 取值失败 ('$VAL'), 内存判定跳过 — 请人工看 free 输出"; ;; esac
done
if [ "${SWAP_TOTAL:-0}" -gt 0 ] 2>/dev/null; then
    SWAP_PCT=$((SWAP_USED * 100 / SWAP_TOTAL))
    if [ "$SWAP_PCT" -ge 60 ]; then
        log "⚠️ swap 已用 ${SWAP_PCT}% (${SWAP_USED}/${SWAP_TOTAL}MB) — 高风险"
        log "   实证: swap 11GB 驻留时 xdist 线程创建即崩; 此状态严禁 -n auto/-n 8"
    elif [ "$SWAP_PCT" -ge 30 ]; then
        log "⚠️ swap 已用 ${SWAP_PCT}% — 中风险, 并行度保守"
    else
        log "swap 压力正常 (${SWAP_PCT}%)"
    fi
fi
if [ "${MEM_AVAIL:-0}" -lt 2000 ] 2>/dev/null; then
    log "⚠️ 可用内存仅 ${MEM_AVAIL}MB — 先清内存再跑批"
fi

# ---------------------------------------------------------------- 3. 线程水位
log ""
log "--- 线程/进程上限 ---"
log "kernel.threads-max : $(cat /proc/sys/kernel/threads-max 2>/dev/null)"
log "kernel.pid_max     : $(cat /proc/sys/kernel/pid_max 2>/dev/null)"
log "ulimit -u (用户)   : $(ulimit -u)"
CUR_THREADS=$(ls /proc | grep -c '^[0-9]*$' 2>/dev/null)
log "当前进程/线程实体数: ${CUR_THREADS}"
log "当前 bash 会话线程数: $(ls /proc/self/task 2>/dev/null | wc -l)"

# ---------------------------------------------------------------- 4. CPU / 磁盘
log ""
log "--- CPU / 磁盘 ---"
log "CPU 核心数: $(nproc)"
DISK_AVAIL=$(df -m /tmp | awk 'NR==2{print $4}')
log "/tmp 可用: ${DISK_AVAIL}MB"
if [ "${DISK_AVAIL:-0}" -lt 2000 ]; then
    log "⚠️ /tmp 空间不足 2GB — 回归日志可能写满磁盘"
fi

# ---------------------------------------------------------------- 5. 残留进程
log ""
log "--- 残留 pytest/xdist 进程 ---"
LEFTOVER=$(pgrep -af "pytest" 2>/dev/null | grep -v grep || true)
if [ -n "$LEFTOVER" ]; then
    log "⚠️ 发现残留 (先清理再开新批, 防资源叠加):"
    echo "$LEFTOVER" | tee -a "$REPORT"
else
    log "无残留"
fi

# ---------------------------------------------------------------- 6. bash 工具超时自测
# 后台排程, 不阻塞本脚本; 125s 后读结果文件判定
# 实证: bash 工具硬超时 120s, 调用侧 timeout 参数无效 → 一切 >2min 命令必须后台化
log ""
log "--- bash 工具超时自测 (后台排程) ---"
PROBE_FLAG="/tmp/probe_bashtimeout_${$}.flag"
rm -f "$PROBE_FLAG"
nohup bash -c "sleep 125; echo ALIVE_\$(date +%T) > '$PROBE_FLAG'" >/dev/null 2>&1 &
log "已排程: 125s 后检查 ${PROBE_FLAG}"
log "  存在 ALIVE → 工具超时 >125s, 长命令可直接跑"
log "  不存在     → 工具超时 ≤120s, 所有长命令必须 nohup 后台化 + 落盘轮询"

# ---------------------------------------------------------------- 结论
log ""
log "=== 探测结论模板 ==="
log "并行度建议: swap>=60% 或 earlyoom 活跃 → 串行(-j1); 否则 -n4 封顶, 禁 -n auto"
log "长命令策略 : 以自测结果为准; 未确认前一律后台化"
log "报告落盘   : ${REPORT}"

exit 0
