#!/usr/bin/env bash
# 预算预警插片评审——宿主执行脚本（形态沿用 panel_20261008_changshou 已验证流程）
# 用途：在宿主终端（有网络的 shell）执行三家外部 agent 评审并落盘
# 用法：bash /home/ai/lingclaude/docs/research/panel_20261008_budget/run_on_host.sh
# 产出：同目录 answer_ac.md / answer_codex.md / answer_oc.md
set -u
D=/home/ai/lingclaude/docs/research/panel_20261008_budget
PROMPT=$(cat "$D/unified_prompt.md")
cd "$D"

run(){ # $1=名字 $2=命令模板
  echo "[$(date +%H:%M:%S)] 启动 $1 ..."
  eval "$2" > "logs_$1.txt" 2>&1 &
}

run ac    "claude -p \"\${PROMPT}
补充：落盘文件用 $D/answer_ac.md，文末署名区写 agent 名/调用方式/耗时/自评置信度\" --permission-mode acceptEdits"

run codex "codex exec \"\${PROMPT}
补充：落盘文件用 $D/answer_codex.md，文末署名区写 agent 名/调用方式/耗时/自评置信度\" --sandbox workspace-write --skip-git-repo-check"

run oc    "opencode run \"\${PROMPT}
补充：必须落盘到 $D/answer_oc.md（不给落盘指令就不落盘），文末署名区写 agent 名/调用方式/耗时/自评置信度\""

echo "三家已后台启动，日志在 $D/logs_*.txt；交付齐后运行：ls -la $D/answer_*.md"
wait
echo "=== 全部结束 ==="
ls -la "$D"/answer_*.md 2>/dev/null || echo "(无交付，查 logs_*.txt 排错)"
