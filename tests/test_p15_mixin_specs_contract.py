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


MIN_SPECS = 30  # 历史基线 32~37 的下限（2026-10-08 空表事故防线）


def test_specs_not_wiped() -> None:
    """契约4：SPECS 表不得低于历史基线下限（20261008 空表事故防线）。

    2026-10-08 16:55 SPECS 被清空为 ()，带参工具 schema 全部缺位、
    新会话工具全灭 2 小时+（详见 docs/audit/20261008_tool_registry_wipe_incident.md）。
    运行时闸门在 register_all_tools（拒启动），本契约把同一底线锁进 CI：
    空表/残表在任何测试环节立即暴露。
    """
    assert len(SPECS) >= MIN_SPECS, (
        f"SPECS 仅 {len(SPECS)} 条 < {MIN_SPECS}（历史基线 32~37），"
        "疑似注册表被清空 —— 20261008 事故防线触发"
    )


def test_register_all_tools_rejects_empty_specs() -> None:
    """契约5：register_all_tools 读侧闸门 —— 空 SPECS 必须 raise，不得静默注册 0 工具。"""
    import lingclaude.engine.tool_registration as mod

    original = mod.SPECS
    try:
        mod.SPECS = ()
        with pytest.raises(RuntimeError, match="20261008"):
            mod.register_all_tools(registry=None, runtime=None)
    finally:
        mod.SPECS = original


def test_extract_script_has_write_guard() -> None:
    """契约6：提取脚本必须带写侧闸门 —— 提取数过低时拒绝覆盖 SPECS 表。

    事故成因即脚本把空提取结果忠实写回（OUT.write_text 无守卫、非原子）。
    本契约锁定守卫存在且位于写入点之前，防止脚本被"简化"回无闸形态。
    """
    script = SRC.parent / "scripts" / "extract_tool_specs.py"
    text = script.read_text(encoding="utf-8")
    assert "len(specs) < 30" in text, (
        "extract_tool_specs.py 丢失写侧闸门（20261008 事故防线）"
    )
    assert "OUT.write_text" in text
    assert text.index("len(specs) < 30") < text.index("OUT.write_text"), (
        "写侧闸门必须位于 OUT.write_text 之前"
    )


def test_readonly_names_disjoint_from_write_scopes() -> None:
    """契约7（G14, 2026-10-08）：permissions.READ_ONLY_TOOLS 与 SPECS 的
    write/execute 集合互斥 —— 单源纪律锁定。

    背景：todo_write 注册表 scope 已于 9/17 修为 write（全量删除+重插存储），
    但 permissions.READ_ONLY_TOOLS 残留其名，导致同一工具在审批闸门
    （check_action 按名单判只读自动放行）与 plan_mode/prior_verifier
    （按 scope 判写域封禁/预检）两处事实相反。本契约令此类漂移永不复发：
    任一工具在注册表声明写/执行域、却在审批名单冒充只读，立即红。
    """
    from lingclaude.core.permissions import READ_ONLY_TOOLS

    leaks = sorted(
        s.name
        for s in SPECS
        if s.security_scope in ("write", "execute") and s.name in READ_ONLY_TOOLS
    )
    assert not leaks, (
        f"注册表 write/execute 工具混入 READ_ONLY_TOOLS（G14 类漂移）: {leaks}"
    )
