#!/usr/bin/env bash
# session_relay_orchestrator.sh — 巨型长任务会话接力编排器（v0.2 R9 无人值守三闸版）
#
# 配套文档: docs/runbooks/LONGTASK_SOP.md (§五 接力恢复 / §六 失败语义)
#           docs/R9_UNATTENDED_RELAY_PLAN.md (三闸立项与 AC)
# 职责: 轮询接力队列 → 发现 waiting_relay 的任务态目录 → [R9-3 漂移复核] →
#       [R9-4 成本总闸] → 开新 lingclaude 会话注入 resume_hint → 监督其完成/
#       失败/阻塞 → [R9-5 独立验收闸: 自报 completed 必过验收才出队] → 更新队列
# 边界: 本脚本只是"消费者", 事实源永远是任务态目录里的文件; 它自身崩溃
#       不丢任何任务状态(重跑即续)。blocked_on_human 必停原则不变。
#
# ── R9 三闸协议(任务态目录新增文件) ─────────────────────────────────────
#   ACCEPTANCE_CRITERIA.md  # 开局写: 验收标准清单(R9-5; 缺失则 completed 永不可出队)
#   TASK_GOAL.md            # 开局写: 任务原始目标(R9-3 漂移复核基准; 缺失则跳过复核)
#   VERDICT.txt / ACCEPTANCE_DIFF.md   # 验收官产出: accept|reject + 差异报告
#   GOAL_DRIFT_VERDICT.txt  # 复核官产出: on_track|drift
#   cost_ledger.txt         # 编排器维护: per-task 累计成本($, 会话时长×估价)
# 环境变量: RELAY_ACCEPTOR_BIN/RELAY_COST_LIMIT_USD/RELAY_DRIFT_CHECK_EVERY 等
#           (0=关闭对应闸; 详见参数区注释)
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

# PATH 容错: daemon 常由非交互环境拉起, ~/.local/bin 可能缺席(lingmessage/CLI 同根因)
export PATH="${HOME:-/home/ai}/.local/bin:$PATH"

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

# ── R9 无人值守三闸参数(立项: docs/R9_UNATTENDED_RELAY_PLAN.md) ──────────
ACCEPTOR_BIN="${RELAY_ACCEPTOR_BIN:-lingclaude}"   # R9-5 验收器: 出队前独立复核产物(自报≠完成)
ACCEPTANCE_TIMEOUT="${RELAY_ACCEPTANCE_TIMEOUT:-900}"  # 验收会话超时(秒)
COST_LIMIT_USD="${RELAY_COST_LIMIT_USD:-0}"        # R9-4 per-task 累计成本限额($), 0=不启用
COST_PER_SESSION_USD="${RELAY_COST_PER_SESSION_USD:-0.5}"  # 每会话小时成本估价(粗粒度台账)
DRIFT_CHECK_EVERY="${RELAY_DRIFT_CHECK_EVERY:-3}"  # R9-3 每完成 K 跳复核一次目标, 0=关闭
DRIFT_REVIEWER_BIN="${RELAY_DRIFT_REVIEWER_BIN:-lingclaude}"  # 漂移复核器(只读)
DRIFT_TIMEOUT="${RELAY_DRIFT_TIMEOUT:-600}"        # 复核会话超时(秒)

# ── bin 路径容错(daemon 常由 systemd/非交互 shell 拉起, PATH 可能不含 ~/.local/bin) ──
# 实测教训 2026-10-10: relay 实验 rc=127 "timeout: 无法运行命令 lingclaude"
resolve_bin() { # $1=bin名 → 可用绝对路径(找不到则原样返回, 让失败自然暴露)
  command -v "$1" >/dev/null 2>&1 && { printf '%s' "$1"; return; }
  local base="${HOME:-/home/ai}"
  for cand in "$base/.local/bin/$1" "/usr/local/bin/$1" "/usr/bin/$1"; do
    [[ -x "$cand" ]] && { printf '%s' "$cand"; return; }
  done
  printf '%s' "$1"
}
LINGCLAUDE_BIN="$(resolve_bin "$LINGCLAUDE_BIN")"
ACCEPTOR_BIN="$(resolve_bin "$ACCEPTOR_BIN")"
DRIFT_REVIEWER_BIN="$(resolve_bin "$DRIFT_REVIEWER_BIN")"
printf "[%s] bin解析: session=%s acceptor=%s reviewer=%s\n" "$(date '+%F %T')" "$LINGCLAUDE_BIN" "$ACCEPTOR_BIN" "$DRIFT_REVIEWER_BIN" >&2

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
  printf '接力任务(无人值守恢复)。请先读取 %s/handover.yaml 与 %s 了解全部现场。\nresume_hint: %s\n请从中断点继续, 遵守 LONGTASK_SOP(状态外置/预停协议/blocked_on_human 必停)。\n终点协议(必做): 会话结束前, 将最终状态(仅 completed 或 blocked 二选一)写入 %s/RELAY_MARKER 文件(一行, 无其他内容)——编排器据此判定接力成败, 不写视为未完成并重试。' \
    "$1" "$1" "$hint" "$1"
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

