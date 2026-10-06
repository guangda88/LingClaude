#!/usr/bin/env bash
# 灵依 (lingyi) 注册表条目摘除 —— 批次 3 收尾（宿主侧执行）
# 背景: 灵依 2026-09-17 退出灵族；lingyi-mcp 指向已删除模块（spawn 即死，纯噪音）。
# 对齐 2026-09-16 灵通「方案 A」先例。路由层按 lingflow 仓自身决策不动。
# 用法: 先 dry-run（默认，在 /tmp 副本上验证补丁），确认后 --execute 落盘+跑测试
set -uo pipefail
MODE="${1:-dry-run}"
DIR=/home/ai/lingflow_plus
M=/home/ai/lingclaude/.atomcode/maintenance

echo "== 灵依注册表摘除 mode=$MODE =="

echo "--- [1] 注册表条目摘除（锚定+语法门+幂等） ---"
if [ "$MODE" = "--execute" ]; then
  python3 "$M/remove_lingyi_registry_entry.py"
else
  cp "$DIR/lingflow_plus/mcp_registry.py" /tmp/mcp_registry.preview.py
  python3 "$M/remove_lingyi_registry_entry.py" /tmp/mcp_registry.preview.py
fi

echo "--- [2] 测试同步（7 处锚定断言） ---"
if [ "$MODE" = "--execute" ]; then
  python3 "$M/update_lingyi_tests.py"
else
  cp "$DIR/tests/test_mcp_registration.py" /tmp/test_mcp_registration.preview.py
  python3 "$M/update_lingyi_tests.py" /tmp/test_mcp_registration.preview.py
fi

if [ "$MODE" = "--execute" ]; then
  echo "--- [3] 定向测试（102 项） ---"
  cd "$DIR" && python3 -m pytest tests/test_mcp_registration.py -q 2>&1 | tail -3
  echo "== 完成。回滚备选: 用同目录 .bak-20261004 覆盖回 =="
else
  echo "== dry-run 完成（副本上验证）。确认后: bash $0 --execute =="
fi
