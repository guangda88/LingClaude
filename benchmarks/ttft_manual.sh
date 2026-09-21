#!/bin/bash
# TTFT 对照测量（手动执行版）——在用户终端跑，绕过沙箱网络限制
# 用法: bash benchmarks/ttft_manual.sh   (需要 ZHIPU_API_KEY 环境变量)
set -e
cd "$(dirname "$0")"
: "${ZHIPU_API_KEY:?请先 export ZHIPU_API_KEY=...}"
AUTH_NAME="Authorization"
AUTH_SCHEME="Bearer"
H1="$AUTH_NAME: $AUTH_SCHEME $ZHIPU_API_KEY"
H2="Content-Type: application/json"

echo "payload 大小: small=$(stat -c%s payload_small.json 2>/dev/null)B mid=$(stat -c%s payload_mid.json 2>/dev/null)B large=$(stat -c%s payload_large.json 2>/dev/null)B"
for size in small mid large; do
  for run in 1 2; do
    metrics=$(curl -s -o /dev/null -w '%{time_starttransfer} %{time_total}' \
      -H "$H1" -H "$H2" \
      --data-binary "@payload_$size.json" \
      https://open.bigmodel.cn/api/coding/paas/v4/chat/completions)
    ttft=${metrics%% *}
    total=${metrics##* }
    python3 -c "print(f'[$size run$run] TTFT={$ttft:.2f}s  total={$total:.2f}s  decode≈{$total-$ttft:.2f}s')"
  done
done
