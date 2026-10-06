#!/bin/bash
# 灵族服务清理 — 沙盒内批次 (1.1 stdio 孤儿 / 1.2 测试 http.server / 4 linminopt 归档)
# 用法: 默认 dry-run; 加 --execute 才真正动手
# 纪律: 动态过滤(PPID+存活时长)，不硬编码 PID；归档校验通过才删源
set -u
MODE="${1:-dry-run}"
[[ "$MODE" == "--execute" ]] && EXEC=1 || EXEC=0
TS="$(date +%Y%m%d_%H%M%S)"
LOG="/home/ai/lingclaude/.atomcode/maintenance/service_repair_${TS}.log"
exec > >(tee -a "$LOG") 2>&1
echo "== 灵族服务清理 mode=$MODE =="

# ---------- 1.1 灵犀 stdio 孤儿 ----------
echo "--- [1.1] 灵犀 stdio 孤儿进程 (PPID=1 且存活>48h)"
ORPHANS=()
for p in $(pgrep -f 'lingxi/dist/cli.js stdio'); do
  ppid=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
  secs=$(ps -o etimes= -p "$p" 2>/dev/null | tr -d ' ')
  [[ -z "$ppid" || -z "$secs" ]] && continue
  if [[ "$ppid" == "1" && "$secs" -gt 172800 ]]; then
    ORPHANS+=("$p")
    echo "  候选: PID=$p PPID=$ppid 存活=${secs}s ($(ps -o lstart= -p $p))"
  else
    echo "  保留: PID=$p PPID=$ppid 存活=${secs}s (活链路/新进程，不动)"
  fi
done
if [[ ${#ORPHANS[@]} -eq 0 ]]; then echo "  无孤儿可清"; else
  if [[ $EXEC -eq 1 ]]; then
    kill "${ORPHANS[@]}" 2>/dev/null; sleep 2
    LEFT=(); for p in "${ORPHANS[@]}"; do kill -0 "$p" 2>/dev/null && LEFT+=("$p"); done
    [[ ${#LEFT[@]} -gt 0 ]] && kill -9 "${LEFT[@]}" 2>/dev/null
    echo "  已 TERM ${#ORPHANS[@]} 个(残留强杀: ${#LEFT[@]})"
  else
    echo "  [dry-run] 将 TERM: ${ORPHANS[*]}"
  fi
fi
echo "  现存 stdio 总数: $(pgrep -fc 'lingxi/dist/cli.js stdio' || echo 0)"

# ---------- 1.2 测试遗留 http.server ----------
echo "--- [1.2] 测试 http.server (8772/8080，9月遗留)"
HS=()
while read -r pid; do
  [[ -z "$pid" || "$pid" == "$$" ]] && continue
  args=$(ps -o args= -p "$pid" 2>/dev/null) || continue
  case "$args" in *bwrap*|*"'echo'"*|*"=== "*|*awk*) continue;; esac  # 排除本会话沙盒链
  case "$args" in
    *"-m http.server 8772"*|*"-m http.server 8080"*|*"python3 -c"*http.server*)
      HS+=("$pid"); echo "  候选: PID=$pid :: ${args:0:80}";;
  esac
done < <( { pgrep -f 'http.server'; pgrep -f 'python3 -c.*http'; } 2>/dev/null | sort -u )
if [[ ${#HS[@]} -eq 0 ]]; then echo "  无可清"; else
  if [[ $EXEC -eq 1 ]]; then kill "${HS[@]}" 2>/dev/null; sleep 1; kill -9 "${HS[@]}" 2>/dev/null; echo "  已杀 ${#HS[@]} 个"; else echo "  [dry-run] 将杀: ${HS[*]}"; fi
fi

# ---------- 4. linminopt 误建目录归档 ----------
echo "--- [4] /home/ai/linminopt (6月工作现场 9.1M) 归档"
SRC=/home/ai/linminopt
ARC_DIR=/home/ai/.atomcode/datalog_archive
ARC="$ARC_DIR/linminopt_workdir_202606.tar.zst"
if [[ -d "$SRC" ]]; then
  N_SRC=$(find "$SRC" -mindepth 1 | wc -l)
  if [[ $EXEC -eq 1 ]]; then
    mkdir -p "$ARC_DIR"
    tar --zstd -C /home/ai -cf "$ARC" linminopt
    N_ARC=$(tar -tf "$ARC" | wc -l)
    echo "  源条目=$N_SRC 归档条目=$N_ARC"
    if [[ "$N_ARC" -ge "$N_SRC" ]]; then
      (cd /home/ai && rm -rf ./linminopt) && echo "  校验通过，源已删 → $ARC"
    else
      echo "  !! 校验失败，保留源，人工检查 $ARC"
    fi
  else
    echo "  [dry-run] 将 tar.zst → $ARC (条目≈$N_SRC)，校验后删源"
  fi
else
  echo "  $SRC 不存在(可能已处理)"
fi

# ---------- README 补记 ----------
if [[ $EXEC -eq 1 && -f "$ARC_DIR/README.md" ]]; then
  cat >> "$ARC_DIR/README.md" <<EOF

## 2026-10-04 服务清理批次追加
- linminopt_workdir_202606.tar.zst : /home/ai/linminopt 误建工作目录(6月, 含独特版本脚本与 crush 会话)，校验后删源
- 灵犀 stdio 孤儿清理与测试 http.server 关停记录见 ../service_repair_${TS}.log
EOF
fi
echo "== 完成 mode=$MODE =="
