#!/usr/bin/env bash
# 红名单棘轮守卫（2026-09-26，C5）——闸门1/2/4 一体机检。
#
# 闸门1 单向棘轮状态机（data/arch_ledger/redlist_state.json）：
#   - stage 只进不退：in_core → migrated → removed；
#   - 台账记 removed/migrated 而 core/ 文件又出现（无 err_case 或案号非 ERR-2026-
#     前缀）= 回退，红；
#   - staged 删除红名单件但台账未升格 = 「迁移不记账」，红（迁移与记账同 commit）。
# 闸门2 双轨互斥显式化（data/arch_ledger/dual_track_registry.json）：
#   - AST 顶层符号判据：core/ 与 plugins|engine/ 同符号 ≥2 个重叠 = 疑似双轨，
#     未在注册表声明 serving_side 即红（单符号泛名重叠视为协议噪音，不报）；
#   - 同名文件但符号零重叠（如 core/hooks.py 的 HookManager 系 vs
#     engine/loop/hooks.py 的 LoopHooks 系）不算双轨。
# 闸门4 清零期限（clear_deadline=2026-12-31）：
#   - 到期后仍有 in_core 存量 = 红；期限内报余量与剩余天数（advisory）。
#
# 基线：arch_m3_redlist_baseline/baseline-ff17eb2.json（口径 B，主干九件除外）。
# 旁路：LINGCLAUDE_SKIP_REDLIST_GATE=1（显式留痕）。
set -u

if [ "${LINGCLAUDE_SKIP_REDLIST_GATE:-0}" = "1" ]; then
  echo "[redlist-gate] skipped (LINGCLAUDE_SKIP_REDLIST_GATE=1)"
  exit 0
fi

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
cd "$ROOT" || exit 0

KEY="$( (git rev-parse HEAD 2>/dev/null; git diff --cached --no-ext-diff 2>/dev/null) | md5sum | cut -d' ' -f1)"
LOCK="/tmp/.redlist_gate_${KEY:0:12}"
if [ -f "$LOCK" ]; then
  echo "[redlist-gate] same staged content already gated, skip duplicate"
  exit 0
fi

# 仅当 staged 触碰 core/ 或 plugins|engine/（双轨可能从任意侧产生）才触发
STAGED="$(git diff --cached --name-only -- lingclaude/core/ lingclaude/plugins/ lingclaude/engine/ 2>/dev/null)"
if [ -z "$STAGED" ]; then
  exit 0
fi
STAGED_DELETED="$(git diff --cached --name-only --diff-filter=D -- lingclaude/core/ 2>/dev/null)"

PY="${PYTHON:-python3}"
"$PY" - "$STAGED" "$STAGED_DELETED" <<'PYEOF'
import ast, json, sys
from datetime import date
from pathlib import Path

ROOT = Path.cwd()
staged = set(sys.argv[1].split())
staged_deleted = {p for p in sys.argv[2].split() if p}
BASELINE = ROOT / "data/arch_ledger/arch_m3_redlist_baseline/baseline-ff17eb2.json"
STATE = ROOT / "data/arch_ledger/redlist_state.json"
REGISTRY = ROOT / "data/arch_ledger/dual_track_registry.json"
CORE = ROOT / "lingclaude/core"

baseline = json.load(open(BASELINE))
state = json.load(open(STATE))
registry = json.load(open(REGISTRY))

redlist = set()
for raw in baseline.get("redlist", []):
    name = raw.split(" ")[0].strip()
    if name and name != "__init__":
        redlist.add(name)
trunk = set(baseline.get("trunk", []))
items = state.get("items", {})

fail = []

# ── 闸门1：状态机 ──
# 1a) 台账已出 core 的件，文件又回来了 = 回退
for name, rec in items.items():
    if rec.get("stage") in ("migrated", "removed"):
        f = CORE / f"{name}.py"
        if f.exists():
            err = rec.get("err_case", "")
            if not err.startswith("ERR-2026-"):
                fail.append(f"闸门1回退：{name} 台账 stage={rec['stage']} 但 core/ 文件又出现，且无有效 ERR 案号")

# 1b) staged 删除红名单件但台账仍 in_core = 迁移不记账
for p in staged_deleted:
    name = Path(p).stem
    if name in redlist and items.get(name, {}).get("stage", "in_core") == "in_core":
        fail.append(f"闸门1迁移不记账：{name} staged 删除但台账仍 in_core——同 commit 更新 redlist_state.json（stage=migrated/removed + evidence）")

# ── 闸门4：清零期限 ──
deadline = date.fromisoformat(state.get("clear_deadline", "2026-12-31"))
today = date.today()
in_core = [m for m in sorted(redlist) if items.get(m, {}).get("stage", "in_core") == "in_core" and (CORE / f"{m}.py").exists()]
if today > deadline and in_core:
    fail.append(f"闸门4清零期限已过（{deadline}）仍有 {len(in_core)} 件 in_core：{in_core[:5]}…")
else:
    days = (deadline - today).days
    print(f"[redlist-gate] 闸门4：红名单存量 {len(in_core)} 件，距清零期限 {deadline} 还有 {days} 天")

# ── 闸门2：双轨 AST 符号级检查 ──
def top_syms(p: Path):
    try:
        tree = ast.parse(p.read_text())
    except Exception:
        return set()
    return {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}

registered = {(o["symbol"], o["core_file"]) for o in registry.get("overlaps", [])}

core_files = [p for p in CORE.glob("*.py") if not p.name.endswith(".bak")]
other_files = []
for base in (ROOT / "lingclaude/plugins", ROOT / "lingclaude/engine"):
    other_files += [p for p in base.rglob("*.py") if "__pycache__" not in str(p)]
other_syms = {p: top_syms(p) for p in other_files}

dual_hits = []
for cf in core_files:
    cs = top_syms(cf)
    if not cs:
        continue
    for of, osyms in other_syms.items():
        shared = cs & osyms
        if len(shared) >= 2:  # 单符号泛名（register/main 等）视为协议噪音
            for s in sorted(shared):
                rel_c = str(cf.relative_to(ROOT))
                if (s, rel_c) not in registered:
                    dual_hits.append(f"闸门2隐式双轨：符号 {s} 同时在 {rel_c} 与 {str(of.relative_to(ROOT))}（未注册 serving_side）")

fail.extend(dual_hits)

if fail:
    print("")
    print("════════════════════════════════════════════════")
    print("✗ 红名单棘轮守卫拦截：")
    for f in fail:
        print(f"  ✗ {f}")
    print("  处置：迁移与记账同 commit / 回退挂 ERR-2026- 案号 /")
    print("  双轨登记 dual_track_registry.json 并声明 serving_side")
    print("  紧急旁路：LINGCLAUDE_SKIP_REDLIST_GATE=1 git commit ...（留痕）")
    print("════════════════════════════════════════════════")
    sys.exit(2)

print(f"[redlist-gate] 闸门1/2/4 全过（双轨扫描 {len(core_files)}×{len(other_files)} 文件，注册表 {len(registered)} 条）")
PYEOF
RC=$?

[ $RC -eq 0 ] && touch "$LOCK"
exit $RC
