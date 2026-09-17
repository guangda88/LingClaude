#!/usr/bin/env bash
# 安全回归驱动 — 本机 (earlyoom + swap 驻留 + bash 工具 120s 超时) 环境的标准姿势
# 教训来源: 2026-09-17 全量回归 20 小时复盘 (docs/POSTMORTEM_REGRESSION_20260917.md)
#
# 用法:
#   直接跑（短验证）:
#     bash scripts/regression_safe.sh tests/test_todo_store.py
#   后台跑全量（推荐; 脱离 REPL 会话, 断点可续）:
#     setsid nohup bash scripts/regression_safe.sh --daemon > /tmp/reg_driver.log 2>&1 &
#   断点续跑: 再执行同一命令, 已成功批次自动跳过
#   清空重跑: rm -rf <输出目录>
#
# 核心设计（对应本轮踩的坑）:
#   [坑] earlyoom 杀大内存进程   → 默认串行 -p no:randomly, 批间内存护栏
#   [坑] swap 驻留 → 线程枯竭    → 禁 xdist; 若 PROBE_PARALLEL=1 且内存健康才允许 -n4 封顶
#   [坑] REPL 重启杀子进程       → --daemon 模式 setsid 脱离会话
#   [坑] 输出丢失/假完成         → 每批落盘 + exit flag + SUMMARY 交叉核对
#   [坑] pkill -f 自匹配         → 用 PID 文件, 不用 pkill
#   [坑] 变量被沙箱掏空          → 多字母变量名, 禁单字母
#   [坑] 2>/dev/null 掩盖错误    → 关键步骤不吞 stderr; 失败显式落盘

set -u
cd "$(dirname "$0")/.." || exit 1

# 教训: 函数必须先定义后调用 — early_die 在参数解析循环里就可能触发,
#       定义放循环后会 "command not found" 且循环不退、无限刷错 (实测爆 369KB 日志)
early_die() { echo "FATAL: $*" >&2; exit 1; }
early_log() { echo "[$(date '+%T')] $*" >&2; }

DAEMON_MODE=0
STRICT_MEM=1          # 1=批间内存不健康则等待; 0=仅警告
BATCH_SIZE=30         # 每批文件数
OUTDIR=""
PREFIX="tests/"
EXTRA_ARGS="-p no:cacheprovider -p no:randomly"

TARGETS=""
while [ $# -gt 0 ]; do
    case "$1" in
        --daemon)   DAEMON_MODE=1; shift ;;
        --batch)    BATCH_SIZE="${2:-30}"; shift 2 ;;
        --outdir)   OUTDIR="${2:-}"; shift 2 ;;
        --no-strict-mem) STRICT_MEM=0; shift ;;
        # 教训: 选项解析不许遇位置参数即 break (本轮冒烟实测: --outdir 被静默忽略)
        --) shift; while [ $# -gt 0 ]; do TARGETS="${TARGETS:+$TARGETS }$1"; shift; done ;;
        -*) early_die "未知选项: $1 (支持: --daemon --batch N --outdir DIR --no-strict-mem --)" ;;
        *)  TARGETS="${TARGETS:+$TARGETS }$1"; shift ;;
    esac
done
TARGETS="${TARGETS:-tests/}"

TS=$(date +%m%d_%H%M%S)
OUTDIR="${OUTDIR:-/tmp/lingclaude_reg_${TS}}"
mkdir -p "$OUTDIR"
PID_FILE="${OUTDIR}/driver.pid"
echo $$ > "$PID_FILE"

log()  { echo "[$(date '+%T')] $*" | tee -a "${OUTDIR}/driver.log"; }
die()  { log "FATAL: $*"; exit 1; }

# ---------------------------------------------------------------- 0. 预检（记忆中的教训逐条核对）
log "=== 安全回归启动: 输出目录 ${OUTDIR} ==="

