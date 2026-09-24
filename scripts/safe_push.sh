#!/bin/sh
# safe_push.sh —— push 标准作业脚本
# 编码 docs/runbooks/PUSH_SOP.md 的全部铁律；事故源见同手册"事故历史"。
# 用法:   scripts/safe_push.sh <remote> [branch]
# 退出码: 0 成功 / 3 沙箱拒跑 / 4 前检失败 / 5 push 失败或指针不符
set -u

REMOTE="${1:-}"
BRANCH="${2:-master}"
if [ -z "$REMOTE" ]; then
  echo "用法: safe_push.sh <remote> [branch]（remote 必填）" >&2
  exit 4
fi

STAMP=$(date +%Y%m%d_%H%M%S)
LOG="/tmp/push_${REMOTE}_${STAMP}.log"

# ---- L1 沙箱拒跑：--unshare-net 内网络判据一律无效（铁律 1）----
if [ "$(ls /sys/class/net 2>/dev/null | grep -cv '^lo$')" -eq 0 ]; then
  echo "REFUSE: 疑似 bwrap --unshare-net 沙箱（无非 lo 网卡）。" >&2
  echo "        沙箱内网络操作/判据一律无效——push 必须走宿主或 git_push 工具。" >&2
  exit 3
fi

# ---- L2 远端存在性 + 双名去重提示（铁律 5 前置）----
git remote get-url "$REMOTE" >/dev/null 2>&1 || {
  echo "FAIL: 远端 $REMOTE 不存在（git remote -v 查看）" >&2; exit 4; }
ORIGIN_URL=$(git remote get-url origin 2>/dev/null || true)
THIS_URL=$(git remote get-url "$REMOTE")
if [ "$REMOTE" != "origin" ] && [ -n "$ORIGIN_URL" ] && [ "$THIS_URL" = "$ORIGIN_URL" ]; then
  echo "NOTE: $REMOTE 与 origin 同 URL——推 origin 已覆盖，本推冗余。"
fi

# ---- L3 资源预检：全量门禁(-n8)需要内存余量，防 earlyoom 误杀 ----
AVAIL_KB=$(awk '/MemAvailable/{print $2; exit}' /proc/meminfo 2>/dev/null || echo 0)
if [ "$AVAIL_KB" -lt 4194304 ] && [ -f .git/hooks/pre-push ] && [ -x .git/hooks/pre-push ]; then
  echo "WARN: MemAvailable=${AVAIL_KB}KB < 4GB，带钩子全量门禁可能被资源峰值误杀。" >&2
  echo "      建议：轻门禁留证 + 按 PUSH_SOP §三 旁路，或等资源空闲。" >&2
fi

# ---- L4 钩子状态记录（铁律：谁禁的谁恢复）----
HOOK=.git/hooks/pre-push
HOOK_STATE=on
if [ -f "$HOOK" ] && [ ! -x "$HOOK" ]; then HOOK_STATE=off; fi
echo "== push $REMOTE/$BRANCH @ $(date '+%F %T') 钩子:$HOOK_STATE 输出:$LOG =="

# ---- L5 输出直写文件，不走管道（铁律 3）----
git push "$REMOTE" "$BRANCH" >"$LOG" 2>&1
RC=$?

# ---- L6 以远端实际指针为准验证，不信本地跟踪引用（铁律 5）----
if [ "$RC" -eq 0 ]; then
  LOCAL_HEAD=$(git rev-parse HEAD)
  REMOTE_HEAD=$(git ls-remote "$REMOTE" "refs/heads/$BRANCH" | awk '{print $1}')
  if [ "$LOCAL_HEAD" = "$REMOTE_HEAD" ]; then
    echo "OK: $REMOTE/$BRANCH == $LOCAL_HEAD（本地=远端实证一致）"
  else
    echo "WARN: push 返回 0 但远端头(${REMOTE_HEAD:-空}) != 本地(${LOCAL_HEAD})，查 $LOG" >&2
    RC=5
  fi
else
  echo "FAIL: git push 退出 $RC，完整输出在 $LOG（勿凭 shell 摘要下结论）" >&2
  tail -20 "$LOG" >&2 2>/dev/null
fi

# ---- L7 钩子执行位安全网恢复 ----
if [ "$HOOK_STATE" = "on" ] && [ -f "$HOOK" ] && [ ! -x "$HOOK" ]; then
  chmod +x "$HOOK" && echo "FIXED: pre-push 钩子执行位已恢复"
fi
exit $RC
