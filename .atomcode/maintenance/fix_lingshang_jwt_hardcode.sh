#!/bin/bash
# fix_lingshang_jwt_hardcode.sh v2 — 修复 lingshang-api.service 硬编码 YAI_JWT（过期67天）
# 模式：仿 lingflow-proxy3.service EVOLUTION_LOG #099 的动态注入方案
#   - 删除 unit 中硬编码的 YAI_JWT / YAI_CSRF_TOKEN 行（先备份，可回滚）
#   - drop-in override: ExecStartPre 从 ~/.ling_keys.env 动态提取最新凭证 → 独立 env 文件 (chmod 600)
#   - 附加：lingflow-jwt-refresh.service 同款去硬编码（ADMIN_KEY）
#
# v2 修复（2026-10-04 晚，灵克自纠）：
#   [bug-1] v1 在 step6 用 `grep -c | awk` 验证 unit 无密钥；修复成功时 grep 无匹配
#           exit 1，叠加 set -o pipefail + set -e，脚本在"确认成功"一刻被杀死，
#           导致 --with-refresher 分支永不执行。→ 改用 if grep -q 判定。
#   [bug-2] v1 refresher 分支用纯 override 注入 ADMIN_KEY——但 drop-in 无法删除
#           原 unit 的 Environment= 行，硬编码仍留在磁盘。→ 升级为完整三步。
#   [bug-3] refresher 直接复用 .ling_keys.systemd.env 时，ExecStartPre 若写
#           `grep ... > 同一文件` 会先截断目标，把 proxy3 的 key 文件清空！
#           → 源==目标的场景不生成 ExecStartPre，仅挂 EnvironmentFile。
#
# 用法：
#   bash fix_lingshang_jwt_hardcode.sh --refresher-only   # 只补修 jwt-refresh（不重启8771）
#   bash fix_lingshang_jwt_hardcode.sh                    # 主修复（lingshang-api），幂等可重跑
#   bash fix_lingshang_jwt_hardcode.sh --with-refresher   # 主修复 + refresher
set -euo pipefail

UNIT="$HOME/.config/systemd/user/lingshang-api.service"
BAK="$UNIT.bak_20261004"          # 原始备份（唯一，幂等：已存在不覆盖）
BAK2="$HOME/lingshang-api.service.bak_20261004.pre"
OVERRIDE_DIR="$UNIT.d"
LINGS_ENV="$HOME/.ling_keys.lingshang.env"
KEYS_ENV="$HOME/.ling_keys.env"

REF_UNIT="$HOME/.config/systemd/user/lingflow-jwt-refresh.service"
REF_BAK="$REF_UNIT.bak_20261004"
REF_OVERRIDE_DIR="$REF_UNIT.d"
SYSTEMD_ENV="$HOME/.ling_keys.systemd.env"

# ---------- 幂等备份：原始版只留一份，修复前当前版也留一份 ----------
backup_unit() {  # $1=unit路径  $2=原始备份路径  $3=修复前备份路径
    local unit="$1" orig="$2" cur="$3"
    if [ -f "$orig" ]; then
        echo "ℹ️ 原始备份已存在，跳过覆盖: $orig"
    else
        cp -p "$unit" "$orig"
        echo "✅ 原始备份: $orig"
    fi
    if [ ! -f "$cur" ] && grep -qE '^Environment=(YAI_JWT|PROXY3_ADMIN_KEY)=' "$unit"; then
        cp -p "$unit" "$cur"
        echo "✅ 修复前版本备份: $cur"
    fi
}

