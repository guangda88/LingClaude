#!/usr/bin/env bash
# race_runner.sh — 评审竞赛首跑包装器（运行时注入 PROXY_API_KEY，值不落上下文/日志）
# 用法: bash scripts/race_runner.sh <question_file> <models_csv> <judge> <out_json>
# 说明: proxy3 双重鉴权(X-Agent-Id + Bearer)；X-Agent-Id 由 review_contest.py 自带，
#       key 在此运行时从 api_keys.json 逐个探活选取，避免明文进命令行。
set -u
QFILE="${1:?question file}"; MODELS="${2:?models csv}"; JUDGE="${3:?judge}"; OUT="${4:?out json}"
KEYS=$(python3 -c "
import json, os
p = os.path.expanduser('~/.llm_proxy/api_keys.json')
for e in json.load(open(p)):
    k = (e or {}).get('key', '')
    if k: print(k)
")
PICKED=""
for k in $KEYS; do
  code=$(curl -sS -m 20 -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8765/v1/chat/completions \
    -H "Content-Type: application/json" -H "X-Agent-Id: lingclaude" -H "Authorization: Bearer $k" \
    -d '{"model":"MiniMax-M3@minimax","messages":[{"role":"user","content":"ping"}],"stream":false}')
  if [ "$code" = "200" ]; then PICKED="$k"; break; fi
done
if [ -z "$PICKED" ]; then echo "NO_VALID_KEY rc=3"; exit 3; fi
export PROXY_API_KEY="$PICKED"
cd /home/ai/lingclaude || exit 2
python3 scripts/review_contest.py --question "$(cat "$QFILE")" --models "$MODELS" --judge "$JUDGE" \
  > "$OUT" 2>/tmp/race_runner.err
echo "rc=$? out=$(wc -c < "$OUT")"
