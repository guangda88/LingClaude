"""P15: Mixin↔SPECS 装配完整性契约 —— 灵元「插片 = 变化」实证。

背景：四家审计称「coding.py 10 Mixin 焊死（542 行）」。实测过时 ——
10 个 Mixin 已拆至 engine/tool_handlers/ 独立插片文件（每个 23-119 行），
coding.py 仅是继承组装层。T4 已把工具定义收敛到 tool_registration.SPECS 表。

本契约锁定三点（防回潮）：
1. 每个 SPECS.handler_attr 都能从 CodingRuntime 解析到方法（插片装配完整）。
2. tool_handlers/ 插片文件不 import coding.py（无反向依赖，插片独立可替换）。
3. Mixin 文件保持薄：每个插片文件 <= 200 行（防「插片变厚主干」回潮）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lingclaude.engine.coding import CodingRuntime
from lingclaude.engine.tool_registration import SPECS

SRC = Path(__file__).resolve().parent.parent / "lingclaude"
HANDLERS_DIR = SRC / "engine" / "tool_handlers"


def test_every_spec_handler_resolves_on_runtime() -> None:
    """契约1：SPECS 表每个 handler_attr 都能在 CodingRuntime 上解析到 callable。

    这证明「定义（SPECS）↔ 实现（Mixin）↔ 运行时（coding.py）」三角装配完整；
    任何一边断链（增 spec 忘写 handler / Mixin 被拆走）立即暴露。
    """
    rt = CodingRuntime()
    missing = [
        spec.handler_attr
        for spec in SPECS
        if not callable(getattr(rt, spec.handler_attr, None))
    ]
    assert not missing, (
        f"SPECS 表存在但 CodingRuntime 无法解析的 handler（插片断链）: {missing}"
    )


def test_handlers_do_not_import_coding_runtime() -> None:
    """契约2：tool_handlers 插片不反向依赖 coding.py。

    灵元「主干不依赖插片、插片可独立替换」：若某插片 import coding.py，
    说明它把主干逻辑焊进了插片，替换该插片会连带主干 —— 违规。
    """
    offenders: list[str] = []
    for py in sorted(HANDLERS_DIR.glob("*.py")):
        if py.name == "__init__.py":
            continue
        text = py.read_text(encoding="utf-8")
        if "import coding" in text or "from lingclaude.engine.coding" in text:
            offenders.append(py.name)
    assert not offenders, (
        f"tool_handlers 插片反向依赖 coding.py（应保持插片独立）: {offenders}"
    )


def test_mixin_files_stay_thin() -> None:
    """契约3：每个插片文件 <= 200 行（防「插片变厚主干」回潮）。

    灵元尺子「砍到最薄」同样适用于插片本身 —— 插片膨胀 = 新厚主干。
    """
    fat = [
        f"{py.name}:{len(py.read_text(encoding='utf-8').splitlines())}"
        for py in sorted(HANDLERS_DIR.glob("*.py"))
        if py.name != "__init__.py"
        and len(py.read_text(encoding="utf-8").splitlines()) > 200
    ]
    assert not fat, f"插片文件超 200 行（应继续拆分）: {fat}"
