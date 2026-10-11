#!/bin/bash
# fix_yai_ws403.sh — 修复 9228 CDP WebSocket 403（Chrome 111+ 默认拒带 Origin 的 ws 连接，
# 2026-09-25 回归，使快速路径收割死了15天）并完成重登后的 JWT 收割
# 用法: bash /home/ai/lingclaude/scripts/yai/fix_yai_ws403.sh   （宿主执行，需能看到 9228/9222）
set -u
S1=/home/ai/yitang/lingshang/server/refresh_yai_token.sh
S2=/home/ai/scripts/refresh_yai_jwt.sh
TS=$(date +%Y%m%d_%H%M%S)

echo "[1/3] 原地补丁 suppress_origin（备份 .bak_wsfix_$TS）"
if [ -f "$S1" ]; then
  cp "$S1" "$S1.bak_wsfix_$TS"
  sed -i "s/create_connection(yai_tab\['webSocketDebuggerUrl'\], timeout=30)/create_connection(yai_tab['webSocketDebuggerUrl'], timeout=30, suppress_origin=True)/" "$S1"
fi
if [ -f "$S2" ]; then
  cp "$S2" "$S2.bak_wsfix_$TS"
  sed -i -e "s/create_connection(target\[0\]\['webSocketDebuggerUrl'\], timeout=10)/create_connection(target[0]['webSocketDebuggerUrl'], timeout=10, suppress_origin=True)/" \
         -e "s/create_connection(yai_tab\['webSocketDebuggerUrl'\], timeout=15)/create_connection(yai_tab['webSocketDebuggerUrl'], timeout=15, suppress_origin=True)/" "$S2"
fi
echo "  suppress_origin 注入数: $(grep -c suppress_origin "$S1" 2>/dev/null || echo 0) + $(grep -c suppress_origin "$S2" 2>/dev/null || echo 0)"

echo "[2/3] 执行收割（编排脚本：9228 快速路径 → profile 同步重型路径 自动降级）"
bash "$S2" 2>&1 | tail -18

echo "[3/3] 权威验证：/tmp/yai_tokens.env（S1 实际写入目标；.ling_keys.env 是静态冷启动源，热注入走 8765/admin/env）"
python3 - << 'PYEOF'
import json, base64, datetime
try:
    raw = open('/tmp/yai_tokens.env').read()
    jwt = [l.split('=',1)[1].strip() for l in raw.splitlines() if l.startswith('YAI_JWT=')][0]
except Exception as e:
    print(f'读取失败: {e}'); raise SystemExit(1)
p = jwt.split('.')[1]; p += '='*(-len(p)%4)
exp = json.loads(base64.urlsafe_b64decode(p))['exp']
ttl = (exp - datetime.datetime.now().timestamp())/3600
print(f"JWT exp={datetime.datetime.fromtimestamp(exp)}  TTL={ttl:+.1f}h  =>  {'✓ 新JWT已收割' if ttl>0 else '✗ 仍旧货'}")
print('proxy3 热注入判定: tail -3 /home/ai/llm-proxy/proxy3_stdout.log 应见 POST /admin/env 200')
PYEOF
echo "（可选终验 live ping）: python3 /home/ai/scripts/service_health_snapshot.py | grep -A6 YAI"