# ---------- 通用：删密钥行 + 写动态注入 override ----------
# $1=unit $2=密钥行egrep模式 $3=override目录 $4=env文件 $5=提取egrep模式(空串=env文件由外部维护，无需ExecStartPre)
strip_and_override() {
    local unit="$1" pat="$2" odir="$3" envf="$4" extract="$5"
    sed -i -E "/^Environment=($pat)=/d" "$unit"
    if grep -qE "^Environment=($pat)=" "$unit"; then
        echo "❌ 密钥行删除失败，中止"; return 1
    fi
    mkdir -p "$odir"
    # [bug-3] 源==目标时绝不能生成 `grep ... > 同一文件`（会先截断清空源文件）
    {
        echo "# 2026-10-04 去硬编码修复（灵克 v2）：凭证不固化在 unit 文件。"
        echo "# 由 EnvironmentFile 加载 $envf (mode 600)。"
        echo "# 回滚：rm -rf $odir && cp -p \$(dirname $unit)/\$(basename $unit).bak_20261004 $unit && systemctl --user daemon-reload"
        echo "[Service]"
        echo "EnvironmentFile=-$envf"
        if [ -n "$extract" ]; then
            echo "ExecStartPre=/bin/bash -c 'grep -E \"$extract\" \"\$HOME/.ling_keys.env\" > $envf; chmod 600 $envf'"
        fi
    } > "$odir/override.conf"
    echo "✅ override 已写入: $odir/override.conf"
}

# ---------- 模式分支：--refresher-only（不碰 8771） ----------
if [ "${1:-}" = "--refresher-only" ]; then
    echo "==== [仅修 lingflow-jwt-refresh.service] ===="
    [ -f "$REF_UNIT" ] || { echo "❌ 找不到 $REF_UNIT"; exit 1; }
    if [ ! -f "$SYSTEMD_ENV" ] || ! grep -q 'PROXY3_ADMIN_KEY' "$SYSTEMD_ENV"; then
        echo "❌ $SYSTEMD_ENV 缺失或无 PROXY3_ADMIN_KEY（需 lingflow-proxy3 启动过一次）"; exit 1
    fi
    backup_unit "$REF_UNIT" "$REF_BAK" "${REF_BAK}.pre"
    # 源==目标（.ling_keys.systemd.env 本身就是 proxy3 维护的成品），extract 传空串
    strip_and_override "$REF_UNIT" 'PROXY3_ADMIN_KEY' "$REF_OVERRIDE_DIR" "$SYSTEMD_ENV" ''
    systemctl --user daemon-reload
    echo "✅ 完成。oneshot 服务，下次 timer 触发自动用新机制（无需手动 restart）。"
    exit 0
fi

# ---------- 主修复：lingshang-api.service ----------
echo "==== [0/6] 前置检查 ===="
[ -f "$UNIT" ] || { echo "❌ 找不到 $UNIT"; exit 1; }
[ -f "$KEYS_ENV" ] || { echo "❌ 找不到 $KEYS_ENV"; exit 1; }
grep -qE '^export YAI_JWT=' "$KEYS_ENV" || { echo "❌ $KEYS_ENV 中无 YAI_JWT 行"; exit 1; }
if grep -qE '^Environment=YAI_JWT=' "$UNIT"; then
    IDEMPOTENT=0
else
    IDEMPOTENT=1
    echo "ℹ️ unit 中已无硬编码 YAI_JWT（v1 已修复过），跳过 1-5 步，不重复重启 8771"
fi
echo "✅ 前置检查通过"

if [ "$IDEMPOTENT" = 0 ]; then
    echo "==== [1/6] 备份并删除原 unit 中的硬编码密钥行 ===="
    backup_unit "$UNIT" "$BAK" "$BAK2"
    sed -i '/^Environment=YAI_JWT=/d; /^Environment=YAI_CSRF_TOKEN=/d' "$UNIT"
    if grep -qE '^Environment=(YAI_JWT|YAI_CSRF_TOKEN)=' "$UNIT"; then
        echo "❌ 密钥行删除失败，恢复备份中止"; cp -p "$BAK" "$UNIT"; exit 1
    fi
    echo "✅ 硬编码 YAI_JWT / YAI_CSRF_TOKEN 行已删除（PROXY3_URL/LINGSHANG_MODEL 保留）"

    echo "==== [2/6] 写入 drop-in override（动态注入） ===="
    mkdir -p "$OVERRIDE_DIR"
    cat > "$OVERRIDE_DIR/override.conf" <<EOF
