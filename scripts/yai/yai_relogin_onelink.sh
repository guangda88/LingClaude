#!/usr/bin/env bash
# yai_relogin_onelink.sh — W6-③YAI 人工重登一键恢复（W6「点一次链接」落地件）
#
# 场景: yai_burnout_monitor.py 报 BURNOUT (refresh_token 烧毁, 三重自愈无效)。
#       人工动作只剩: ①在 9228 的 Chrome 窗口里重登 ai.yitang.top ②跑本脚本。
# 链路: 前置检查 → 复用 fix_yai_ws403.sh 收割 → **立即冷备快照(新凭据入版本库)**
#       → burnout 观测器终验 → 结论。
# 设计: 不重造收割逻辑(fix_yai_ws403.sh 已实战验证 2 次), 本脚本只做编排+验收。
# 用法: bash /home/ai/lingclaude/scripts/yai/yai_relogin_onelink.sh   (宿主执行)
# 关联: scripts/yai/fix_yai_ws403.sh · scripts/yai/credentials_snapshot.sh ·
#       scripts/yai/yai_burnout_monitor.py
set -uo pipefail

REPO=/home/ai/lingclaude
YDIR="$REPO/scripts/yai"
log(){ printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
fail(){ log "FATAL: $*" >&2; exit 1; }

# ---- [1/4] 前置: 9228 窗口浏览器可达性 (沙箱/宿主自适应) ----
log "[1/4] 前置检查: CDP 9228 ..."
if ! curl -s -m 3 http://127.0.0.1:9228/json >/dev/null 2>&1; then
  fail "9228 不可达。请先: ①宿主开 Chrome 窗口版(带 --remote-debugging-port=9228)
        ②浏览器打开 https://ai.yitang.top 完成人工登录 ③再跑本脚本"
fi
if ! curl -s -m 3 http://127.0.0.1:9228/json 2>/dev/null | grep -q 'ai.yitang.top'; then
  fail "9228 可达但无 YAI 页面。请在 9228 的 Chrome 里打开 https://ai.yitang.top 登录后重试"
fi
log "  ✅ 9228 有 YAI 页面(登录态应已刷新)"

# ---- [2/4] 收割 (复用实战资产, 含 ws403 原地补丁) ----
log "[2/4] 收割 JWT (fix_yai_ws403.sh) ..."
if ! bash "$YDIR/fix_yai_ws403.sh"; then
  fail "收割失败——查 /tmp/yai_refresh.log 与 fix 脚本输出"
fi

# ---- [3/4] 新凭据立即冷备 (W6-①, 防再次烧毁时无版本可回) ----
log "[3/4] 冷备快照 ..."
KEEP=24 bash "$YDIR/credentials_snapshot.sh" || fail "冷备快照失败(收割已成功,可手动重跑)"

# ---- [4/4] 终验: 烧毁标记应转陈旧/消失 ----
log "[4/4] 终验 ..."
if python3 "$YDIR/yai_burnout_monitor.py"; then
  log "✅ 恢复闭环完成: 收割+热更+冷备+终验 全绿"
  log "   提示: YAI 链路权威验证仍建议跑一次宿主 service_health_snapshot.py 看 YAI live_ping"
  exit 0
else
  fail "标记仍新鲜——续签可能未真正生效, 查 /tmp/yai_refresh.log; 必要时 5 分钟后重跑本脚本"
fi
