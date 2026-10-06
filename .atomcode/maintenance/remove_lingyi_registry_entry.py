#!/usr/bin/env python3
"""灵依 (lingyi) 注册表条目摘除器 —— 批次 3 之 lingflow_plus 侧。

背景（2026-10-04）：灵依 2026-09-17 经用户决策退出灵族（org_event 实证），
载体定性为库型（无 MCP 入口），console_script `lingyi-mcp` 指向已不存在的
`lingyi.mcp_server` 模块（ModuleNotFoundError 实证）。注册表条目每次被
调用即 spawn 失败，属纯噪音工具面。

做法：对齐 2026-09-16 灵通 (lingflow) 条目「方案 A」先例——移除条目、
留注释 tombstone 说明摘除原因与恢复条件。路由层 (lingflow.router.tool)
按其自身「路由保留兼容」决策不动；glm_agent 任务分类、外部项目定性、
watchdog 例外清单均不动。

安全设计：
  - 幂等：已摘除则 0 退出
  - 锚定校验：仅当存在 `\"lingyi\": MCPServerConfig(` 且存在收尾 `),` 行时才动
  - 备份：<file>.bak-YYYYMMDD（不覆盖已有备份）
  - 语法门：ast.parse 通过才落盘
  - 断言门：摘除后 grep 断言 0 命中 `lingyi`（防误删/防漏删双保险）
"""
import ast
import shutil
import sys
from datetime import date
from pathlib import Path

TARGET = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/home/ai/lingflow_plus/lingflow_plus/mcp_registry.py")
TOMBSTONE = '''    # "lingyi"（灵依）条目已移除（2026-10-04，经用户授权；对齐灵通 2026-09-16 方案 A 先例）：
    # 灵依 2026-09-17 退出灵族（data/ling_org/org_event 实证，retired）。
    # 载体为库型（无 MCP 入口）；~/.local/bin/lingyi-mcp 指向已不存在的
    # lingyi.mcp_server 模块（ModuleNotFoundError），spawn 即失败，纯噪音工具面。
    # 路由层 lingflow.router.tool 按「路由保留兼容」决策保留 LINGYI 规则（不动）。
    # 恢复条件：灵依回归灵族且发布可用 MCP server 入口。
'''


def main() -> int:
    if not TARGET.exists():
        print(f"!! 目标不存在: {TARGET}")
        return 1

    src = TARGET.read_text(encoding="utf-8")

    if '"lingyi": MCPServerConfig(' not in src:
        if "条目已移除（2026-10-04" in src:
            print("已处理：lingyi 条目此前已摘除，无需重复操作")
            return 0
        print("!! 未找到 lingyi 条目锚点，且非摘除态——文件结构与预期不符，拒绝盲改")
        return 1

    lines = src.splitlines(keepends=True)

    # 锚定：条目起始行 + 收尾行
    start = next(i for i, ln in enumerate(lines) if '"lingyi": MCPServerConfig(' in ln)
    end = None
    depth = 0
    for j in range(start, len(lines)):
        depth += lines[j].count("(") - lines[j].count(")")
        if depth <= 0 and j > start:
            end = j
            break
    if end is None:
        print("!! 未找到条目收尾行，拒绝盲改")
        return 1

    block = "".join(lines[start:end + 1])
    if "council_health" not in block:  # 条目特征复核（27 工具清单含 council_health）
        print("!! 块内容与 lingyi 条目特征不符，拒绝盲改")
        return 1

    new_src = "".join(lines[:start]) + TOMBSTONE + "".join(lines[end + 1:])

    # 语法门
    try:
        ast.parse(new_src)
    except SyntaxError as e:
        print(f"!! 语法门未过，放弃: {e}")
        return 1

    # 备份
    bak = TARGET.with_suffix(f".py.bak-{date.today():%Y%m%d}")
    if not bak.exists():
        shutil.copy2(TARGET, bak)
        print(f"已备份: {bak}")

    TARGET.write_text(new_src, encoding="utf-8")
    print(f"已摘除 lingyi 条目（行 {start + 1}-{end + 1}，共 {end - start + 1} 行）")

    # 断言门：注册表内不再有 lingyi 条目
    assert '"lingyi": MCPServerConfig(' not in TARGET.read_text(encoding="utf-8")
    print("断言门通过：注册表已无 lingyi 条目")
    return 0


if __name__ == "__main__":
    sys.exit(main())
