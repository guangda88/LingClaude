#!/usr/bin/env bash
# git hooks 直连链路安装器（2026-09-26，core.hooksPath 方案 v2）。
#
# 演进史：
#   v1（补丁式）：把直连段插进 .git/hooks/pre-commit shim——被当日实测推翻：
#     lefthook 每次运行 sync hooks 都会重写 shim，补丁活不过下一次提交。
#   v2（本版）：core.hooksPath 指向仓库内 .githooks/（入库持久）——
#     .githooks/pre-commit 先跑六段机检直连，再委托 .git/hooks/ 的
#     lefthook shim；lefthook install/sync 只写 .git/hooks/，永远不碰
#     .githooks/。新克隆跑一次本脚本即完成全部接线。
#
# 用法: bash scripts/repair_hook_direct_chain.sh   （幂等，可重复执行）
set -eu
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

HOOKS_DIR=".githooks"
[ -f "$HOOKS_DIR/pre-commit" ] || { echo "✗ $HOOKS_DIR/pre-commit 不存在（仓库文件缺失？）"; exit 1; }

git config core.hooksPath "$HOOKS_DIR"
echo "✓ core.hooksPath = $(git config core.hooksPath)"

chmod +x "$HOOKS_DIR"/*.sh 2>/dev/null || true
chmod +x "$HOOKS_DIR/pre-commit" "$HOOKS_DIR/post-commit" "$HOOKS_DIR/pre-push" 2>/dev/null || true

# 确认 .git/hooks 侧的委托目标（post-commit v3.0 签名钩子 / pre-push shim）
for h in post-commit pre-push; do
  if [ -x ".git/hooks/$h" ]; then
    echo "✓ .git/hooks/$h 存在（.githooks/$h 将委托给它）"
  else
    echo "⚠ .git/hooks/$h 不存在——.githooks/$h 委托将空转（该钩子不生效）"
  fi
done

echo "直连链路：.githooks/pre-commit → secret/arch_guard/smoke/orphan/redlist/tripartite 六段 → lefthook shim"
echo "重放时机：新克隆、或 core.hooksPath 被改后（lefthook install 不会动它）"
