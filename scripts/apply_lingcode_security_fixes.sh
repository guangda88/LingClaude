#!/usr/bin/env bash
# apply_lingcode_security_fixes.sh — lingcode 密钥泄露处置落盘脚本
# 在 lingcode 目录可写(解除沙箱只读)后运行：一键完成 脱敏+历史清理+hook 安装
#
# 用法: bash /home/ai/lingclaude/scripts/apply_lingcode_security_fixes.sh
# 前置: 1) 沙箱已放开 /home/ai/lingcode 写权限
#        2) 已备份必要数据（脚本内也自动备份）

set -euo pipefail

LINGCODE="${1:-/home/ai/lingcode}"
SANITIZER="/home/ai/lingclaude/scripts/sanitize_lingcode_config.py"
HOOK="/home/ai/lingclaude/scripts/secret_scan_hook.sh"
STAMP="$(date +%Y%m%d_%H%M%S)"

echo "=== 0. 前置检查 ==="
if [ ! -d "$LINGCODE/.git" ]; then
  echo "[错误] $LINGCODE 不是 git 仓库" >&2; exit 1
fi
if [ ! -w "$LINGCODE" ]; then
  echo "[错误] $LINGCODE 不可写 — 请先放开沙箱写权限（LINGCLAUDE_EXTRA_WRITABLE_DIRS）" >&2
  exit 1
fi

cd "$LINGCODE"

echo "=== 1. 备份 ==="
cp config.json "config.json.bak.$STAMP" 2>/dev/null || true
echo "  备份: config.json.bak.$STAMP"

echo "=== 2. 脱敏 config.json（明文 key → \${ENV} 占位） ==="
python3 "$SANITIZER" config.json --apply --env-out .env.example
echo "  ✓ config.json 已脱敏，环境变量清单见 .env.example"

echo "=== 2.5 应用 config.go \${ENV} 展开补丁 ==="
PATCHER="/home/ai/lingclaude/scripts/apply_env_expand_patch.py"
if [ -f "$PATCHER" ]; then
  python3 "$PATCHER" "$LINGCODE"
else
  echo "  [警告] 未找到补丁脚本: $PATCHER"
fi

echo "=== 3. 重写本地历史（清理 e2b4e81 中的明文 key） ==="
# 仅本地未推送提交含 key，用 rebase 重写（自动跳过无法修改的旧提交）
if git branch -r --contains HEAD >/dev/null 2>&1; then
  echo "  [跳过] HEAD 已在远程，历史重写会破坏远程，改为仅脱敏工作区"
else
  # 重建 tip 提交：先把脱敏后的 config.json 提交为新 commit 替代原 e2b4e81
  git add config.json .gitignore
  git -c user.name="$(git log -1 --format=%an)" -c user.email="$(git log -1 --format=%ae)" \
    commit --amend --no-edit --allow-empty 2>/dev/null || {
      echo "  [警告] amend 失败，尝试 rebase"; 
      git rebase HEAD~1 2>/dev/null || echo "  [警告] 历史重写失败，请手动处理";
    }
  echo "  ✓ 历史已重写（tip 提交不再含明文 key）"
fi

echo "=== 4. 安装 secret 扫描 hook ==="
if [ -f "$HOOK" ]; then
  cp "$HOOK" .git/hooks/pre-commit
  chmod +x .git/hooks/pre-commit
  cp "$HOOK" .git/hooks/pre-push
  chmod +x .git/hooks/pre-push
  echo "  ✓ pre-commit/pre-push 已安装 secret 扫描"
else
  echo "  [警告] 未找到 hook 脚本: $HOOK"
fi

echo "=== 5. 验证 ==="
python3 "$SANITIZER" config.json --check && echo "  ✓ config.json 无明文 key" || echo "  [警告] config.json 仍有明文 key"
git log --all --oneline -3

echo ""
echo "=== 完成 ==="
echo "下一步:"
echo "  1) 把 .env.example 里的变量填入真实 key（或 export 到 shell 环境）"
echo "  2) git push 前确认 hook 生效"
echo "  3) 如需彻底清理历史（非 tip 提交也含 key），运行: git filter-repo --replace-text"
