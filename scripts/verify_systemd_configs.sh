#!/bin/bash
# systemd服务配置验证脚本（7/16事故教训：StartLimit在[Service]段被静默忽略）
# 检查所有灵族systemd服务配置是否通过systemd-analyze verify
# 用法: bash verify_systemd_configs.sh

LOG="/tmp/systemd_verify_$(date +%Y%m%d_%H%M%S).log"
ERRORS=0
WARNINGS=0

echo "=== systemd config verification ==="

# 收集所有用户级服务文件
USER_SERVICES=$(find /home/ai/.config/systemd/user -name '*.service' 2>/dev/null)
# 收集系统级灵族服务文件
SYSTEM_SERVICES=$(find /etc/systemd/system -name 'lingflow*' -o -name 'lingbus*' -o -name 'omniroute*' -o -name 'proxy3*' -o -name 'memory-watchdog*' -o -name 'atomcode*' -o -name 'meeting*' 2>/dev/null)

for svc in $USER_SERVICES $SYSTEM_SERVICES; do
    [ -f "$svc" ] || continue
    result=$(systemd-analyze verify "$svc" 2>&1)
    # 检查是否有StartLimit相关警告
    if echo "$result" | grep -q "StartLimit.*section 'Service'"; then
        echo "ERROR: $svc - StartLimit in [Service] section (ignored by systemd)!"
        ERRORS=$((ERRORS + 1))
    fi
    # 检查其他警告
    other_warnings=$(echo "$result" | grep -v "StartLimit.*section 'Service'" | grep -i "unknown\|error\|warning" | grep -v "teamviewerd\|legacy directory")
    if [ -n "$other_warnings" ]; then
        echo "WARN: $svc - $other_warnings"
        WARNINGS=$((WARNINGS + 1))
    fi
done

echo ""
echo "=== Summary: $ERRORS errors, $WARNINGS warnings ==="
echo "Errors = StartLimit in [Service] (must fix)"
echo "Warnings = other config issues (review recommended)"

exit $ERRORS
