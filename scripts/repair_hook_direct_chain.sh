#!/usr/bin/env bash
# 修复 .git/hooks/pre-commit 直连链路（2026-09-26）。
#
# 背景：arch_guard_gate.sh / lefthook.yml 注释均声称「pre-commit 直连双路，
#   lefthook 崩溃时仍有机检兜底」（C1 先例，2026-09-13）。实测 2026-09-26：
#   .git/hooks/pre-commit 已被 lefthook install 覆盖回纯 shim（mtime 09-23
#   16:29），直连段丢失——G1/G2/M3 门禁、secret scan 实际只剩 lefthook
#   单路，而 lefthook 在低 ulimit 环境（Go runtime 崩溃）恒 exit 0 静默放行。
#   C1 教训原样复现。
#
# .git/hooks 不入库，任何 lefthook install 后本修复都会被冲掉——
# 故做成幂等重放脚本；跑一次修复，以后 lefthook install 后重跑即可。
#
# 用法: bash scripts/repair_hook_direct_chain.sh   （幂等，可重复执行）
set -eu

HOOK=".git/hooks/pre-commit"
MARKER="=== 直连段（repair_hook_direct_chain.sh 维护，勿手改）"

[ -f "$HOOK" ] || { echo "✗ $HOOK 不存在（未 init？）"; exit 1; }

if [ -f "$HOOK" ] && grep -qF "$MARKER" "$HOOK"; then
  echo "→ 直连段已存在，重建（段内容可能已更新）"
  REBUILD=1
fi

cp "$HOOK" "$HOOK.bak.direct_chain.$(date +%Y%m%d_%H%M%S)"

# 直连段插在 lefthook 调用之前：即便 lefthook 崩溃 exit 0，机检已跑完。
# 三段均自带旁路与去重（arch_guard_gate 按 staged 哈希去重，双路不双跑）。
# patch 文本直接放 python heredoc 内（<<'PYEOF' 引号定界，零展开；
# 经 shell 变量中转会在 $(cat <<EOF) 尾换行/引号配对上踩坑，2026-09-26 实测）。
python3 - "$HOOK" <<'PYEOF'
import re, sys

hook = sys.argv[1]
patch = """# === 直连段（repair_hook_direct_chain.sh 维护，勿手改）===
# C1 教训复现修复（2026-09-26）：lefthook 低 ulimit 崩溃恒 exit 0，
# 机检不能只押 lefthook 单路。以下三段在 lefthook 之前直连强制：
bash "$(git rev-parse --show-toplevel)/scripts/secret_scan_hook.sh" || exit 1
bash "$(git rev-parse --show-toplevel)/scripts/arch_guard_gate.sh" || exit 1
bash "$(git rev-parse --show-toplevel)/scripts/smoke_gate.sh" || exit 1
bash "$(git rev-parse --show-toplevel)/scripts/orphan_gate.sh" || exit 1
# === 直连段结束 ===

"""
src = open(hook).read()
needle = 'call_lefthook run "pre-commit"'
if needle not in src:
    print("✗ shim 中找不到 lefthook 调用点，中止", file=sys.stderr)
    sys.exit(1)
# 重建式：先剥掉旧直连段（marker 行到结束标记行含尾空行），再插入新段
src = re.sub(
    r"# === 直连段（.*?# === 直连段结束 ===\n\n",
    "",
    src,
    flags=re.S,
)
open(hook, "w").write(src.replace(needle, patch + needle, 1))
print("✓ 直连段已写入")
PYEOF

chmod +x "$HOOK"
echo "✓ 直连段已插入 $HOOK（secret_scan + arch_guard_gate + smoke_gate）"
echo "  重放时机：每次 lefthook install 之后（.git/hooks 不入库）"
