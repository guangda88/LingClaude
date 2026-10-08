#!/usr/bin/env bash
# scripts/pre_push_gate.sh — full-pytest 门禁网关 v1.0 (2026-10-08)
#
# 背景: 10-07/10-08 推送悬案复盘（见 PUSH_SOP §七）。门禁本体健康，病灶是
#   「无界全量(5591 tests, 实测 ~26-35min) × 各通道短超时(90~880s) × 多路并发」
#   三者绞杀——被外部 timeout 掐死的轮次留下 pytest 孤儿 worker 互抢 CPU，
#   越推越慢；表象(钩子长时间不退出)一度被误诊为「lefthook 不退出 bug」。
# 职责: 保持 lefthook.yml 原 full-pytest 完全相同的测试语义，只补三个工程性质：
#   1) 总预算   —— 超时由网关自己收割（SIGTERM→30s 后 SIGKILL），无孤儿外溢
#   2) 并发串行 —— flock 单飞，多路 push/探针排队而非互杀（10-08 实测双路
#                   并发曾把 8+8 worker 全部拖到 37% CPU）
#   3) 结果复用 —— 同一 HEAD 已绿则秒过（先跑门禁后推送的工作流不再二次全量）
# 环境变量:
#   GATE_TIMEOUT_BUDGET  总预算秒数，默认 2700（实测基线 26min × 1.7 余量）
#   GATE_NO_CACHE=1      强制重跑（忽略已有绿色缓存）
set -u
cd "$(git rev-parse --show-toplevel)" || exit 1

CACHE=".audit/full_pytest_gate.json"
LOCK="${CACHE}.lock"
BUDGET="${GATE_TIMEOUT_BUDGET:-2700}"

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
  if [ "$C_HEAD" = "$HEAD8" ] && [ "$C_RC" = "0" ]; then
    echo "GATE: full-pytest 已在 HEAD=$HEAD8 通过（复用 $(date -r "$CACHE" '+%m-%d %H:%M') 结果），秒过。"
    exit 0
  fi
fi

# ── 1) 总预算执行 ────────────────────────────────────────────────
echo "GATE: 全量 pytest 启动 (HEAD=$HEAD8, 预算 ${BUDGET}s, $(date '+%H:%M:%S'))"
timeout --kill-after=30 "$BUDGET" \
  python3 -m pytest tests/ --tb=line -q --timeout=300 -n "$(nproc)" </dev/null
RC=$?

if [ "$RC" -eq 0 ]; then
  printf '{"head":"%s","rc":0,"ts":"%s"}\n' "$HEAD8" "$(date +%s)" > "$CACHE"
  echo "GATE: PASS（绿色结果已按 HEAD=$HEAD8 缓存）"
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
