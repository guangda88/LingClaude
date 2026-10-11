#!/bin/bash
# 修复 user 级 failed 单元 —— 2026-10-10 诊断包（配 docs 报告使用）
# 用法: bash /home/ai/lingclaude/scripts/systemd/repair_user_failed_units.sh
set -u
log(){ echo "[$(date '+%F %T')] $*"; }
U="$HOME/.config/systemd/user"

# ---- P0: state_backup（INC-20260905-RMRF 防线，停摆 40+ 天）----
# 病根: unit 文件缺 [Unit] 节头 + User=ai 在 user manager 下非法(216/GROUP)
#       + backups/state/ 目录从未创建(一次都没跑成过)
if diff -q "$U/state_backup.service" /home/ai/lingclaude/scripts/systemd/state_backup.service.fixed >/dev/null 2>&1; then
  log "state_backup.service 已是修正版，跳过"
else
  cp "$U/state_backup.service" "$U/state_backup.service.bak.$(date +%Y%m%d_%H%M%S)"
  cp /home/ai/lingclaude/scripts/systemd/state_backup.service.fixed "$U/state_backup.service"
  log "state_backup.service 已替换(原文件已备份 .bak.*)"
fi

# ---- 禁用依赖已消失/过时的单元 ----
for u in lingai-local-inference ling-thinking-watchdog yai-harvest; do
  systemctl --user disable --now "$u.service" >/dev/null 2>&1
  log "disabled: $u"
done
# 说明: lingai-local-inference=模型文件已删; thinking-watchdog=日志停在4月且指向旧路径;
#       yai-harvest=从未接 timer、journal 零记录

# ---- 清理一次性/桌面残留状态 ----
systemctl --user reset-failed lc-push-round8.service 2>/dev/null
for u in xdg-desktop-portal xdg-desktop-portal-gtk xdg-document-portal xfce4-notifyd; do
  systemctl --user reset-failed "$u.service" 2>/dev/null
done
log "reset-failed: lc-push-round8 + 桌面4件(下次图形登录自愈)"

systemctl --user daemon-reload

# ---- P0 立即端到端验证 ----
if systemctl --user start state_backup.service; then
  log "state_backup 启动 OK"
  log "最新备份: $(ls -t /home/ai/lingclaude/backups/state/ 2>/dev/null | head -2 | tr '\n' ' ')"
else
  log "!! state_backup 启动仍失败，查看: journalctl --user -u state_backup -n 20 --no-pager"
fi

echo
log "—— 决策项: ①已代做自动恢复 ②~⑤为带证据建议（脚本不代做）——"

# ① ling-security-daily 自动恢复（脚本已从 983d514^ 考古找回 + 成员清单更新为16仓）
sed -i 's|^ExecStart=/usr/bin/python3 scripts/daily_security_check.py|WorkingDirectory=/home/ai/lingclaude\nExecStart=/usr/bin/python3 scripts/daily_security_check.py|' \
  "$HOME/.config/systemd/user/ling-security-daily.service" 2>/dev/null \
  && log "security-daily unit: 补 WorkingDirectory(相对路径在user manager下cwd=$HOME找不到脚本)"
systemctl --user daemon-reload
if systemctl --user start ling-security-daily.service 2>/dev/null; then
  log "ling-security-daily: 已恢复(脚本找回+16成员+unit修正), 报告落 ~/.ling_security/daily/"
else
  log "WARN 启动失败: journalctl --user -u ling-security-daily -n 20 --no-pager"
fi

cat <<'EOF'
② mlflow_eval（164条订阅路由评估→MLflow）：判定=当前无硬需求。
   ranker.eval_quality 权重0.2，MLflow缺席时 9-15 热修已回退audit兜底(ranker.py:633)；
   服务未部署(:5000无监听)、评估在开始前即失败(未烧LLM成本)。
   建议: systemctl --user disable --now mlflow_eval_proxy3.timer mlflow_eval_proxy3.service
   未来要系统化LLM-as-judge评估 → uvx mlflow server 一条命令重启此链路(eval输入evals/*.yaml在位)。
③ yai-rescue（2h一次JWT抢救）：判定=保留。JWT续签主力=proxy3进程内 auth_refresh._refresh_yai
   (auth_refresh.py:173)→refresh_yai_jwt.sh触发式续签，本单元是2h兜底。
   ⚠ 现状: 罐内refresh_token已401烧毁、现役JWT 2026-10-10 01:46已过期——YAI路由当前是死的,
   需人工yitang重登(或跑scripts/yai_harvest.py收割新族,族输入停在9-19)后自然恢复。
   空转成本26s/2h可忽略,不建议disable。
④ yai-harvest（新族收割）：判定=一次性工具(无timer、me探活→rt刷新→原子更新ling_keys.env,
   9-19后未扩族)。disable --now 摘出failed列表即可,脚本保留(下次扩族手动跑)。
⑤ ling-term-mcp-http-proxy / lingxi-redzone（9529/9532均无监听、疑被stdio传输取代）:
   与灵犀负责人确认后 systemctl --user disable --now <两单元>，或保留观察。
EOF
log "完成后执行: systemctl --user --failed  # 应只剩你决定保留观察的"
