#!/usr/bin/env bash
# session_relay_orchestrator.sh — 巨型长任务会话接力编排器（原型 v0.1）
#
# 配套文档: docs/runbooks/LONGTASK_SOP.md (§五 接力恢复 / §六 失败语义)
# 职责: 轮询接力队列 → 发现 waiting_relay 的任务态目录 → 开新 lingclaude
#       会话注入 resume_hint → 监督其完成/失败/阻塞 → 更新队列
# 边界: 本脚本只是"消费者", 事实源永远是任务态目录里的文件; 它自身崩溃
#       不丢任何任务状态(重跑即续)。
#
# ── 任务态目录协议（会话侧预停时写入, 详见 LONGTASK_SOP §四）──────────
#   /tmp/<task>_state/
#     RELAY_MARKER      # 一行: waiting_relay | running | completed | blocked
#     RESUME_HINT.txt   # 新会话收到的第一句话(必填, 否则拒接力)
#     handover.yaml|md  # Handover V2 交接(供新会话读取, 编排器不解析)
#
# ── 队列文件 schema (scripts/relay_queue.json) ─────────────────────────
#   [
#     {"task_id":"push_gate","state_dir":"/tmp/push_gate_state",
#      "status":"waiting_relay","attempts":0,
#      "next_attempt_ts":0,"last_error":""}
#   ]
#   status 流转: waiting_relay → running → (completed | waiting_relay[重试] | blocked)
#
# ── 用法 ────────────────────────────────────────────────────────────────
#   session_relay_orchestrator.sh --once [--dry-run]   # 单次扫描(调试/systemd timer)
#   session_relay_orchestrator.sh --daemon [--dry-run] # 常驻轮询(默认间隔120s)
#
# ── 部署(可选) ─────────────────────────────────────────────────────────
#   systemd: systemd-run --unit=lingclaude-relay --on-calendar='*:*:00/2' \
#            /home/ai/lingclaude/scripts/session_relay_orchestrator.sh --once
#   或 cron: * * * * * flock -n /tmp/relay.lock ... --once (每分钟扫描一次)
#
# 设计决策: 不用 systemd timer 时 --daemon 内置 flock 单实例锁, 多起无害退出。

set -uo pipefail

# ── 可调参数(环境变量覆盖) ─────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUEUE_FILE="${RELAY_QUEUE_FILE:-$SCRIPT_DIR/relay_queue.json}"
LOCK_FILE="${RELAY_LOCK_FILE:-/tmp/lingclaude_relay.lock}"
POLL_INTERVAL="${RELAY_POLL_INTERVAL:-120}"        # daemon 模式轮询秒数
SESSION_TIMEOUT="${RELAY_SESSION_TIMEOUT:-3600}"   # 单个接力会话最长运行秒数
MAX_ATTEMPTS="${RELAY_MAX_ATTEMPTS:-3}"            # 同一任务最多接力次数(LONGTASK_SOP §六)
LINGCLAUDE_BIN="${RELAY_LINGCLAUDE_BIN:-lingclaude}"
ALERT_RECIPIENTS="${RELAY_ALERT_RECIPIENTS:-lingflow}"
MODE="" ; DRY_RUN=0

# ── 工具函数 ───────────────────────────────────────────────────────────
log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
die() { log "FATAL: $*" >&2; exit 1; }

need_jq() { command -v jq >/dev/null || die "需要 jq (apt install jq)"; }

# 单实例锁(daemon 模式防重复)
acquire_lock() {
  exec 9>"$LOCK_FILE" || die "无法打开锁文件 $LOCK_FILE"
  flock -n 9 || { log "另一实例运行中, 退出(--once 模式不受影响)"; exit 0; }
}

# 从任务态目录读标记; 输出到全局 REPLY 变量(避免 subshell 传值)
read_marker() { # $1=state_dir
  local f="$1/RELAY_MARKER"
  if [[ -f "$f" ]]; then
    REPLY="$(head -c 64 "$f" | tr -d '[:space:]')"
  else
    REPLY=""
  fi
}

# 构造注入 prompt: resume_hint 必填, 附 handover 路径。结果写 stdout(调用方 $() 捕获)
build_prompt() { # $1=state_dir
  local hint_file="$1/RESUME_HINT.txt"
  [[ -f "$hint_file" ]] || return 1
  local hint
  hint="$(cat "$hint_file")"
  printf '接力任务(无人值守恢复)。请先读取 %s/handover.yaml 与 %s 了解全部现场。\nresume_hint: %s\n请从中断点继续, 遵守 LONGTASK_SOP(状态外置/预停协议/blocked_on_human 必停)。' \
    "$1" "$1" "$hint"
}

# 置位任务态目录标记(编排器视角的状态翻转)
set_marker() { # $1=state_dir $2=status
  printf '%s\n' "$2" > "$1/RELAY_MARKER"
}

# 灵信告警(blocked 时)
alert_blocked() { # $1=task_id $2=state_dir $3=reason
  command -v lingmessage >/dev/null || { log "lingmessage 不可用, 跳过告警"; return; }
  lingmessage send --sender lingclaude --recipients "$ALERT_RECIPIENTS" \
    --channel alert --topic "relay-blocked" \
    --subject "[接力编排] 任务 $1 重试 $MAX_ATTEMPTS 次仍失败, 转人工" \
    --body "state_dir=$2 | reason=$3 | 需人工处理后手动重新入队(改 RELAY_MARKER=waiting_relay)" \
    >/dev/null 2>&1 || true
}

