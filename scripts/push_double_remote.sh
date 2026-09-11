#!/usr/bin/env bash
# 推送双远程 (github 密钥远程 + origin HTTPS 远程) — v2.0 带 preflight 前置闸
# 用途: 1) 先跑 git_push_preflight 前置预检 (门禁阻塞/未推送数/工作区脏态)
#       2) 再推 github + origin 双远程
# 前置闸: gate_blocking=True → 硬停 (避免白跑 30 分钟 full-pytest 门禁)
#         无未推送提交     → 提示无内容可推
#         工作区脏         → 警告 (子模块内部噪声自动放行, 其余提示但继续)
# 前提: 主进程网络域 (非 bwrap --unshare-net) 或重启 REPL 加载新代码。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

BRANCH="${1:-master}"
REMOTES=("github" "origin")
FAILED=0

echo "== 推送分支: $BRANCH =="

# ── 前置闸: preflight ──────────────────────────────────────────────
echo "== [前置闸] git_push_preflight =="
PREFLIGHT=$(python3 -c "
import sys, json
sys.path.insert(0, '.')
from lingclaude.engine.git import git_push_preflight
r = git_push_preflight('.')
if not r.is_ok:
    print(json.dumps({'error': r.error}))
else:
    d = r.data
    print(json.dumps({'ok': d['ok'], 'summary': d['summary'], 'checks': d['checks']}))
" 2>/dev/null)

if [ -z "$PREFLIGHT" ] || echo "$PREFLIGHT" | grep -q '"error"'; then
  echo "⚠️  preflight 调用失败 (引擎不可用?), 跳过前置闸继续 (风险自担)"
  echo "    $PREFLIGHT"
else
  echo "    $(echo "$PREFLIGHT" | python3 -c 'import sys,json; print(json.load(sys.stdin)["summary"])')"
  GATE_BLOCKING=$(echo "$PREFLIGHT" | python3 -c 'import sys,json; print(json.load(sys.stdin)["checks"]["gate_blocking"])')
  UNPUSHED=$(echo "$PREFLIGHT" | python3 -c 'import sys,json; d=json.load(sys.stdin)["checks"]; print(d["unpushed_commits"] if d["unpushed_commits"] is not None else -1)')
  DIRTY=$(echo "$PREFLIGHT" | python3 -c 'import sys,json; print(json.load(sys.stdin)["checks"]["dirty"])')
  DIRTY_FILES=$(echo "$PREFLIGHT" | python3 -c 'import sys,json; print(" ".join(json.load(sys.stdin)["checks"]["dirty_files"]))')

  # 1) 门禁阻塞 → 硬停
  if [ "$GATE_BLOCKING" = "True" ]; then
    echo "❌ [前置闸] exempt-review-gate 有 FAIL 未 ack — 拒绝推送 (会触发 30min full-pytest 门禁)"
    echo "   请先处理: python3 scripts/exempt_review_gate.py status"
    echo "            python3 scripts/exempt_review_gate.py ack <sha> -r '原因'"
    exit 2
  fi

  # 2) 无未推送提交
  if [ "$UNPUSHED" = "0" ]; then
    echo "ℹ️  无未推送提交 (origin/master 已同步), 无需推送"
    exit 0
  fi
  if [ "$UNPUSHED" = "-1" ]; then
    echo "⚠️  无法获取未推送数 (origin/master 可能不存在?), 继续推送"
  fi

  # 3) 工作区脏态
  if [ "$DIRTY" = "True" ]; then
    if [ -n "$DIRTY_FILES" ] && ! echo "$DIRTY_FILES" | grep -vq "workspace/better-harness"; then
      echo "ℹ️  工作区脏但仅子模块内部噪声 (better-harness), 放行"
    else
      echo "⚠️  工作区有未提交改动: $DIRTY_FILES"
      echo "    (如需强制推送: 确认改动后手动 git add/commit, 或忽略警告)"
    fi
  fi
fi

# ── 双远程推送 ─────────────────────────────────────────────────────
for r in "${REMOTES[@]}"; do
  echo "--- push → $r ---"
  if timeout 90 git push "$r" "$BRANCH" 2>&1; then
    echo "✅ $r 推送成功"
  else
    echo "❌ $r 推送失败 (exit=$?)"
    FAILED=1
  fi
done

if [ "$FAILED" -eq 0 ]; then
  echo ""
  echo "✅ 双远程推送完成"
  git log --oneline -1
else
  echo ""
  echo "⚠️ 有远程推送失败。检查:"
  echo "  1. 网络域: 当前是否仍被 bwrap --unshare-net 隔离? (主进程终端执行, 或重启 REPL)"
  echo "  2. 凭据:"
  echo "     • origin (HTTPS): ~/.git-credentials 需含 https://guangda88:<PAT>@github.com"
  echo "       (GitHub 已停密码认证, 必须用 PAT)"
  echo "     • github (SSH):   22 被重置时用 SSH over 443 (Host github.com / Port 443)"
  echo "  3. 代理: clash(7890) 未运行时 smart-push 只剩直连; 可 git smart-push 自动选路"
  exit 1
fi