# 0.1 残留 pytest 检查（防资源叠加; 不用 pkill, 只报告）
LEFTOVER_PIDS=$(pgrep -f "pytest" 2>/dev/null || true)
if [ -n "$LEFTOVER_PIDS" ]; then
    log "⚠️ 检测到残留 pytest 进程: ${LEFTOVER_PIDS} — 请确认后手动清理, 本脚本不代杀"
fi

# 0.2 内存预检
SWAP_PCT=$(LC_ALL=C free -m | awk 'NR==3{ int($2>0); print ($3*100)/$2 }' 2>/dev/null)
SWAP_PCT="${SWAP_PCT%%.*}"
case "$SWAP_PCT" in ''|*[!0-9]*) SWAP_PCT=0; log "⚠️ swap 读取失败, 护栏降级为仅警告" ;; esac
if [ "$SWAP_PCT" -ge 60 ]; then
    log "⚠️ swap 已用 ${SWAP_PCT}% — 强制串行模式 (xdist 已禁用, 见 EXTRA_ARGS)"
fi

# 0.3 文件清单核实（教训: 凭记忆写清单, 跑了不存在的 test_ask*.py）
if [ -d "$TARGETS" ] || [ -f "$TARGETS" ]; then
    mapfile -t ALL_FILES < <(find "$TARGETS" -name "test_*.py" -type f 2>/dev/null | sort)
else
    mapfile -t ALL_FILES < <(for ONE in $TARGETS; do [ -f "$ONE" ] && echo "$ONE"; done | sort)
fi
TOTAL_FILES=${#ALL_FILES[@]}
[ "$TOTAL_FILES" -eq 0 ] && die "清单为空: '${TARGETS}' 无可发现测试文件 — 停止而非空跑"
log "发现 ${TOTAL_FILES} 个测试文件"

# ---------------------------------------------------------------- 1. 分批（稳定排序 → 断点续跑有效）
BATCH_MAP="${OUTDIR}/batches.txt"
: > "$BATCH_MAP"
BATCH_IDX=0
STAMPER=""
for FPATH in "${ALL_FILES[@]}"; do
    STAMPER="${STAMPER} ${FPATH}"
    BATCH_LEN=$(echo $STAMPER | wc -w)
    if [ "$BATCH_LEN" -ge "$BATCH_SIZE" ]; then
        BATCH_IDX=$((BATCH_IDX+1))
        echo "${BATCH_IDX} ${STAMPER}" >> "$BATCH_MAP"
        STAMPER=""
    fi
done
if [ -n "$STAMPER" ]; then
    BATCH_IDX=$((BATCH_IDX+1))
    echo "${BATCH_IDX} ${STAMPER}" >> "$BATCH_MAP"
fi
TOTAL_BATCHES=$BATCH_IDX
log "分为 ${TOTAL_BATCHES} 批 (每批 ≤${BATCH_SIZE} 文件), 清单: ${BATCH_MAP}"

# ---------------------------------------------------------------- 2. 批间内存护栏
mem_guard() {
    while true; do
        local SP SWAP_DELAY
        SP=$(LC_ALL=C free -m | awk 'NR==3{print int($3*100/$2)}' 2>/dev/null)
        case "$SP" in ''|*[!0-9]*) break ;; esac   # 读不到就不拦, 交给内存预检日志
        [ "$SP" -lt 75 ] && break
        if [ "$STRICT_MEM" -eq 1 ]; then
            log "  内存护栏: swap ${SP}% ≥ 75%, 等待 60s (STRICT 模式)"
            SWAP_DELAY=60
            sleep "$SWAP_DELAY"
        else
            log "  内存护栏: swap ${SP}% (非 STRICT, 仅警告不等待)"
            break
        fi
    done
}

