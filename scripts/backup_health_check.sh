#!/bin/bash
# backup_health_check.sh - 备份健康检查 (CRUSH.md 教训#4 静默失效检测代码化)
#
# 检查 /home/ai/.crush_backups/<member>/ 最新备份文件 mtime
# 超过 25 小时无新备份 -> 输出告警 (供 cron/systemd 调用)
#
# 用法:
#   bash backup_health_check.sh              # 检查并打印
#   bash backup_health_check.sh --warn-only  # 仅在有告警时输出 (供 cron 静默运行)
#
# 退出码:
#   0 - 全部健康
#   1 - 有成员备份过期 (25h 无新备份)
#   2 - 脚本错误
#
# 作者: 灵克 (lingclaude)
# 日期: 2026-07-20
# 关联: .audit/backup_silent_failure_20260720.md

set -euo pipefail

BACKUP_ROOT="/home/ai/.crush_backups"
THRESHOLD_HOURS=25
WARN_ONLY=false
ALERTS=0
CHECKED=0

[[ "${1:-}" == "--warn-only" ]] && WARN_ONLY=true

# 获取所有有 .crush/crush.db 的成员
for db in /home/ai/*/.crush/crush.db; do
    [[ -f "$db" ]] || continue
    OWNER=$(basename "$(dirname "$(dirname "$db")")")
    DEST_DIR="$BACKUP_ROOT/$OWNER"
    CHECKED=$((CHECKED + 1))

    # 找最新备份文件 (加 || true 防 glob 无匹配, 同 backup 脚本根因)
    LATEST=$(ls -1t "$DEST_DIR"/crush.db.* 2>/dev/null | head -1 || true)

    if [[ -z "$LATEST" ]]; then
        echo "🔴 [CRITICAL] $OWNER: 从无备份记录"
        ALERTS=$((ALERTS + 1))
        continue
    fi

    # 计算备份文件年龄 (小时)
    NOW=$(date +%s)
    MTIME=$(stat -c%Y "$LATEST" 2>/dev/null || echo 0)
    AGE_HOURS=$(( (NOW - MTIME) / 3600 ))

    if [[ "$AGE_HOURS" -gt "$THRESHOLD_HOURS" ]]; then
        echo "🟡 [WARNING] $OWNER: 最新备份 ${AGE_HOURS}h 前 (阈值 ${THRESHOLD_HOURS}h) -> $(basename "$LATEST")"
        ALERTS=$((ALERTS + 1))
    elif [[ "$WARN_ONLY" == false ]]; then
        SIZE=$(du -h "$LATEST" | cut -f1)
        echo "🟢 [OK] $OWNER: ${AGE_HOURS}h 前 ($SIZE) -> $(basename "$LATEST")"
    fi
done

if [[ "$ALERTS" -gt 0 ]]; then
    echo "---"
    echo "告警: $ALERTS/$CHECKED 成员备份过期"
    exit 1
elif [[ "$WARN_ONLY" == false ]]; then
    echo "---"
    echo "全部健康: $CHECKED/$CHECKED"
fi

exit 0
