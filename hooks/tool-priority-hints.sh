#!/usr/bin/env bash
# PreToolUse hook: 工具优先级提示
# 当成员用 bash 执行 grep -r/find/sqlite3 直查时，注入 context 提示用专用 MCP 工具
# 不阻断执行，只提示（exit 0 + context）
set -euo pipefail

input=$(cat)
cmd=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('command',''))" 2>/dev/null || echo "")

context_lines=()

# grep -r (跨项目/跨目录递归搜索) -> code_search
if echo "$cmd" | grep -qiE 'grep\s+(-r|--recursive)\s'; then
    context_lines+=("提示: 跨目录递归搜索建议用 mcp_ling-term-mcp_code_search (支持 project+language filter+语义搜索)。grep 仅用于单文件/单目录精确匹配。")
fi

# find . -name -> glob 或 search
if echo "$cmd" | grep -qiE '^find\s|find\s+\.\s'; then
    context_lines+=("提示: 文件查找建议用 glob 工具或 mcp_ling-term-mcp_search (内外统一+语义匹配)。")
fi

# sqlite3 直查 lingmemory/lingbus -> lm_query / poll_messages
if echo "$cmd" | grep -qiE 'sqlite3\s+.*lingmem|sqlite3\s+.*lingbus'; then
    context_lines+=("提示: 数据库查询建议用 mcp_ling-term-mcp_lm_query (lingmemory) 或 mcp_ling-term-mcp_poll_messages (lingbus)，支持游标分页+类型过滤。")
fi

# ss/ps/netstat -> visible_state
if echo "$cmd" | grep -qiE '\b(ss|netstat)\s+.*-tln|ps\s+aux'; then
    context_lines+=("提示: 服务巡检建议用 mcp_ling-term-mcp_visible_state (聚合安全可见性指标)。")
fi

# 输出 context JSON (不阻断)
if [ ${#context_lines[@]} -gt 0 ]; then
    ctx=$(printf '%s\\n' "${context_lines[@]}")
    python3 -c "
import json, sys
ctx = '''$ctx'''
print(json.dumps({'context': ctx}, ensure_ascii=False))
"
fi

exit 0
