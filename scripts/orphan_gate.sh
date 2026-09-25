#!/usr/bin/env bash
# 孤儿扫描守卫（2026-09-26，A2）——orphan_scan_v2 接 CI。
#
# 双触发（仅当 staged 触碰 lingclaude/core/ 时跑，其余提交零开销）：
#   1) 双向校验锚点（orphan_scan_v2.py --selfcheck）：
#      l5_audit 必须有生产消费 / l7_cognitive_bridge 必须无生产消费。
#      失败=尺子坏了 或 发生意料外的消费变化（意外复活/误撤）→ 红。
#   2) 增量漂移对比：零生产消费候选集 vs 基线
#      （.atomcode/orphan_scan_v2_baseline.json，入库）；新孤儿必须显式知会。
#
# 红线对齐交接文档：「候选清单未过双向校验前，禁写 2T3A 卷宗 / 任何 record」。
# 本守卫把这条从纪律升为机检：锚点红则迁移类 commit 提交不进去。
#
# 基线更新：bash scripts/orphan_gate.sh --write-baseline（确认候选集变化合法后执行）。
# 旁路：LINGCLAUDE_SKIP_ORPHAN_GATE=1（显式留痕）。
set -u

if [ "${LINGCLAUDE_SKIP_ORPHAN_GATE:-0}" = "1" ]; then
  echo "[orphan-gate] skipped (LINGCLAUDE_SKIP_ORPHAN_GATE=1)"
  exit 0
fi

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
cd "$ROOT" || exit 0

# 双路去重（与 arch_guard_gate 同款：staged 内容哈希锁）
KEY="$( (git rev-parse HEAD 2>/dev/null; git diff --cached --no-ext-diff 2>/dev/null) | md5sum | cut -d' ' -f1)"
LOCK="/tmp/.orphan_gate_${KEY:0:12}"
if [ -f "$LOCK" ]; then
  echo "[orphan-gate] same staged content already gated, skip duplicate"
  exit 0
fi

# 仅当 staged 触碰 core/ 才触发（写基线模式除外——基线管理独立于提交）
if [ "${1:-}" != "--write-baseline" ]; then
  STAGED_CORE="$(git diff --cached --name-only -- lingclaude/core/ 2>/dev/null)"
  if [ -z "$STAGED_CORE" ]; then
    exit 0
  fi
fi

PY="${PYTHON:-python3}"
BASELINE=".atomcode/orphan_scan_v2_baseline.json"

if [ "${1:-}" = "--write-baseline" ]; then
  "$PY" scripts/orphan_scan_v2.py --selfcheck || { echo "✗ selfcheck 未过，拒绝写基线"; exit 2; }
  # 全量模式跑一遍以落盘 result，再拷为基线
  "$PY" scripts/orphan_scan_v2.py > /dev/null
  cp ".atomcode/orphan_scan_v2_result.json" "$BASELINE"
  echo "[orphan-gate] 基线已更新：$BASELINE"
  exit 0
fi

echo "[orphan-gate] staged 触碰 lingclaude/core/，跑孤儿扫描（全量+双向校验）……"
OUT="$("$PY" scripts/orphan_scan_v2.py 2>&1)"
RC=$?
echo "$OUT" | grep -E "^\[PASS\]|^\[FAIL\]|=== " || echo "$OUT" | tail -5

if [ $RC -ne 0 ]; then
  echo ""
  echo "════════════════════════════════════════════════"
  echo "✗ 孤儿扫描守卫拦截：双向校验锚点失败（exit=$RC）"
  echo "  含义：l5_audit 消费丢失 或 l7_cognitive_bridge 意外复活/口径漂移"
  echo "  红线：锚点未过前禁写任何回收 record（交接文档硬约束）"
  echo "  处置：先修尺子/查消费变化，勿绕行"
  echo "  紧急旁路：LINGCLAUDE_SKIP_ORPHAN_GATE=1 git commit ...（留痕）"
  echo "════════════════════════════════════════════════"
  exit 2
fi

# 增量漂移对比（无基线则提示生成，不拦）
if [ ! -f "$BASELINE" ]; then
  echo "[orphan-gate] 无基线文件，跳过漂移对比（生成：bash scripts/orphan_gate.sh --write-baseline）"
  touch "$LOCK"
  exit 0
fi

"$PY" - "$BASELINE" ".atomcode/orphan_scan_v2_result.json" <<'PYEOF'
import json, sys

baseline = json.load(open(sys.argv[1]))
current = json.load(open(sys.argv[2]))
old_orphans = {m for m, d in baseline.items() if d.get("prod_count", 1) == 0}
new_orphans = {m for m, d in current.items() if d.get("prod_count", 1) == 0}
revived = sorted(old_orphans - new_orphans)
grown = sorted(new_orphans - old_orphans)

if grown:
    print("")
    print("════════════════════════════════════════════════")
    print("✗ 孤儿扫描守卫拦截：出现新零生产消费件（对比入库基线）")
    for m in grown:
        print(f"  + {m}")
    print("  处置：逐件四判据复核；确认合法（如刚迁移完未接线）后")
    print("  更新基线：bash scripts/orphan_gate.sh --write-baseline")
    print("  紧急旁路：LINGCLAUDE_SKIP_ORPHAN_GATE=1 git commit ...（留痕）")
    print("════════════════════════════════════════════════")
    sys.exit(2)

if revived:
    print("[orphan-gate] 候选集收缩（恢复消费/已迁移）：")
    for m in revived:
        print(f"  - {m}")

print(f"[orphan-gate] 增量对比通过：零消费候选 {len(new_orphans)} 件，无新增")
PYEOF
RC=$?

[ $RC -eq 0 ] && touch "$LOCK"
exit $RC