# ── R9-4 成本总闸: per-task 累计台账(粗粒度: 会话数×小时估价) ─────────────
cost_ledger_file() { # $1=state_dir → 台账路径(状态外置原则: 随任务目录走)
  printf '%s/cost_ledger.txt' "$1"
}
cost_accumulate() { # $1=state_dir $2=session_seconds → 累加一次会话成本
  local f; f="$(cost_ledger_file "$1")"
  local add; add=$(awk -v s="$2" -v rate="$COST_PER_SESSION_USD" 'BEGIN{printf "%.4f", s/3600*rate}')
  local prev=0 total
  [[ -f "$f" ]] && prev=$(head -n1 "$f")
  total=$(awk -v p="$prev" -v a="$add" 'BEGIN{printf "%.4f", p+a}')
  printf '%s\n' "$total" > "$f"
  printf '%s' "$total"   # stdout 返回累计值(调用方 $() 捕获)
}
cost_exceeded() { # $1=state_dir → 0=超限 1=未超/未启用 (供调用方 if 判断)
  [[ "$COST_LIMIT_USD" != "0" ]] || return 1
  local f; f="$(cost_ledger_file "$1")"
  [[ -f "$f" ]] || return 1
  local cur; cur=$(head -n1 "$f")
  awk -v c="$cur" -v lim="$COST_LIMIT_USD" 'BEGIN{exit !(c+0 > lim+0)}'
}

# ── R9-5 独立验收闸: completed 出队前必过(自报≠完成, H17 同构防线) ────────
# 协议: state_dir/ACCEPTANCE_CRITERIA.md 存在时, 起独立复核会话对照核产物;
#       判定文件 VERDICT.txt 首行 accept / reject; 验收差异报告 written by reviewer;
#       无 AC 文件 → 无从验收, 视为 reject(防"没写标准就没人能宣称完成")
run_acceptance_gate() { # $1=task_id $2=state_dir → 0=通过(可出队) 非0=拦截
  local task_id="$1" state_dir="$2"
  local ac_file="$state_dir/ACCEPTANCE_CRITERIA.md"
  local verdict_file="$state_dir/VERDICT.txt"
  local diff_file="$state_dir/ACCEPTANCE_DIFF.md"
  rm -f "$verdict_file"
  if [[ ! -s "$ac_file" ]]; then
    log "$task_id 无验收标准(ACCEPTANCE_CRITERIA.md), completed 不可采信 → blocked"
    printf 'reject\nno acceptance criteria file\n' > "$verdict_file"
    printf '# 验收差异报告 — %s\n\n- 缺失: ACCEPTANCE_CRITERIA.md(无标准即无验收, 自报 completed 不可采信)\n' "$(date '+%F %T')" > "$diff_file"
    return 1
  fi
  local prompt
  prompt="你是独立验收官(只读复核, 不可修改任何产物)。任务 ${task_id} 自报 completed。
请严格对照验收标准 ${ac_file} 逐条核验:
1) 每条 AC 对应的产物文件是否存在且非空(逐个 ls/wc 实测, 禁止凭叙述采信);
2) 可复跑项抽验冒烟(能跑则跑, 不能跑注明原因);
3) 产物内容与 AC 语义一致(防文件存在但内容空壳)。
输出要求(强制):
- 第一行只写 accept 或 reject;
- 若 reject, 逐条列出缺失/不合格项到 ${diff_file}(markdown, 含实测证据如 ls/wc 输出);
- 判定写入 ${verdict_file}(第一行 accept/reject)。"
  if timeout "$ACCEPTANCE_TIMEOUT" "$ACCEPTOR_BIN" run --print "$prompt" >"$state_dir/acceptance_run.log" 2>&1; then
    if [[ -f "$verdict_file" ]] && head -n 1 "$verdict_file" | grep -q '^accept$'; then
      log "$task_id 验收闸通过(独立复核 accept), 准出队"
      return 0
    fi
  fi
  log "$task_id 验收闸拦截(判 reject 或复核会话异常), 转 blocked"
  if [[ ! -f "$verdict_file" ]]; then printf 'reject\nreviewer did not produce verdict\n' > "$verdict_file"; fi
  if [[ ! -f "$diff_file" ]]; then
    printf '# 验收差异报告 — %s\n\n- 验收官会话未产出差异报告(见 acceptance_run.log), 按保守原则拦截自报 completed\n- 人工复核入口: 对照 %s 与 %s/relay_run_*.log\n' "$(date '+%F %T')" "$ac_file" "$state_dir" > "$diff_file"
  fi
  return 1
}

