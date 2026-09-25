#!/usr/bin/env bash
# 冒烟三验门禁（2026-09-26，闸门3）——迁移类 commit 提交前强制。
#
# 背景（ERR-05）：d36e5b9 迁移误删 session_token_sink 致启动链崩溃——
#   单测 28 passed 绿而 CLI 全死。交接文档硬约束3：每个回收件撤 core 后
#   必跑启动链冒烟。此前只靠人自觉（文档一句话提醒），本脚本升为机检。
#
# 三验（对应 docs/research/self-evolution-assessment-20260925.md §3.2 闸门3）：
#   1) import 链 ：python -c "import lingclaude.core.wiring"
#   2) CLI 入口 ：lingclaude --help（正确口径是可执行 lingclaude，python -m 是错的）
#   3) 族内回归 ：tests/test_p2a_wiring_manifest.py（wiring 装配面）
#
# 触发条件：staged 改动删除 lingclaude/core/ 下任何文件（迁移/回收动作）。
# 非迁移类提交零触发（staged 无 core/ 删除直接放行，零开销）。
#
# 旁路：LINGCLAUDE_SKIP_SMOKE_GATE=1（显式留痕，与 arch-guard 同惯例）。
# 注册：lefthook.yml pre-commit.smoke-gate + scripts/repair_hook_direct_chain.sh
set -u

if [ "${LINGCLAUDE_SKIP_SMOKE_GATE:-0}" = "1" ]; then
  echo "[smoke-gate] skipped (LINGCLAUDE_SKIP_SMOKE_GATE=1)"
  exit 0
fi

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
cd "$ROOT" || exit 0

# 仅当本次 staged 删除了 core/ 文件（迁移/回收动作）才触发三验
DELETED="$(git diff --cached --name-only --diff-filter=D -- lingclaude/core/ 2>/dev/null)"
if [ -z "$DELETED" ]; then
  exit 0
fi

echo "[smoke-gate] staged 删除 core/ 件（迁移类 commit，触发三验）："
echo "$DELETED" | sed 's/^/  - /'
echo "[smoke-gate] 启动链冒烟三验开始……"

# 双路去重（与 arch_guard_gate 同款：staged 内容哈希锁；三验含 76s pytest，
# 无锁则直连+lefthook 双路各跑一遍，迁移类提交白等两分半）
KEY="$( (git rev-parse HEAD 2>/dev/null; git diff --cached --no-ext-diff 2>/dev/null) | md5sum | cut -d' ' -f1)"
LOCK="/tmp/.smoke_gate_${KEY:0:12}"
if [ -f "$LOCK" ]; then
  echo "[smoke-gate] same staged content already gated, skip duplicate"
  exit 0
fi

FAIL=0

# ── 一验：import 链 ──
PY="${PYTHON:-python3}"
if ! "$PY" -c "import lingclaude.core.wiring" 2>/tmp/.smoke_import_err; then
  echo "✗ 一验 import 链 FAIL：import lingclaude.core.wiring 崩溃"
  tail -5 /tmp/.smoke_import_err | sed 's/^/    /'
  FAIL=1
else
  echo "✓ 一验 import 链 PASS"
fi

# ── 二验：CLI 入口（可执行 lingclaude；交接文档硬约束3 明示 python -m 是错误口径）──
if ! command -v lingclaude >/dev/null 2>&1; then
  echo "✗ 二验 CLI 入口 FAIL：可执行 lingclaude 不在 PATH"
  FAIL=1
elif ! lingclaude --help >/dev/null 2>/tmp/.smoke_cli_err; then
  echo "✗ 二验 CLI 入口 FAIL：lingclaude --help 非零退出"
  tail -5 /tmp/.smoke_cli_err | sed 's/^/    /'
  FAIL=1
else
  echo "✓ 二验 CLI 入口 PASS"
fi

# ── 三验：族内回归（wiring 装配面）──
if ! "$PY" -m pytest -q tests/test_p2a_wiring_manifest.py \
    --no-header -p no:cacheprovider 2>/tmp/.smoke_pytest_err; then
  echo "✗ 三验 族内回归 FAIL：test_p2a_wiring_manifest 有红"
  tail -8 /tmp/.smoke_pytest_err | sed 's/^/    /'
  FAIL=1
else
  echo "✓ 三验 族内回归 PASS"
fi

rm -f /tmp/.smoke_import_err /tmp/.smoke_cli_err /tmp/.smoke_pytest_err

if [ $FAIL -ne 0 ]; then
  echo ""
  echo "════════════════════════════════════════════════"
  echo "✗ 冒烟三验门禁拦截：迁移类 commit 启动链有红（见上）"
  echo "  ERR-05 血训：单测绿 ≠ 启动链活。先修复再提交。"
  echo "  紧急旁路：LINGCLAUDE_SKIP_SMOKE_GATE=1 git commit ...（留痕）"
  echo "════════════════════════════════════════════════"
  exit 1
fi

echo "[smoke-gate] 三验全绿，放行迁移类 commit"
touch "$LOCK"
exit 0
