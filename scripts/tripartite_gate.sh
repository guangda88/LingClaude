#!/usr/bin/env bash
# 三方对账守卫（2026-09-26，C6）——cycle_report 对账接执法。
#
# 背景：tripartite.py 已有 COVERAGE_RED_BELOW=1.0（「候选归宿覆盖率 <100% 即红，
#   候选消失即红」）与 STALE_AFTER_HOURS=168 观测常量，但全仓无任何 hook 调用
#   对账面——「有数据无执法」。本守卫把对账从仪表层升为门禁。
#
# 触发：staged 触碰 data/arch_ledger/arch_tripartite_cycle/（对账面写保护）。
# 检查（对每份 cycle record）：
#   1) 覆盖率：candidates 全员必须有 disposition（pending/缺失即红——覆盖率<100%）；
#   2) 证据实存：disposition≠pending 的 evidence 路径必须存在（编造证据=红）；
#   3) 聚合一致：cycle_report 各档计数必须等于 candidates 实数（候选消失=红）；
#   4) 新鲜度：record 距今 >168h = advisory（账本死兆，不拦提交——拦了就没人
#      能提交去刷新它，红应打在刷新账本的动作上）。
#
# 旁路：LINGCLAUDE_SKIP_TRIPARTITE_GATE=1（显式留痕）。
set -u

if [ "${LINGCLAUDE_SKIP_TRIPARTITE_GATE:-0}" = "1" ]; then
  echo "[tripartite-gate] skipped (LINGCLAUDE_SKIP_TRIPARTITE_GATE=1)"
  exit 0
fi

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
cd "$ROOT" || exit 0

KEY="$( (git rev-parse HEAD 2>/dev/null; git diff --cached --no-ext-diff 2>/dev/null) | md5sum | cut -d' ' -f1)"
LOCK="/tmp/.tripartite_gate_${KEY:0:12}"
if [ -f "$LOCK" ]; then
  echo "[tripartite-gate] same staged content already gated, skip duplicate"
  exit 0
fi

STAGED="$(git diff --cached --name-only -- data/arch_ledger/arch_tripartite_cycle/ 2>/dev/null)"
if [ -z "$STAGED" ]; then
  exit 0
fi

PY="${PYTHON:-python3}"
"$PY" - <<'PYEOF'
import json, sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path.cwd()
CYCLE_DIR = ROOT / "data/arch_ledger/arch_tripartite_cycle"
STALE_AFTER_HOURS = 168
fail, advisory = [], []

if not CYCLE_DIR.exists():
    print("[tripartite-gate] 对账目录不存在，跳过")
    sys.exit(0)

now = datetime.now()
for rec_path in sorted(CYCLE_DIR.glob("*.json")):
    try:
        rec = json.load(open(rec_path))
    except Exception as e:
        fail.append(f"{rec_path.name}: JSON 解析失败 {e}")
        continue
    cyc = rec.get("cycle", {})
    cands = cyc.get("candidates", [])
    if not cands:
        fail.append(f"{rec_path.name}: candidates 为空——覆盖率 0%")
        continue

    # 1) 覆盖率：全员必须有归宿
    undisposed = [c["item"] for c in cands
                  if c.get("disposition", "pending") in ("pending", "", None)]
    if undisposed:
        fail.append(f"{rec_path.name}: 覆盖率<100%（{len(undisposed)} 件无归宿）：{undisposed[:5]}")

    # 2) 证据实存
    fake = []
    for c in cands:
        ev = c.get("evidence", "")
        if ev and not (ROOT / ev).exists():
            fake.append(f"{c['item']} → {ev}")
    if fake:
        fail.append(f"{rec_path.name}: evidence 路径不存在（编造证据）：{fake[:5]}")

    # 3) 聚合一致：cycle_report 计数 vs candidates 实数
    report = cyc.get("cycle_report", {})
    actual = {}
    for c in cands:
        d = c.get("disposition", "pending") or "pending"
        actual[d] = actual.get(d, 0) + 1
    for tier, n in report.items():
        if n != actual.get(tier, 0):
            fail.append(f"{rec_path.name}: cycle_report.{tier}={n} 但实际 {actual.get(tier, 0)}（候选消失/虚报）")
    missing_tiers = {k: v for k, v in actual.items() if k not in report}
    if missing_tiers:
        fail.append(f"{rec_path.name}: cycle_report 漏档 {missing_tiers}")

    # 4) 新鲜度 advisory
    created = rec.get("created") or cyc.get("as_of", "")
    try:
        t = datetime.fromisoformat(created)
        age_h = (now - t).total_seconds() / 3600
        if age_h > STALE_AFTER_HOURS:
            advisory.append(f"{rec_path.name}: 距上次对账 {age_h:.0f}h >{STALE_AFTER_HOURS}h（账本死兆，去刷新对账）")
    except Exception:
        pass

    if not fail:
        n = len(cands)
        print(f"[tripartite-gate] {rec_path.name}: 覆盖率 100%（{n} 件全归宿），聚合一致")

for a in advisory:
    print(f"[tripartite-gate] ⚠ advisory: {a}")

if fail:
    print("")
    print("════════════════════════════════════════════════")
    print("✗ 三方对账守卫拦截：")
    for f in fail:
        print(f"  ✗ {f}")
    print("  COVERAGE_RED_BELOW=1.0 红线执法：候选全归宿+证据实存+聚合一致")
    print("  紧急旁路：LINGCLAUDE_SKIP_TRIPARTITE_GATE=1 git commit ...（留痕）")
    print("════════════════════════════════════════════════")
    sys.exit(2)
PYEOF
RC=$?

[ $RC -eq 0 ] && touch "$LOCK"
exit $RC