# 2026-10-04 去硬编码修复（灵克 v2）：JWT/CSRF 不再固化在 unit 文件，
# 启动时由 ExecStartPre 从 ~/.ling_keys.env 动态提取（该文件由 jwt-refresh timer 每30分钟维护）。
# 回滚：cp $BAK $UNIT && rm -rf $OVERRIDE_DIR && systemctl --user daemon-reload && systemctl --user restart lingshang-api
[Service]
EnvironmentFile=-$LINGS_ENV
ExecStartPre=/bin/bash -c 'grep -E "^(export )?(YAI_JWT|YAI_CSRF_TOKEN|YAI_REFRESH_TOKEN)=" "$HOME/.ling_keys.env" | sed "s/^export //" > $LINGS_ENV && chmod 600 $LINGS_ENV'
EOF
    echo "✅ override.conf 已写入: $OVERRIDE_DIR/override.conf"

    echo "==== [3/6] daemon-reload ===="
    systemctl --user daemon-reload

    echo "==== [4/6] 重启 lingshang-api ===="
    systemctl --user restart lingshang-api.service
    echo "✅ restart 已发出（知识库加载约需 1-2 分钟，轮询等待 bind :8771 ...）"

    echo "==== [5/6] 等待 8771 就绪 ===="
    ok=0
    for i in $(seq 1 45); do
        if ss -tln 2>/dev/null | grep -q ':8771 '; then ok=1; break; fi
        sleep 2
    done
    if [ "$ok" = 1 ]; then
        echo "✅ 8771 已 LISTEN（等待约 $((i*2)) 秒）"
    else
        echo "⚠️ 90 秒内未监听，请查: journalctl --user -u lingshang-api -n 30 --no-pager"
    fi
else
    echo "==== [1-5/6] 跳过（已修复过） ===="
fi

echo "==== [6/6] 验证 ===="
systemctl --user is-active lingshang-api.service || true
ss -tlnp 2>/dev/null | grep ':8771 ' || true
curl -s -o /dev/null -w '8771 HTTP: %{http_code}\n' -m 5 http://127.0.0.1:8771/ || echo "8771 HTTP: 不通（可能仍需加载）"
echo "--- 生效 env 文件检查 ---"
systemctl --user show lingshang-api.service -p EnvironmentFiles --no-pager || true
# [bug-1 修复点]：if 判定而非管道计数，grep exit 1 不再触发 pipefail 误杀
if grep -qE '^Environment=YAI_JWT=' "$UNIT"; then
    echo "⚠️ unit 内仍残留 YAI_JWT 明文"
else
    echo "✅ unit 内已无 YAI_JWT 明文"
fi
echo "✅ 主修复流程完成。回滚命令见 override.conf 首部注释。"

# ---------- 可选：修 jwt-refresh.service 的硬编码 PROXY3_ADMIN_KEY ----------
if [ "${1:-}" = "--with-refresher" ]; then
    echo "==== [附加] jwt-refresh.service 去硬编码（v2 完整三步：备份+删行+动态注入） ===="
    if [ -f "$SYSTEMD_ENV" ] && grep -q 'PROXY3_ADMIN_KEY' "$SYSTEMD_ENV"; then
        backup_unit "$REF_UNIT" "$REF_BAK" "${REF_BAK}.pre"
        # 源==目标场景，extract 传空串（见 bug-3 注释）
        strip_and_override "$REF_UNIT" 'PROXY3_ADMIN_KEY' "$REF_OVERRIDE_DIR" "$SYSTEMD_ENV" ''
        systemctl --user daemon-reload
        echo "✅ jwt-refresh 修复完成（oneshot，下次 timer 触发自动生效）"
    else
        echo "⚠️ $SYSTEMD_ENV 不存在或无 ADMIN_KEY（需 lingflow-proxy3 启动过一次生成），跳过此步"
    fi
fi

echo
echo "======== 后续人工步骤（脚本无法代劳）========"
echo "1. 在 9228 窗口浏览器中打开 ai.yitang.top 重新登录（会话已被服务端吊销 SESSION_REVOKED，必须人工认证）"
echo "2. 登录后手动触发一次续签并核对 token 新鲜度："
echo "   bash /home/ai/scripts/refresh_yai_jwt.sh"
echo "   python3 -c \"import json,time,base64;t=json.load(open('/tmp/yai_tokens.json'))['YAI_JWT'].split('.')[1];t+='='*(-len(t)%4);c=json.loads(base64.urlsafe_b64decode(t));print('exp 剩余小时:',round((c['exp']-time.time())/3600,2))\""
echo "3. token 文件更新后灵商运行实例会自动热加载（call_yai 每次调用前读 /tmp/yai_tokens.json），无需重启"
