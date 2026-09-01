#!/bin/bash
# crush-low-oom.service (INCIDENT_20260825 配套)
# 每个 timer tick 把所有 crush TUI 进程 oom_score_adj 拉到 -900。
# 注意：本 service User=root，脚本内禁止 sudo（2026-08-29 重建，
# 原脚本丢失导致 203/EXEC，timer 10s 一次持续失败）。
# 已知局限（#064）：OOMScoreAdjust 只挡 kernel OOM killer，不挡 systemd-oomd；
# oomd 目前 masked，若将来重新启用需配合 OOMPolicy=continue。
set -u
for pid in $(pgrep -f 'crush/bin/crush' 2>/dev/null); do
    [ -w "/proc/$pid/oom_score_adj" ] || continue
    if [ "$(cat /proc/$pid/oom_score_adj 2>/dev/null)" != "-900" ]; then
        echo -900 > "/proc/$pid/oom_score_adj" 2>/dev/null || true
    fi
done
exit 0
