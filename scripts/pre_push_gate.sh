#!/usr/bin/env bash
# scripts/pre_push_gate.sh — full-pytest 门禁网关 v2.0 (2026-10-28)
#
# 背景: 10-07/10-08 推送悬案复盘（见 PUSH_SOP §七）。门禁本体健康，病灶是
#   「无界全量(5591 tests, 实测 ~26-35min) × 各通道短超时(90~880s) × 多路并发」
#   三者绞杀——被外部 timeout 掐死的轮次留下 pytest 孤儿 worker 互抢 CPU，
#   越推越慢；表象(钩子长时间不退出)一度被误诊为「lefthook 不退出 bug」。
# 职责: 保持 lefthook.yml 原 full-pytest 完全相同的测试语义，只补四个工程性质：
#   1) 总预算   —— 超时由网关自己收割（SIGTERM→30s 后 SIGKILL），无孤儿外溢
#   2) 并发串行 —— flock 单飞，多路 push/探针排队而非互杀（10-08 实测双路
#                   并发曾把 8+8 worker 全部拖到 37% CPU）
#   3) 结果复用 —— 同一 HEAD 已绿则秒过（先跑门禁后推送的工作流不再二次全量）
#   4) 已知失败基线豁免 —— v2.0 (2026-10-28)。RC=1 时失败集 ⊆ 入库基线
#     (data/arch_ledger/test_baselines/push_gate_known_failures.json，每条带
#     reason+verified_head，棘轮只缩不放) 则豁免放行；新红/数据异常/超时杀
#     (124/137) 一律维持严格拒绝。修复背景：10-09 后 HEAD 积累 ~16 条历史失败
#     （10-27 W3 甄别闭环），v1 缓存从未绿过 → push 结构性被挡 18 天，10-27
#     被迫走 §三 旁路。豁免判定器=scripts/gate_exemption_check.py。
# 环境变量:
#   GATE_TIMEOUT_BUDGET  总预算秒数，默认 2700（实测基线 26min × 1.7 余量）
#   GATE_NO_CACHE=1      强制重跑（忽略已有绿色缓存）
#   GATE_BASELINE        豁免基线路径覆盖（默认入库基线；置空串禁用豁免回 v1 语义）
set -u
cd "$(git rev-parse --show-toplevel)" || exit 1

CACHE=".audit/full_pytest_gate.json"
LOCK="${CACHE}.lock"
BUDGET="${GATE_TIMEOUT_BUDGET:-2700}"
GATE_BASELINE="${GATE_BASELINE-data/arch_ledger/test_baselines/push_gate_known_failures.json}"
# 豁免判定数据源：pytest cacheprovider lastfailed（内建，无需插件）。
# 专属 cache_dir 与默认 .pytest_cache 隔离——开发态 lastfailed 不污染门禁判定，
# 并发会话亦不互踩（配合 flock 双保险）。加入 .gitignore（临时数据）。
GATE_CACHE_DIR=".audit/pytest_gate_cache"

mkdir -p .audit

# ── 2) 并发串行 ──────────────────────────────────────────────────
exec 9>"$LOCK"
if ! flock -w "$BUDGET" 9; then
  echo "GATE: 等待门禁锁超时(${BUDGET}s)——另一全量仍在跑。旁路见 PUSH_SOP §三。" >&2
  exit 1
fi

# ── 3) 结果复用（按 HEAD 键：门禁守护的对象就是将被推送的提交内容；
#       工作区未提交改动由 pre-commit 快子集负责，分工见 PUSH_SOP）──
HEAD8=$(git rev-parse --short=8 HEAD)
if [ "${GATE_NO_CACHE:-0}" != "1" ] && [ -f "$CACHE" ]; then
  C_HEAD=$(python3 -c "import json;print(json.load(open('$CACHE'))['head'])" 2>/dev/null || echo "")
  C_RC=$(python3 -c "import json;print(json.load(open('$CACHE'))['rc'])" 2>/dev/null || echo "")
  C_EX=$(python3 -c "import json;print(json.load(open('$CACHE')).get('exempted',''))" 2>/dev/null || echo "")
  if [ "$C_HEAD" = "$HEAD8" ] && [ "$C_RC" = "0" ]; then
    echo "GATE: full-pytest 已在 HEAD=$HEAD8 通过（复用 $(date -r "$CACHE" '+%m-%d %H:%M') 结果），秒过。"
    exit 0
  fi
  # v2.0: 豁免放行结果同样按 HEAD 复用（基线为入库文件，同 HEAD=同基线语义）；
  # 本轮显式禁用豁免(GATE_BASELINE="")则不复用豁免缓存，回归严格语义。
  if [ "$C_HEAD" = "$HEAD8" ] && [ "$C_RC" = "1" ] && [ "$C_EX" = "True" ] && [ -n "$GATE_BASELINE" ]; then
    echo "GATE: HEAD=$HEAD8 已按已知失败基线豁免放行（复用 $(date -r "$CACHE" '+%m-%d %H:%M') 结果），秒过。"
    exit 0
  fi
fi

# ── 1) 总预算执行 ────────────────────────────────────────────────
mkdir -p "$GATE_CACHE_DIR"
echo "GATE: 全量 pytest 启动 (HEAD=$HEAD8, 预算 ${BUDGET}s, $(date '+%H:%M:%S'))"
timeout --kill-after=30 "$BUDGET" \
  python3 -m pytest tests/ --tb=line -q --timeout=300 -n "$(nproc)" \
  -o cache_dir="$GATE_CACHE_DIR" </dev/null
RC=$?

if [ "$RC" -eq 0 ]; then
  printf '{"head":"%s","rc":0,"ts":"%s"}\n' "$HEAD8" "$(date +%s)" > "$CACHE"
  echo "GATE: PASS（绿色结果已按 HEAD=$HEAD8 缓存）"
elif [ "$RC" -eq 1 ] && [ -n "$GATE_BASELINE" ]; then
  # ── v2.0 已知失败基线豁免（仅真实测试红；数据异常由判定器回退严格语义）──
  LASTFAILED="$GATE_CACHE_DIR/v/cache/lastfailed"
  if [ ! -f "$LASTFAILED" ]; then
    echo "GATE: FAIL——pytest exit=1 且 lastfailed 缺失（疑数据面异常），维持严格拒绝" >&2
    exit 1
  fi
  # ⚠ 不得写成 `if 判定器; then`——bash 的 if 在条件假且无 else 时返回 0，
  # 会吞掉判定器的非零退出码（2026-10-28 E2E 实测 EXEMPT_DENIED 却 RC=0 的放行漏洞）。
  python3 scripts/gate_exemption_check.py "$GATE_BASELINE" < "$LASTFAILED"
  RC_EX=$?
  if [ "$RC_EX" -eq 0 ]; then
    # 豁免放行：按 HEAD 缓存（exempted=True），同 HEAD 第二远程秒过
    printf '{"head":"%s","rc":1,"exempted":true,"ts":"%s"}\n' "$HEAD8" "$(date +%s)" > "$CACHE"
    exit 0
  fi
  exit "$RC_EX"
else
  rm -f "$CACHE"
  if [ "$RC" -eq 124 ] || [ "$RC" -eq 137 ]; then
    echo "GATE: FAIL——全量超出预算 ${BUDGET}s，已由网关收割(exit=$RC)。" >&2
    echo "      高负载时段属环境病，处理路径见 PUSH_SOP §三（旁路需留痕）。" >&2
  else
    echo "GATE: FAIL——pytest exit=$RC（真实红，修复后再推）" >&2
  fi
fi
exit "$RC"
