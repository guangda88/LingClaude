#!/usr/bin/env bash
# stub_lingclaude.sh — R9 三闸 E2E 测试用假 lingclaude（只用于测试，勿用于生产）
# 用法: stub_lingclaude.sh run --print "<prompt>"
# 角色按 prompt 特征自适应（同一 stub 可同时扮演三种会话）:
#   prompt 含 GOAL_DRIFT_VERDICT.txt → 漂移复核官: 写 STUB_DRIFT(默认 on_track)
#   prompt 含 VERDICT.txt            → 验收官:     写 STUB_ACCEPT(默认 accept)
#   prompt 含 RELAY_MARKER 写入指令   → 接力会话:   写 RELAY_MARKER=STUB_RELAY_MARK(默认 completed)
# 变量: STUB_SLEEP=秒(模拟耗时) STUB_RC=非0(模拟会话崩溃) STUB_ACCEPT/STUB_DRIFT/STUB_RELAY_MARK
set -u
prompt="${3:-}"
case "$prompt" in
  *GOAL_DRIFT_VERDICT.txt*)
    verdict=$(grep -oE '/[^ ]+GOAL_DRIFT_VERDICT\.txt' <<<"$prompt" | head -1)
    [[ -n "$verdict" ]] && printf '%s\n' "${STUB_DRIFT:-on_track}" > "$verdict"
    exit 0 ;;
  *VERDICT.txt*)
    verdict=$(grep -oE '/[^ ]+VERDICT\.txt' <<<"$prompt" | head -1)
    [[ -n "$verdict" ]] && printf '%s\n' "${STUB_ACCEPT:-accept}" > "$verdict"
    exit 0 ;;
  *"RELAY_MARKER 文件"*)
    sleep "${STUB_SLEEP:-0}"
    [[ "${STUB_RC:-0}" != "0" ]] && exit "$STUB_RC"
    state_dir=$(grep -oE '/[^ ]*state' <<<"$prompt" | head -1)
    if [[ -n "$state_dir" && -d "$state_dir" ]]; then
      printf '%s\n' "${STUB_RELAY_MARK:-completed}" > "$state_dir/RELAY_MARKER"
    fi
    exit 0 ;;
esac
exit 0
