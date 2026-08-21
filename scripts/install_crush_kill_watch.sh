#!/usr/bin/env bash
# 安装 auditd 并加载 crush kill 监控规则（需 root，一次性）
set -euo pipefail

if ! command -v auditd >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y auditd
fi

install -m 0644 /home/ai/lingclaude/scripts/crush_kill_watch.rules \
    /etc/audit/rules.d/crush_kill_watch.rules

augenrules --load
systemctl enable --now auditd

echo "--- 验证 ---"
auditctl -l | grep crush_kill_watch && echo "RULES_LOADED"

echo ""
echo "事后取证命令（下次 crush 被杀后执行）："
echo "  ausearch -k crush_kill_watch -ts today | aureport -k -i | tail -50"
