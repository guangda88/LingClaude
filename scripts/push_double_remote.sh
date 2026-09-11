#!/usr/bin/env bash
# 推送双远程 (github 密钥远程 + origin HTTPS 远程) — 2026-09-12
# 用途: lingclaude 沙箱内 git push 被 --unshare-net 隔离时, 走修复后的
#       bash.py 网络白名单 (allow_network=True 不注入 --unshare-net)。
# 前提: 重启 REPL 加载新代码 (sandbox_provider urandom 可写 + 网络白名单),
#       或在主进程网络域 (非 bwrap) 的终端执行。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

BRANCH="${1:-master}"
REMOTES=("github" "origin")
FAILED=0

echo "== 推送分支: $BRANCH =="
for r in "${REMOTES[@]}"; do
  echo "--- push → $r ---"
  if timeout 60 git push "$r" "$BRANCH" 2>&1; then
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
  echo "  1. 是否已重启 REPL 加载新代码 (bash.py 网络白名单 + sandbox urandom 修复)"
  echo "  2. 网络是否通畅 (主进程域): curl -sI https://github.com"
  echo "  3. 凭据: origin 需 HTTPS token; github 需 SSH key"
  exit 1
fi
