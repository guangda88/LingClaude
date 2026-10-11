#!/usr/bin/env bash
# install_relay_systemd.sh — R9-1 编排器 systemd 托管一键装配(user 级)
# 前置: systemd user 实例可用; 建议 Linger=yes(否则用户未登录时单元不跑, 脚本会提示)
# 用法: bash /home/ai/lingclaude/scripts/install_relay_systemd.sh
# 卸载: systemctl --user disable --now ling-relay-orchestrator.service
set -euo pipefail

REPO=/home/ai/lingclaude
UNIT_SRC="$REPO/scripts/systemd/ling-relay-orchestrator.service"
UNIT_DST="$HOME/.config/systemd/user/ling-relay-orchestrator.service"
LOG_DIR="$REPO/logs"
QUEUE_FILE="$REPO/scripts/relay_queue.json"

log(){ printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
die(){ log "FATAL: $*" >&2; exit 1; }

[[ -f "$UNIT_SRC" ]] || die "unit 源文件缺失: $UNIT_SRC"
command -v systemctl >/dev/null || die "systemctl 不可用(非 systemd 宿主?)"
system_state="$(systemctl --user is-system-running 2>/dev/null || true)"
case "$system_state" in
  running)  : ;;
  degraded) log "⚠ systemd user 实例为 degraded(存在失败单元)——不影响本单元安装/运行, 继续";;
  *) die "systemd user 实例不可达(state=${system_state:-unknown})。自诊断:
     ① 重登 SSH(让 pam_systemd 注入 XDG_RUNTIME_DIR), 或手动: export XDG_RUNTIME_DIR=/run/user/\$(id -u) DBUS_SESSION_BUS_ADDRESS=unix:path=\$XDG_RUNTIME_DIR/bus
     ② 看 user manager 是否存活: ps aux | grep 'systemd --user'
     ③ 看 /run/user/ 是否有 \$(id -u)";;
esac
mkdir -p "$HOME/.config/systemd/user" "$LOG_DIR"
[[ -f "$QUEUE_FILE" ]] || printf '[]\n' > "$QUEUE_FILE"   # 队列不存在则建空队列

# Linger 检查: 未登录也要跑(无人值守核心), Linger=no 时只告警不阻断
if ! loginctl show-user "$USER" --property=Linger 2>/dev/null | grep -q '=yes'; then
  log "⚠ Linger 未开启: 用户未登录时单元不会运行。无人值守请执行: sudo loginctl enable-linger $USER"
fi

cp "$UNIT_SRC" "$UNIT_DST"
chmod 644 "$UNIT_DST"

systemctl --user daemon-reload
systemctl --user enable --now ling-relay-orchestrator.service
sleep 1
systemctl --user --no-pager status ling-relay-orchestrator.service | head -8 || true
log "装配完成。日志: journalctl --user -u ling-relay-orchestrator -f 或 tail -f $LOG_DIR/relay_orchestrator.log"
log "验收(AC): kill -9 \$(pgrep -f session_relay_orchestrator) && sleep 12 && systemctl --user is-active ling-relay-orchestrator  # 应 active"
