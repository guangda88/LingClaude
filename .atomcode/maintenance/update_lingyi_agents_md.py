#!/usr/bin/env python3
"""AGENTS.md 定性补记: 在 Project Snapshot 的 Status 行后追加 linghealth 产品线定位。
幂等: 已含标记则跳过。含备份。"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

MARK = "[2026-10-04 linghealth]"

ADD = """
- **[2026-10-04 linghealth]** 本仓库承载 **灵康(linghealth) 平台的 AI 健康助手**（对话问诊/计划方案）。linghealth 经 `POST /api/v1/plan/generate` 调用本服务(:8902, 见 `linghealth/src/api/ai_dispatch.py:154`，模块注册于其 `config/default.yaml`)。2026-09-17 灵族 org 的「退出」条目指 MCP 编制变化，**非项目废弃**。边界要求：任何对话/方案输出必须保持「生活方式建议、非医疗诊断」声明。
"""


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: update_lingyi_agents_md.py <AGENTS.md 路径>", file=sys.stderr)
        return 1
    target = Path(sys.argv[1]).resolve()
    text = target.read_text(encoding="utf-8")
    if MARK in text:
        print("已处理: AGENTS.md 补记已存在(幂等)")
        return 0
    anchor = "\n## 2. Essential Commands"
    if anchor not in text:
        print("错误: 锚点(§2)未找到", file=sys.stderr)
        return 1
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = target.parent / f"{target.name}.bak-{stamp}"
    shutil.copy2(target, backup)
    print(f"已备份: {backup}")
    text = text.replace(anchor, ADD.rstrip() + "\n" + anchor, 1)
    target.write_text(text, encoding="utf-8")
    assert MARK in target.read_text(encoding="utf-8")
    print("已写入 linghealth 定性补记(§1 末尾)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
