#!/bin/bash
# 灵族服务修复 — 宿主侧批次 (2 拉起灵极优 MCP server / C-1 前置复核)
# 用法: bash host_service_repair_20261004.sh [--execute]   默认 dry-run
# 位置: 必须在宿主终端执行（灵克沙盒进程随会话销毁，拉起的服务必须宿主持有）
set -u
MODE="${1:-dry-run}"
EXEC=0; [[ "$MODE" == "--execute" ]] && EXEC=1
echo "== 宿主侧服务修复 mode=$MODE =="

# ---------- 0 前置复核 ----------
echo "--- [0] 前置复核"
# stdio 型 server 无常驻进程是正常态，此处仅复核注册表现状(读 grep 不改)
grep -n 'args=\["run".*lingminopt' /home/ai/lingflow_plus/lingflow_plus/mcp_registry.py || echo "  !! 注册表无 lingminopt 条目"
pgrep -af 'opencode serve' || echo "  opencode serve 不在跑 (C-1 无需先停服务)"

# ---------- 2 灵极优 MCP 面修复 ----------
# 2026-10-04 排查结论: 灵极优是 stdio 型 MCP server，由 lingclaude 引擎
# (lingclaude/engine/mcp_proxy.py → lingflow_plus/mcp_registry.py:225) 在调用时按需拉起，
# 无需宿主常驻进程。此前的 nohup 拉起设计是错的(stdin=EOF 即退出，kill -0 必失败)。
# 真正故障: 注册表命令 `fastmcp run ...` 缺 --transport stdio，默认起 HTTP 绑 8000，
# 与灵知后端(9-23 起常驻 :8000)端口冲突 → spawn 秒死 → "closed stdout"。
echo "--- [2] 灵极优修复: 注册表补 --transport stdio + 握手验证"
PATCH=/home/ai/lingclaude/.atomcode/maintenance/patch_lingminopt_registry.py
if [[ $EXEC -eq 1 ]]; then
  python3 "$PATCH" && {
    echo "  握手验证(initialize+tools/list):"
    printf '%s\n' \
      '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' \
      '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
      '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
      | timeout 15 /home/ai/.local/bin/fastmcp run /home/ai/lingminopt/lingminopt/mcp_server.py --transport stdio 2>/dev/null \
      | head -c 200
    echo
    echo "  ✓ 若上行出现 serverInfo lingminopt 即修复成功；随后新开会话调 [MCP:灵极优] 工具实测"
  }
else
  echo "  [dry-run] 将执行: python3 $PATCH (含备份与语法门)"
fi
# ---------- 4 删除已归档的 linminopt 误建目录 (沙盒白名单外 EROFS，须宿主删) ----------
echo "--- [4] /home/ai/linminopt 删源 (前提: 归档已存在且校验)"
ARC=/home/ai/.atomcode/datalog_archive/linminopt_workdir_202606.tar.zst
SRC=/home/ai/linminopt
if [[ -d "$SRC" ]]; then
  N_ARC=$(tar --zstd -tf "$ARC" 2>/dev/null | wc -l)
  N_SRC=$(find "$SRC" -mindepth 1 | wc -l)
  echo "  归档条目=$N_ARC 源条目=$N_SRC (归档应>=源)"
  if [[ "$N_ARC" -lt "$N_SRC" ]]; then
    echo "  [跳过] 校验不过: 归档($N_ARC) < 源($N_SRC)，不删源"
  elif [[ $EXEC -eq 1 ]]; then
    rm -rf "$SRC" && echo "  源已删 (回收 ~9.1M)，归档保留: $ARC"
  else
    echo "  [dry-run] 校验通过(15>=14)，--execute 时删源"
  fi
else
  echo "  $SRC 不存在(已处理)"
fi
echo "== 完成 =="
