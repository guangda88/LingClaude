#!/bin/bash
# backup_lingclaude_dbs.sh — lingclaude 四个运行时 DB 的专用备份
# 背景: 2026-09-05 事故(rm -rf .lingclaude 导致四库丢失,无任何备份可恢复)。
# 特性:
#   1. SQLite 在线备份(.backup 命令) — 热库安全,不会备份到写一半的损坏状态
#   2. 大小骤降告警 — 新备份 < 最新备份 50% 时告警并保留旧备份(参考 crush 备份 v2)
#   3. 每 DB 保留 7 份,超出自动清理
# 建议调度: cron/定时器 每 12 小时一次(对齐 SDT-lc-003 热备频率)

set -uo pipefail

SRC_DIR="/home/ai/lingclaude/.lingclaude"
DEST_ROOT="/home/ai/lingclaude/backups/lingclaude_dbs"
LOG_FILE="/home/ai/lingclaude/logs/lingclaude_db_backup.log"
ALERT_FILE="/home/ai/lingclaude/logs/lingclaude_db_backup_alerts.log"
KEEP_COPIES=7
SIZE_ALERT_RATIO=0.5
DBS=(data_flywheel.db knowledge.db memory.db metrics.db)

mkdir -p "$DEST_ROOT" "$(dirname "$LOG_FILE")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"; }
alert() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠️ ALERT: $1" | tee -a "$ALERT_FILE" "$LOG_FILE"; }

TOTAL=0; OK=0; FAIL=0; ALERTS=0

for name in "${DBS[@]}"; do
    db="$SRC_DIR/$name"
    TOTAL=$((TOTAL + 1))
    if [ ! -f "$db" ]; then
        log "SKIP: $name 不存在（尚未生成）"
        continue
    fi

    DEST_DIR="$DEST_ROOT/$name"
    mkdir -p "$DEST_DIR"
    STAMP=$(date +%Y%m%d_%H%M%S)
    DEST="$DEST_DIR/$name.$STAMP"

    SIZE_BEFORE=$(stat -c %s "$db" 2>/dev/null || echo 0)

    # SQLite 在线备份（热安全）；CLI 不可用时降级为 cp（有损坏风险，记录告警）
    if command -v sqlite3 >/dev/null 2>&1; then
        if sqlite3 "$db" ".backup '$DEST'" 2>>"$LOG_FILE"; then
            METHOD="sqlite_online"
        else
            alert "$name: sqlite .backup 失败，降级 cp（可能不一致）"
            cp "$db" "$DEST" && METHOD="cp_fallback"
        fi
    else
        cp "$db" "$DEST" && METHOD="cp_no_sqlite3"
    fi

    if [ ! -s "$DEST" ]; then
        alert "$name: 备份结果为空（源大小 $SIZE_BEFORE），已保留空文件供追查"
        FAIL=$((FAIL + 1))
        continue
    fi

    # 大小骤降告警：与最新旧备份对比
    LATEST_OLD=$(ls -1t "$DEST_DIR"/"$name".* 2>/dev/null | grep -v "$STAMP" | head -1 || true)
    if [ -n "$LATEST_OLD" ]; then
        OLD_SIZE=$(stat -c %s "$LATEST_OLD")
        if [ "$OLD_SIZE" -gt 0 ] && [ "$SIZE_BEFORE" -lt $((OLD_SIZE * 50 / 100)) ]; then
            alert "$name 大小骤降: $(numfmt --to=iec $OLD_SIZE 2>/dev/null || echo ${OLD_SIZE}B) → $(numfmt --to=iec $SIZE_BEFORE 2>/dev/null || echo ${SIZE_BEFORE}B)（可能清空/重建），旧备份已保留: $LATEST_OLD"
            ALERTS=$((ALERTS + 1))
        fi
    fi

    # 保留 7 份
    COUNT=$(ls -1t "$DEST_DIR"/"$name".* 2>/dev/null | wc -l)
    if [ "$COUNT" -gt "$KEEP_COPIES" ]; then
        ls -1t "$DEST_DIR"/"$name".* | tail -n +$((KEEP_COPIES + 1)) | xargs rm -f 2>/dev/null || true
    fi

    log "OK: $name → $DEST（method=$METHOD, size=$SIZE_BEFORE, 副本数=$COUNT）"
    OK=$((OK + 1))
done

log "汇总: total=$TOTAL ok=$OK fail=$FAIL alerts=$ALERTS"

# 退出码非 0 便于 cron 告警联动
if [ "$FAIL" -gt 0 ]; then exit 1; fi
if [ "$OK" -eq 0 ]; then exit 2; fi
exit 0
