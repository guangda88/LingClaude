#!/usr/bin/env bash
# credentials_snapshot.sh — W6-①凭证冷备快照（W6-A 核心件）
# 作用: 给 ~/.ling_keys.env（冷启动源，/admin/env 热更原子持久化的落点）
#       做版本化快照 → data/credentials_mirror/，防一次坏写污染冷源无回滚。
# 背景: proxy3 续签链（auth_refresh → refresh_yai_jwt.sh → /admin/env）会周期性
#       重写该文件；一旦某轮续签抓到坏值并落盘，冷启动源即被污染且无历史可回。
# 安全: data/* 已被 .gitignore:30 覆盖，快照永不入库；快照文件 chmod 600。
# 用法:
#   bash scripts/yai/credentials_snapshot.sh            # 做一次快照（含校验）
#   KEEP=24 bash scripts/yai/credentials_snapshot.sh    # 自定义保留份数(默认12)
# 验收: ls data/credentials_mirror/ | tail -3
# 范式: scripts/yai/fix_yai_ws403.sh (宿主执行，同仓内)
set -euo pipefail

SRC="$HOME/.ling_keys.env"
MIRROR_DIR="/home/ai/lingclaude/data/credentials_mirror"
KEEP="${KEEP:-12}"

log(){ printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
die(){ log "FATAL: $*" >&2; exit 1; }

[[ -f "$SRC" ]] || die "冷启动源不存在: $SRC"

# ---- 校验: YAI 三件套必须在且非空（快照没价值的残缺文件不收）----
check_key(){
  local k="$1" v
  v="$(grep -E "^export ${k}=" "$SRC" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\"'"'"' ' | head -c 20)"
  [[ -n "$v" ]] || return 1
  return 0
}
missing=""
for k in YAI_JWT YAI_CSRF_TOKEN YAI_REFRESH_TOKEN; do
  check_key "$k" || missing="$missing $k"
done
if [[ -n "$missing" ]]; then
  die "源文件缺 YAI 凭据:$missing —— 不快照残缺态（可能是坏写/污染，先人工核查 $SRC）"
fi

# ---- 幂等: 与上一份快照逐字节相同则跳过 ----
mkdir -p "$MIRROR_DIR"
chmod 700 "$MIRROR_DIR"
LATEST="$(ls -1t "$MIRROR_DIR"/ling_keys_*.env 2>/dev/null | head -1 || true)"
TS="$(date -u '+%Y%m%dT%H%M%SZ')"
TMP="$MIRROR_DIR/.tmp_ling_keys_$$.env"
trap 'rm -f "$TMP"' EXIT
install -m 600 "$SRC" "$TMP"
if [[ -n "$LATEST" ]] && cmp -s "$TMP" "$LATEST"; then
  log "OK: 内容与最新快照一致，跳过 ($LATEST)"
  exit 0
fi

# ---- 原子落盘 + 轮转 ----
mv "$TMP" "$MIRROR_DIR/ling_keys_$TS.env"
chmod 600 "$MIRROR_DIR/ling_keys_$TS.env"
# 轮转: 只保留最近 KEEP 份
ls -1t "$MIRROR_DIR"/ling_keys_*.env 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
  rm -f "$old"
  log "rotate: 删过期快照 $(basename "$old")"
done
log "OK: 快照落盘 ling_keys_$TS.env (保留最近 $KEEP 份)"