# 更新队列中某任务的字段
update_entry() { # $1=task_id  其余: STATUS/ATTEMPTS/NEXT_TS/ERR 为全局
  local tmp; tmp="$(mktemp)"
  jq --arg id "$1" --arg st "$STATUS" --argjson at "$ATTEMPTS" \
     --argjson ts "$NEXT_TS" --arg er "$ERR" \
     'map(if .task_id==$id then .status=$st|.attempts=$at|.next_attempt_ts=$ts|.last_error=$er else . end)' \
     "$QUEUE_FILE" > "$tmp" && mv "$tmp" "$QUEUE_FILE"
}

# ── 核心: 处理单个 waiting_relay 任务 ──────────────────────────────────
relay_one() { # $1=task_id $2=state_dir $3=attempts
  local task_id="$1" state_dir="$2" attempts="$3"
  local prompt
  STATUS="waiting_relay"; ERR=""   # 默认值(set -u 下 scan_once 必可安全引用; dry-run 不翻队列)
  log "接力 $task_id (第 $((attempts+1))/$MAX_ATTEMPTS 次)…"

  read_marker "$state_dir"
  if [[ "$REPLY" != "waiting_relay" ]]; then
    # 竞态: 会话侧已自行翻状态, 以标记为准
    STATUS="$REPLY"; log "$task_id 标记已是 $STATUS, 跳过启动"; return 0
  fi

  if ! prompt=$(build_prompt "$state_dir"); then
    log "$task_id 缺 RESUME_HINT.txt, 拒绝盲接力 → blocked"
    STATUS="blocked"; ERR="missing RESUME_HINT.txt"
    set_marker "$state_dir" "blocked"; alert_blocked "$task_id" "$state_dir" "$ERR"; return 1
  fi

  [[ $DRY_RUN -eq 1 ]] && { log "[dry-run] 将执行: $LINGCLAUDE_BIN run --print <prompt: ${prompt:0:80}…>"; return 0; }

  set_marker "$state_dir" "running"
  local out_file="$state_dir/relay_run_$$.log"
  if timeout "$SESSION_TIMEOUT" "$LINGCLAUDE_BIN" run --print "$prompt" >"$out_file" 2>&1; then
    # 会话退出后看它自报的终态(预停协议要求接力会话结束时翻转标记)
    read_marker "$state_dir"
    if [[ "$REPLY" == "completed" || "$REPLY" == "blocked" ]]; then
      STATUS="$REPLY"; log "$task_id 接力会话自报终态: $STATUS"
    else
      # 接力会话没按协议翻标记: 视为未完成, 允许重试
      set_marker "$state_dir" "waiting_relay"
      STATUS="waiting_relay"; ERR="session ended without terminal marker"
      log "$task_id 接力会话未留下终态标记, 计划重试"
    fi
  else
    local rc=$?
    set_marker "$state_dir" "waiting_relay"
    STATUS="waiting_relay"; ERR="lingclaude run rc=$rc (timeout=$SESSION_TIMEOUT)"
    log "$task_id 接力会话异常退出(rc=$rc), 计划重试"
  fi
}

# ── 主扫描 ─────────────────────────────────────────────────────────────
scan_once() {
  need_jq
  [[ -f "$QUEUE_FILE" ]] || { log "队列不存在($QUEUE_FILE), 无事可做"; return 0; }
  local now; now=$(date +%s)
  # 取到期的 waiting_relay 条目逐个处理
  local ids
  ids=$(jq -r --argjson now "$now" --argjson max "$MAX_ATTEMPTS" \
    '.[] | select(.status=="waiting_relay" and .next_attempt_ts<=$now and .attempts<$max) | .task_id' \
    "$QUEUE_FILE") || die "队列文件解析失败: $QUEUE_FILE"
  [[ -z "$ids" ]] && { log "无到期任务"; return 0; }

  local id
  while IFS= read -r id; do
    [[ -z "$id" ]] && continue
    local entry state_dir attempts
    entry=$(jq -r --arg id "$id" '.[] | select(.task_id==$id)' "$QUEUE_FILE")
    state_dir=$(jq -r '.state_dir' <<<"$entry")
    attempts=$(jq -r '.attempts' <<<"$entry")
    [[ -d "$state_dir" ]] || { STATUS="blocked"; ERR="state_dir missing"; ATTEMPTS=$MAX_ATTEMPTS; NEXT_TS=0
      update_entry "$id"; alert_blocked "$id" "$state_dir" "$ERR"; continue; }

    ATTEMPTS=$((attempts+1)); NEXT_TS=0; ERR=""
    relay_one "$id" "$state_dir" "$attempts"
    if [[ $DRY_RUN -eq 1 ]]; then
      log "[dry-run] 队列与标记保持原样"; continue
    fi
    if [[ "$STATUS" == "waiting_relay" ]]; then
      # 指数退避: 60s * 2^n
      NEXT_TS=$(( $(date +%s) + 60 * (2 ** ATTEMPTS) ))
    fi
    update_entry "$id"
  done <<<"$ids"
}

# ── 入口 ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --once)   MODE="once";   shift ;;
    --daemon) MODE="daemon"; shift ;;
    --dry-run) DRY_RUN=1;    shift ;;
    -h|--help) sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "未知参数: $1 (用 --once/--daemon, 可叠加 --dry-run)" ;;
  esac
done
[[ -n "$MODE" ]] || die "必须指定 --once 或 --daemon"

acquire_lock
log "relay orchestrator 启动 mode=$MODE queue=$QUEUE_FILE dry_run=$DRY_RUN"
if [[ "$MODE" == "once" ]]; then
  scan_once
else
  while true; do
    scan_once
    sleep "$POLL_INTERVAL"
  done
fi
log "relay orchestrator 退出"
