#!/usr/bin/env bash
# 架构守卫门禁（2026-09-23）：G1/G2/M3 红灯型守卫提交前强制。
#
# 背景：4787ec7(09-23 07:21) 删行导致 G1 行号漂移假红后，G1+M3 双红 6 小时、
#   12 笔提交带病入库无人拦截（守卫为事后审计面，无环节强制其绿）——
#   「纯自觉不可靠」已被实证，门禁化即本脚本。
#
# 设计：
#   - 沿用 C1 先例（lefthook 在 ulimit -v 512MB 下 Go runtime 崩溃 → secret
#     scan 直连 .git/hooks/pre-commit），本脚本被 .git/hooks/pre-commit 直连，
#     同时注册进 lefthook.yml（本文件入库，其他环境自动生效）。
#   - 双路并存会双重执行，按 staged 内容哈希去重（同一次提交只跑一次；
#     不同提交 staged 内容必不同，不会误跳）。
#   - 范围仅红灯型守卫：G1（core→engine 计数基线）、G2（sys.path 基线）、
#     M3（依赖方向，engine 静态侧读 G1 基线）。G3 数字型守卫为告警级，
#     J5 已裁定慢性噪音，入门禁会重演「烦死→被绕过」，不入。
#
# 旁路：LINGCLAUDE_SKIP_ARCH_GATE=1（显式留痕，与 pre-push 豁免门同一惯例）。
set -u

if [ "${LINGCLAUDE_SKIP_ARCH_GATE:-0}" = "1" ]; then
  echo "[arch-gate] skipped (LINGCLAUDE_SKIP_ARCH_GATE=1)"
  exit 0
fi

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
cd "$ROOT" || exit 0

# 同一次提交双路触发去重（staged 内容哈希；失败时不落锁，修复后自然重跑）
KEY="$( (git rev-parse HEAD 2>/dev/null; git diff --cached --no-ext-diff 2>/dev/null) | md5sum | cut -d' ' -f1)"
LOCK="/tmp/.arch_gate_${KEY:0:12}"
if [ -f "$LOCK" ]; then
  echo "[arch-gate] same staged content already gated, skip duplicate"
  exit 0
fi

PY="${PYTHON:-python3}"
OUT="$("$PY" -m pytest -q \
  "tests/test_p04_arch_guards.py::test_g1_core_engine_imports_count_baseline" \
  "tests/test_p04_arch_guards.py::test_g2_no_new_sys_path_insert" \
  "tests/test_iron_law_guards.py::test_m3_dependency_direction" \
  -x --no-header -p no:cacheprovider 2>&1)"
RC=$?
echo "$OUT" | tail -6

if [ $RC -ne 0 ]; then
  echo ""
  echo "════════════════════════════════════════════════"
  echo "✗ 架构守卫门禁拦截：G1/G2/M3 存在红灯（见上）"
  echo "  处置：修复违规 / 按台账纪律归因后调基线（只紧不松）"
  echo "  紧急旁路：LINGCLAUDE_SKIP_ARCH_GATE=1 git commit ...（留痕）"
  echo "════════════════════════════════════════════════"
  exit 1
fi

touch "$LOCK"
find /tmp -name ".arch_gate_*" -mtime +1 -delete 2>/dev/null
exit 0
