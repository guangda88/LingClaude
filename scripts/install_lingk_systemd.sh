#!/usr/bin/env bash
# install_lingk_systemd.sh — W1: 灵克四服务一键装配 (user 级 systemd, 幂等)
# 装配内容:
#   1. lingk-webui.service  (23458 webui-server, 本仓新增——四服务中此前唯一无托管的)
#   2. lingk.target         (聚合 proxy3 8765 / lingmemory 9530 / webui 23458 / codearts-bridge 4110)
# 用法:   bash /home/ai/lingclaude/scripts/install_lingk_systemd.sh
# 卸载:   systemctl --user disable --now lingk.target lingk-webui.service
# 验收:   systemctl --user is-active lingk.target && python3 scripts/bootstrap_env.py (宿主跑, 应 4/4 UP)
# 范式:   scripts/install_relay_systemd.sh (R9-1, 已实战验证)
set -euo pipefail

REPO=/home/ai/lingclaude
UNIT_DIR="$REPO/scripts/systemd"
DST_DIR="$HOME/.config/systemd/user"

log(){ printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
die(){ log "FATAL: $*" >&2; exit 1; }

# ---- 前置检查 ----
[[ -f "$UNIT_DIR/lingk-webui.service" ]] || die "unit 缺失: $UNIT_DIR/lingk-webui.service"
[[ -f "$UNIT_DIR/lingk.target" ]] || die "target 缺失: $UNIT_DIR/lingk.target"
command -v systemctl >/dev/null || die "systemctl 不可用(非 systemd 宿主?)"

BIN="$REPO/webui-server/target/release/lingclaude-webui"
if [[ ! -x "$BIN" ]]; then
  die "webui 二进制未编译: $BIN
     构建方法: cd $REPO/webui-server && cargo build --release  (首次约 2-5 分钟)
     然后重跑本脚本"
fi

# 前端新鲜度: dist 经 rust-embed 编进二进制(webui.rs:12), 源码新于二进制=嵌的是旧前端
stale_front="$(find "$REPO/webui/src" -newer "$BIN" 2>/dev/null | wc -l)"
if [[ "$stale_front" -gt 0 ]]; then
  log "⚠ 前端源码有 $stale_front 个文件新于二进制——内嵌 dist 可能过期。
     重建: cd $REPO/webui && npm run build && cd ../webui-server && cargo build --release"
fi

system_state="$(systemctl --user is-system-running 2>/dev/null || true)"
case "$system_state" in
  running)  : ;;
  degraded) log "⚠ systemd user 实例 degraded(存在失败单元)——不影响本装配, 继续" ;;
  *) die "systemd user 实例不可达(state=${system_state:-unknown})。自诊断:
     ① 重登 SSH(让 pam_systemd 注入 XDG_RUNTIME_DIR), 或:
        export XDG_RUNTIME_DIR=/run/user/\$(id -u) DBUS_SESSION_BUS_ADDRESS=unix:path=\$XDG_RUNTIME_DIR/bus
     ② ps aux | grep 'systemd --user'; ③ ls /run/user/" ;;
esac

if ! loginctl show-user "$USER" --property=Linger 2>/dev/null | grep -q '=yes'; then
  log "⚠ Linger 未开启: 用户未登录时单元不运行。无人值守请: sudo loginctl enable-linger $USER"
fi

# ---- 装配 (幂等: 覆盖拷贝 + enable --now 可重复执行) ----
mkdir -p "$DST_DIR" "$REPO/logs"
install -m 644 "$UNIT_DIR/lingk-webui.service" "$DST_DIR/"
install -m 644 "$UNIT_DIR/lingk.target" "$DST_DIR/"
# ---- W6 凭证冷备: 保证镜像里至少一份当前凭据快照(重装/换机恢复点) ----
# 链路: /admin/env 热更原子持久化写 ~/.ling_keys.env (a_l10d_operation_gate.py:48);
#       快照给冷源做版本化防污染(一次坏写可回滚)。详见 scripts/yai/README.md。
if [[ -f "$HOME/.ling_keys.env" ]] && [[ -x "$REPO/scripts/yai/credentials_snapshot.sh" ]]; then
  log "→ 凭证冷备快照(内容无新变化自动跳过)"
  bash "$REPO/scripts/yai/credentials_snapshot.sh" || log "⚠ 快照失败(不阻塞装配, 可手动重跑)"
else
  log "⚠ 跳过冷备快照(源或脚本缺失)——建议宿主手动: bash $REPO/scripts/yai/credentials_snapshot.sh"
fi

systemctl --user daemon-reload
systemctl --user enable --now lingk-webui.service
systemctl --user enable --now lingk.target

sleep 1
log "── 状态 ──"
systemctl --user --no-pager is-active lingk-webui.service lingk.target || true
log "装配完成。逐服务健康(宿主跑): python3 $REPO/scripts/bootstrap_env.py"
log "卸载: systemctl --user disable --now lingk.target lingk-webui.service && rm $DST_DIR/lingk{.target,-webui.service}"
