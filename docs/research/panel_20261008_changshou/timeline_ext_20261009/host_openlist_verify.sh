#!/usr/bin/env bash
# OpenList 实时在线核验 356 个讲课资产文件（宿主侧执行）v2
# v1 bug 修复: 核对 TSV 为「文件名\t路径\t状态」3 列（HIT/MISS）
#             或「文件名\t资产行\t路径\t候选N」4 列（FUZZY 已解析）——
#             v1 误按「状态在第 1 列」grep，会空跑 0 条。
# 前置: OpenList 服务运行于宿主 :5244（灵知 openlist）; 如需认证配 OPENLIST_TOKEN
# 用法: bash host_openlist_verify.sh
set -uo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"
TSV="$BASE/asr_资产核对_缓存级.tsv"
[[ -f "$TSV" ]] || { echo "缺少 $TSV"; exit 1; }
OUT="$BASE/openlist_verify_$(date +%Y%m%d_%H%M%S).tsv"
TOKEN="${OPENLIST_TOKEN:-}"
API="${OPENLIST_API:-http://localhost:5244/api/fs/get}"
AUTH=(); [[ -n "$TOKEN" ]] && AUTH=(-H "Authorization: $TOKEN")

# 提取待核验路径（唯一化）:
#   3 列且 $3==HIT → 路径=$2；4 列（候选N）→ 路径=$3
PATHS=$(awk -F'\t' '
  NF==3 && $3=="HIT" && $2!="" {print $2}
  NF==4 && $4 ~ /^候选/ && $3!="" {print $3}
' "$TSV" | sort -u)

echo "路径	存在	大小" > "$OUT"
n=0; ok=0
while IFS= read -r path; do
  [[ -z "$path" ]] && continue
  n=$((n+1))
  resp=$(curl -s --max-time 15 -X POST "$API" -H "Content-Type: application/json" \
    ${AUTH[@]+"${AUTH[@]}"} \
    -d "{\"path\":\"$path\",\"password\":\"\"}")
  exist=$(python3 -c "import json,sys;d=json.loads(sys.argv[1]);print(bool((d.get('data') or {}).get('name')))" "$resp" 2>/dev/null || echo False)
  size=$(python3 -c "import json,sys;d=json.loads(sys.argv[1]);print((d.get('data') or {}).get('size',0))" "$resp" 2>/dev/null || echo 0)
  [[ "$exist" == "True" ]] && ok=$((ok+1))
  printf '%s	%s	%s\n' "$path" "$exist" "$size" >> "$OUT"
  sleep 0.2   # 温和限速，防打爆网盘挂载
done <<< "$PATHS"

echo "实时核验: $ok / $n 存在 → $OUT"
[[ $n -eq 0 ]] && { echo "⚠ 0 条待核验——检查 TSV 列格式是否又变了"; exit 3; }
