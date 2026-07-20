#!/usr/bin/env bash
# PreToolUse hook: 拦截对 handover 文件的写入
# 配置: matcher "^(edit|write|multiedit|bash)$"
set -euo pipefail

python3 /home/ai/lingclaude/lingmemory/lifecycle_enforcer.py check-write
