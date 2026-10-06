#!/usr/bin/env python3
"""修复 lingflow MCP 注册表：所有裸 `fastmcp run` 条目补 --transport stdio。

根因（2026-10-04 排查）:
  fastmcp 3.x CLI 的 `run` 子命令默认 transport=HTTP 并绑定 127.0.0.1:8000，
  而宿主 8000 已被灵知(lingzhi)后端自 9-23 常驻占用 → spawn 即 EADDRINUSE 秒死，
  客户端(lingclaude/engine/mcp_client.py)表现为
  "stdio transport error: MCP server closed stdout"。
  独立 netns(沙盒)里 8000 空闲故无法复现，宿主侧必现。

修复范围: 注册表内 4 处裸 run 条目(lingmessage×3 + lingminopt)统一补
  `--transport stdio`——引擎 stdio 客户端只支持 stdio，此改动对全部条目均为纠偏。
  lingminopt 条目已用 initialize/tools_list 握手实测通过。
"""
import ast
import re
import shutil
import sys
from pathlib import Path

F = Path("/home/ai/lingflow_plus/lingflow_plus/mcp_registry.py")
s = F.read_text()
if "--transport" in s and "stdio" in s and 'args=["run"' not in re.sub(r'--transport",\s*"stdio', "", s):
    pass  # 继续走逐条检查

pat = re.compile(r'args=\["run", ([^\]]*?)\],')
hits = [m for m in pat.finditer(s) if "--transport" not in m.group(0)]
if not hits:
    print("无待修复条目(已全部带 --transport 或结构变化)，请人工确认")
    sys.exit(0)
print(f"待修复 {len(hits)} 处:")
for m in hits:
    print("  " + m.group(0)[:110])

new_s = pat.sub(lambda m: f'args=["run", {m.group(1)}, "--transport", "stdio"],' if "--transport" not in m.group(0) else m.group(0), s)
bak = F.parent / (F.name + ".bak-20261004")
shutil.copy2(F, bak)
F.write_text(new_s)
ast.parse(new_s)  # 语法门
print(f"已补丁 {len(hits)} 处: {F}")
print(f"已备份: {bak}")
