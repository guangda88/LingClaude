#!/usr/bin/env bash
# pre-push 资源预检门（push-gate-host-resource-precheck ②，2026-09-30）
#
# 机制化 safe_push.sh 的 L3 警告：宿主内存余量不足时，全量 pytest -n8
# 会触发 earlyoom 成批杀 worker / swap 抖动，环境病被误报为代码红
# （09-24 push-gate-xdist-spin 三次 push 事故实录）。
#
# 退出码语义：
#   0 = 资源充足，放行
#   1 = MemAvailable < 4G，结构性挡门（按 PUSH_SOP §三 旁路须显式留痕）
#   2 = 读取失败/环境异常（/proc/meminfo 缺失等），保守放行并告警
#
# 测试缝（仅测试用）：MEMINFO_FILE / MIN_AVAIL_KB 可环境变量覆盖，
# 生产默认 /proc/meminfo + 4G 不变——债单④"资源紧张状态"的模拟锚点。
set -u

MEMINFO_FILE=${MEMINFO_FILE:-/proc/meminfo}
MIN_AVAIL_KB=${MIN_AVAIL_KB:-4194304}  # 4G，与 safe_push.sh L3、PUSH_SOP §二.1a 同一口径

AVAIL_KB=$(awk '/MemAvailable/{print $2; exit}' "$MEMINFO_FILE" 2>/dev/null || echo "")
if [ -z "$AVAIL_KB" ]; then
  echo "[resource-gate] WARN: 无法读取 MemAvailable（非 Linux 或 /proc 不可用），保守放行" >&2
  exit 2
fi

if [ "$AVAIL_KB" -lt "$MIN_AVAIL_KB" ]; then
  AVAIL_MB=$((AVAIL_KB / 1024))
  echo "[resource-gate] FAIL: MemAvailable=${AVAIL_MB}MB < 4G（阈值 4194304KB）" >&2
  echo "[resource-gate] 全量门禁(-n\$(nproc))在此状态下会成批杀 worker，环境病误报代码红。" >&2
  echo "[resource-gate] 处置：① 等资源空闲后重推；② 按 PUSH_SOP §三 旁路（LINGCLAUDE_SKIP_GATE 类变量显式留痕）。" >&2
  exit 1
fi

AVAIL_MB=$((AVAIL_KB / 1024))
echo "[resource-gate] OK: MemAvailable=${AVAIL_MB}MB >= 4G，放行全量门禁"
exit 0