# ── R9-3 目标漂移检测: 每完成 K 跳, 只读复核"还在干最初那件事吗" ───────────
# 协议: state_dir/TASK_GOAL.md(任务原始目标, 开局必写) + GOAL_DRIFT_VERDICT.txt
drift_check_needed() { # $1=attempts(已完成跳数) → 0=需要复核(即将启动第 attempts+1 跳, 每 K 跳的首跳复核)
  [[ "$DRIFT_CHECK_EVERY" != "0" && "$1" -gt 0 ]] || return 1
  local n="$1"
  (( (n + 1) % DRIFT_CHECK_EVERY == 0 ))
}
run_drift_check() { # $1=task_id $2=state_dir → 0=on-track 1=drift/复核失败(宁可误拦不可放漂)
  local task_id="$1" state_dir="$2"
  local goal_file="$state_dir/TASK_GOAL.md"
  local verdict_file="$state_dir/GOAL_DRIFT_VERDICT.txt"
  rm -f "$verdict_file"
  if [[ ! -s "$goal_file" ]]; then
    log "$task_id 无 TASK_GOAL.md, 无法漂移复核(建议开局写入) — 本次跳过"
    return 0
  fi
  local prompt
  prompt="你是目标漂移复核官(只读)。任务 ${task_id} 已接力 ${attempts} 跳。
原始目标: ${goal_file}
当前最新交接: ${state_dir}/handover.yaml(如无则看该目录最新 handover* 文件)与 RESUME_HINT.txt。
判定当前工作是否仍在服务原始目标(允许路径优化, 不允许目标替换/范围偷换)。
输出(强制): 第一行只写 on_track 或 drift; 若 drift, 在 ${verdict_file} 附理由与证据(引用具体文件)。判定同时写入 ${verdict_file}。"
  if timeout "$DRIFT_TIMEOUT" "$DRIFT_REVIEWER_BIN" run --print "$prompt" >"$state_dir/drift_run.log" 2>&1; then
    if [[ -f "$verdict_file" ]] && head -n 1 "$verdict_file" | grep -q '^on_track$'; then
      log "$task_id 漂移复核: on_track"; return 0
    fi
  fi
  log "$task_id 漂移复核: drift 或复核异常 → 阻断接力"
  [[ -f "$verdict_file" ]] || printf 'drift\nreviewer did not produce verdict\n' > "$verdict_file"
  return 1
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
    log "$task_id 缺 RESUME_HINT.txt, 拒绝盲接力"
    STATUS="blocked"; ERR="missing RESUME_HINT.txt"
    if [[ $DRY_RUN -eq 1 ]]; then
      log "[dry-run] 零副作用: 不翻标记、不发告警 (实跑将: set blocked + 灵信 alert)"
    else
      set_marker "$state_dir" "blocked"; alert_blocked "$task_id" "$state_dir" "$ERR"
    fi
    return 1
  fi

  # R9-3: 接力前按跳数做目标漂移复核(失败宁可误拦, 不放目标漂移)
  if drift_check_needed "$attempts"; then
    if [[ $DRY_RUN -eq 1 ]]; then
      log "[dry-run] 第 $attempts 跳触发漂移复核 (实跑将: $DRIFT_REVIEWER_BIN 只读复核 TASK_GOAL.md vs 现场)"
    elif ! run_drift_check "$task_id" "$state_dir"; then
      set_marker "$state_dir" "blocked"
      STATUS="blocked"; ERR="goal drift detected at hop $attempts (see GOAL_DRIFT_VERDICT.txt)"
      alert_blocked "$task_id" "$state_dir" "目标漂移: 等人工裁决后重置 RELAY_MARKER=waiting_relay"
      return 1
    fi
  fi

  # R9-4: 成本总闸 — 启动前先看台账, 超限不再拉新会话
  if cost_exceeded "$state_dir"; then
    log "$task_id 成本超限(ledger=$(cat "$(cost_ledger_file "$state_dir")" 2>/dev/null) 限额=\$${COST_LIMIT_USD}) → blocked"
    set_marker "$state_dir" "blocked"
    STATUS="blocked"; ERR="cost limit exceeded: \$${COST_LIMIT_USD}"
    alert_blocked "$task_id" "$state_dir" "成本总闸触发: 累计超 \$${COST_LIMIT_USD}, 需人工提额或终止"
    return 1
  fi

  [[ $DRY_RUN -eq 1 ]] && { log "[dry-run] 将执行: $LINGCLAUDE_BIN run --print <prompt: ${prompt:0:80}…>"; return 0; }

  set_marker "$state_dir" "running"
  local out_file="$state_dir/relay_run_$$.log"
  local t0 t1 elapsed
  t0=$(date +%s)
  if timeout "$SESSION_TIMEOUT" "$LINGCLAUDE_BIN" run --print "$prompt" >"$out_file" 2>&1; then
    # 会话退出后看它自报的终态(预停协议要求接力会话结束时翻转标记)
    t1=$(date +%s); elapsed=$((t1-t0))
    # R9-4: 无论成败, 本次会话时长计入成本台账
    local spent; spent=$(cost_accumulate "$state_dir" "$elapsed")
    log "$task_id 本次会话 ${elapsed}s, 任务累计成本 ≈\$${spent}"
    read_marker "$state_dir"
    if [[ "$REPLY" == "completed" || "$REPLY" == "blocked" ]]; then
      if [[ "$REPLY" == "completed" ]]; then
        # R9-5: 自报 completed 不可直接采信, 必过独立验收闸(H17 同构防线)
        if run_acceptance_gate "$task_id" "$state_dir"; then
          STATUS="completed"; log "$task_id 出队(completed 已独立验收)"
        else
          set_marker "$state_dir" "blocked"
          STATUS="blocked"; ERR="acceptance gate rejected self-reported completed (see ACCEPTANCE_DIFF.md)"
          alert_blocked "$task_id" "$state_dir" "验收闸拦截自报completed, 差异见 ACCEPTANCE_DIFF.md"
        fi
      else
        STATUS="blocked"; log "$task_id 接力会话自报 blocked, 转人工"
      fi
    else
      # 接力会话没按协议翻标记: 视为未完成, 允许重试
      set_marker "$state_dir" "waiting_relay"
      STATUS="waiting_relay"; ERR="session ended without terminal marker"
      log "$task_id 接力会话未留下终态标记, 计划重试"
    fi
  else
    local rc=$?
    t1=$(date +%s); elapsed=$((t1-t0))
    local spent; spent=$(cost_accumulate "$state_dir" "$elapsed")
    log "$task_id 接力会话异常退出(rc=$rc, ${elapsed}s, 累计≈\$${spent}), 计划重试"
    set_marker "$state_dir" "waiting_relay"
    STATUS="waiting_relay"; ERR="lingclaude run rc=$rc (timeout=$SESSION_TIMEOUT)"
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
      if [[ $DRY_RUN -eq 1 ]]; then log "[dry-run] $id state_dir 缺失, 零副作用跳过 (实跑将: 队列翻 blocked + alert)"
      else update_entry "$id"; alert_blocked "$id" "$state_dir" "$ERR"; fi; continue; }

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