# ---------------------------------------------------------------- 3. 主循环
RUN_START=$(date +%s)
BATCH_NO=0
while IFS=' ' read -r BNO FILE_LIST; do
    BATCH_NO=$BNO
    RESULT="${OUTDIR}/batch_${BNO}.txt"
    FLAG="${OUTDIR}/exit_${BNO}.flag"

    # 断点续跑: 已成功批次跳过
    if [ -f "$FLAG" ] && [ "$(cat "$FLAG")" = "0" ]; then
        log "批 ${BNO}/${TOTAL_BATCHES}: 已成功 (flag=0), 跳过"
        continue
    fi

    mem_guard

    log "批 ${BNO}/${TOTAL_BATCHES}: 开始 ($(echo $FILE_LIST | wc -w) 文件)"
    BATCH_T0=$(date +%s)

    # 串行执行; 关键失败不吞 stderr
    python3 -m pytest $EXTRA_ARGS -q $FILE_LIST \
        > "${RESULT}" 2>"${OUTDIR}/batch_${BNO}.stderr"
    RC=$?
    echo "$RC" > "$FLAG"
    BATCH_SEC=$(( $(date +%s) - BATCH_T0 ))

    if [ "$RC" -eq 0 ]; then
        log "批 ${BNO}: ✅ 通过 (${BATCH_SEC}s)"
    else
        # 教训: worker 崩溃假失败 vs 真失败, 留特征供定性
        if grep -qE "can't start new thread|node down|worker.*crashed" "${RESULT}" "${OUTDIR}/batch_${BNO}.stderr" 2>/dev/null; then
            log "批 ${BNO}: ⚠️ rc=${RC} 且含崩溃特征 — 疑似环境假失败, 标记 RERUN_NEEDED (flag 保持非0)"
            echo "RERUN_${RC}" > "$FLAG"
        else
            log "批 ${BNO}: ❌ rc=${RC} 真失败 (${BATCH_SEC}s) — 详见 ${RESULT}"
        fi
    fi
done < "$BATCH_MAP"

# ---------------------------------------------------------------- 4. 汇总（教训: 完成声明必须交叉核对）
PASS_BATCH=0; FAIL_BATCH=0; RERUN_BATCH=0
for BN in $(seq 1 "$TOTAL_BATCHES"); do
    FV=$(cat "${OUTDIR}/exit_${BN}.flag" 2>/dev/null || echo "MISSING")
    case "$FV" in
        0)          PASS_BATCH=$((PASS_BATCH+1)) ;;
        RERUN*)     RERUN_BATCH=$((RERUN_BATCH+1)) ;;
        *)          FAIL_BATCH=$((FAIL_BATCH+1)) ;;
    esac
done
TOTAL_SEC=$(( $(date +%s) - RUN_START ))

{
    echo "=== 回归汇总 $(date '+%F %T') ==="
    echo "输出目录 : ${OUTDIR}"
    echo "批次     : 总 ${TOTAL_BATCHES} | 通过 ${PASS_BATCH} | 真失败 ${FAIL_BATCH} | 需重跑(崩溃疑云) ${RERUN_BATCH}"
    echo "总耗时   : ${TOTAL_SEC}s"
    echo ""
    echo "失败批次清单:"
    for BN in $(seq 1 "$TOTAL_BATCHES"); do
        FV=$(cat "${OUTDIR}/exit_${BN}.flag" 2>/dev/null || echo "MISSING")
        [ "$FV" != "0" ] && echo "  批 ${BN}: flag=${FV} → ${OUTDIR}/batch_${BN}.txt"
    done
    echo ""
    echo "完成三证: [批次flag齐全: $( [ $((PASS_BATCH+FAIL_BATCH+RERUN_BATCH)) -eq "$TOTAL_BATCHES" ] && echo YES || echo NO )]"
    echo "  1. 每批 exit flag 存在  2. 每批结果文件非空  3. 下方 pytest 统计行"
    grep -h "passed" "${OUTDIR}"/batch_*.txt 2>/dev/null | tail -"$TOTAL_BATCHES" || echo "  (无统计行 — 检查是否有批未跑完)"
} | tee "${OUTDIR}/SUMMARY.txt"

log "=== 全部完成, 汇总: ${OUTDIR}/SUMMARY.txt ==="
rm -f "$PID_FILE"
exit 0
