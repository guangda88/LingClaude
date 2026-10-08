#!/usr/bin/env bash
# relay_audit.sh — 接力治理对账兜底（边界2：todo 无原生 hook 的补偿机制）
#
# 原理：todo_write 是外部工具，会话内无代码级钩子可挂。改为「对账」模式：
#   会话按 SOP §三约定，每完成一个 todo 应同步增量写 handover。
#   本脚本轮询对比 todo 快照(会话侧导出)与 handover 增量记录，
#   发现「todo 已完成但 handover 无对应记录」→ 灵信提醒 + 追加缺口清单。
#
# 用法：
#   scripts/relay_audit.sh --session-state /tmp/<task>_state/audit  \
#                          [--once | --daemon] [--interval 300]
#   会话侧只需在每次 todo_write 后执行：
#     mkdir -p /tmp/<task>_state/audit
#     ls /tmp/<task>_state/audit >/dev/null  # 目录即快照锚点
#     # 并把 {"todo_id":..., "done_at":ts} 逐行追加到 audit/todo_log.jsonl
#
# 退出码：0=对账通过/无事 1=发现缺口(已告警) 2=用法错误
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/relay_lib.sh" 2>/dev/null || true   # 复用 log/alert；无则降级

AUDIT_DIR="" ; MODE="once" ; INTERVAL="${AUDIT_INTERVAL:-300}"
ALERT_BIN="${ALERT_BIN:-lingmessage}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session-state) AUDIT_DIR="$2"; shift 2 ;;
    --once)   MODE="once";   shift ;;
    --daemon) MODE="daemon"; shift ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    *) echo "用法: relay_audit.sh --session-state DIR [--once|--daemon] [--interval SEC]" >&2; exit 2 ;;
  esac
done
[[ -n "$AUDIT_DIR" ]] || { echo "缺少 --session-state" >&2; exit 2; }
mkdir -p "$AUDIT_DIR"

log()  { printf '[%s] [relay-audit] %s\n' "$(date '+%F %T')" "$*"; }
alert_gap() { # $1=task $2=detail —— 灵信提醒(失败不阻塞对账)
  "$ALERT_BIN" send --to lingflow --kind alert --subject "[relay-audit] ${1} 交接缺口" \
    --body "$2" >/dev/null 2>&1 || log "告警发送失败(继续)"
}

audit_once() {
  local todo_log="$AUDIT_DIR/todo_log.jsonl"
  local handover_log="$AUDIT_DIR/handover_incr.jsonl"
  [[ -f "$todo_log" ]] || { log "无 todo_log(会话尚未产出 todo 记录), 无事"; return 0; }
  touch "$handover_log"
  # 缺口 = todo_log 中存在、但 handover_incr 中无同 todo_id 记录的条目
  local gaps
  gaps=$(jq -r -s --slurpfile H <(jq -s '.' "$handover_log" 2>/dev/null || echo '[]') '
    . as $T | $H[0] as $h |
    [ $T[] | select(.todo_id as $id | ($h | map(.todo_id) | index($id) | not)) ]
    | .[] | .todo_id' "$todo_log" 2>/dev/null || true)
  if [[ -z "$gaps" ]]; then log "对账通过: todo 与 handover 增量一一对应"; return 0; fi
  local n; n=$(wc -l <<<"$gaps")
  log "发现 $n 条交接缺口: $(echo "$gaps" | tr '\n' ' ')"
  alert_gap "$(basename "$AUDIT_DIR")" \
    "todo 完成但 handover 无增量记录 ×${n}: $(echo "$gaps" | tr '\n' ',')。请补录 handover 或修正约定。"
  echo "$gaps" >> "$AUDIT_DIR/gaps.history"
  return 1
}

case "$MODE" in
  once)   audit_once ;;
  daemon) log "对账守护启动 interval=${INTERVAL}s dir=$AUDIT_DIR"
          while true; do sleep "$INTERVAL"; audit_once || true; done ;;
esac
