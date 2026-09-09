#!/bin/bash
cd /home/ai/lingclaude
echo "=== Python 语法检查（变更文件）==="
for f in $(git diff --name-only HEAD) $(git status --porcelain | awk '{print $NF}'); do
  if [[ "$f" == *.py && -f "$f" ]]; then
    out=$(python -m py_compile "$f" 2>&1)
    if [[ -n "$out" ]]; then
      echo "FAIL: $f"
      echo "$out" | head -5
    fi
  fi
done
echo "=== 语法检查完成 ==="
echo ""
echo "=== 可疑文件检查 ==="
ls -la sitecustomize.py 2>/dev/null
echo "--- sitecustomize.py 内容 ---"
head -30 sitecustomize.py 2>/dev/null
echo "--- 根目录奇怪的'-'文件 ---"
file ./- 2>/dev/null; head -5 ./- 2>/dev/null
echo "--- tmp/ 内容 ---"
ls -la tmp/ 2>/dev/null | head -10
