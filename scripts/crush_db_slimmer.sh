#!/usr/bin/env bash
# crush_db_slimmer.sh — 灵族crush.db瘦身工具
# 策略：清空30天前tool消息的parts字段（保留元数据/session完整性）
# 触发器会自动更新session.message_count（不删除行，不触发）
# 灵克 | lingclaude | SDT-lc-003

set -euo pipefail

AGE_DAYS=${1:-30}
DRY_RUN=${DRY_RUN:-true}
BACKUP_DIR="/home/ai/.crush_backups/slim"

mkdir -p "$BACKUP_DIR"

cutoff=$(date -d "${AGE_DAYS} days ago" +%s)
cutoff_iso=$(date -d "${AGE_DAYS} days ago" +%Y-%m-%d)

total_freed=0
total_cleaned=0

echo "=== crush.db 瘦身 (${DRY_RUN:+DRY RUN}) ==="
echo "清理目标: ${AGE_DAYS}天前的tool消息parts字段 (早于 ${cutoff_iso})"
echo

for db_dir in /home/ai/*/.crush/crush.db; do
    [ -f "$db_dir" ] || continue
    name=$(echo "$db_dir" | sed 's|/home/ai/||;s|/.crush/crush.db||')

    cnt=$(sqlite3 "$db_dir" "SELECT COUNT(*) FROM messages WHERE role='tool' AND created_at < ${cutoff};" 2>/dev/null || echo 0)
    mb=$(sqlite3 "$db_dir" "SELECT ROUND(SUM(LENGTH(COALESCE(parts,'')))/1024.0/1024.0, 1) FROM messages WHERE role='tool' AND created_at < ${cutoff};" 2>/dev/null || echo 0)

    if [ "$cnt" -eq 0 ] || [ "$mb" = "0.0" ] || [ "$mb" = "0" ]; then
        continue
    fi

    printf "%-16s %6s条  %6sMB → " "$name" "$cnt" "$mb"

    if [ "$DRY_RUN" = "false" ]; then
        ts=$(date +%Y%m%d_%H%M%S)
        cp "$db_dir" "${BACKUP_DIR}/${name}_pre_slim_${ts}.db"

        sqlite3 "$db_dir" "UPDATE messages SET parts='[]' WHERE role='tool' AND created_at < ${cutoff};"
        sqlite3 "$db_dir" "VACUUM;"

        new_size=$(stat -c%s "$db_dir")
        old_size=$(stat -c%s "${BACKUP_DIR}/${name}_pre_slim_${ts}.db")
        freed_mb=$(echo "scale=1; ($old_size - $new_size)/1024/1024" | bc)
        printf "释放 %sMB\n" "$freed_mb"
        total_freed=$(echo "$total_freed + $freed_mb" | bc)
    else
        printf "(dry run, skip)\n"
        total_freed=$(echo "$total_freed + $mb" | bc)
    fi
    total_cleaned=$((total_cleaned + cnt))
done

echo
echo "统计: ${total_cleaned}条tool消息, 预估释放 ${total_freed}MB"

if [ "$DRY_RUN" = "true" ]; then
    echo
    echo "执行清理: DRY_RUN=false $0 $AGE_DAYS"
fi
