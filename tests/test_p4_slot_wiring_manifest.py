"""P4 红→绿验收：3 个真插片槽注册数据化（SLOT_WIRING_MANIFEST）。

对应主方案 §八 P4 验收：装配一致性测试绿。双轨制消灭——主干 __init__ 不再
内联注册插片槽，改遍历 coding_wiring.SLOT_WIRING_MANIFEST（装配数据化，
与 14 项工具 manifest 同一装配哲学）。

红因（改动前）：__init__ 内联 self._slot_manager.register(...) 3 处，
插片注册不在 manifest，新增协作者需改主干。
改动后：SLOT_WIRING_MANIFEST 声明 3 槽，assemble_coding_slots 统一注册。
"""

from __future__ import annotations

from pathlib import Path

from lingclaude.core.config import lingclaudeConfig
from lingclaude.engine.coding import CodingRuntime
from lingclaude.engine.coding_wiring import (
    SLOT_WIRING_MANIFEST,
    CodingSlotWiringSpec,
    assemble_coding_slots,
)


def _runtime(tmp_path: Path) -> CodingRuntime:
    import os

    old = os.environ.get("LINGCLAUDE_DATA_DIR")
    os.environ["LINGCLAUDE_DATA_DIR"] = str(tmp_path)
    try:
        return CodingRuntime(lingclaudeConfig())
    finally:
        if old is None:
            os.environ.pop("LINGCLAUDE_DATA_DIR", None)
        else:
            os.environ["LINGCLAUDE_DATA_DIR"] = old


# ---------------------------------------------------------------------------
# manifest 数据化结构
# ---------------------------------------------------------------------------
class TestSlotWiringManifest:
    def test_manifest_declares_three_slots(self) -> None:
        names = [s.slot_name for s in SLOT_WIRING_MANIFEST]
        assert names == ["model_provider", "todo_store", "session_runtime"]

    def test_entries_are_slot_specs(self) -> None:
        for spec in SLOT_WIRING_MANIFEST:
            assert isinstance(spec, CodingSlotWiringSpec)
            assert spec.attr.startswith("_")  # 装配到 runtime 的私有属性

    def test_todo_and_session_transparent(self) -> None:
        transparent = {s.slot_name: s.transparent for s in SLOT_WIRING_MANIFEST}
        assert transparent["todo_store"] is True
        assert transparent["session_runtime"] is True
        assert transparent["model_provider"] is False  # 普通 SlotHandle


# ---------------------------------------------------------------------------
# 装配一致性：__init__ 走 manifest，属性就位且句柄类型正确
# ---------------------------------------------------------------------------
class TestAssembledConsistency:
    def test_runtime_slots_registered_via_manifest(self, tmp_path: Path) -> None:
        rt = _runtime(tmp_path)
        # manifest 声明的槽都在 SlotManager 注册
        for spec in SLOT_WIRING_MANIFEST:
            assert spec.slot_name in rt._slot_manager.names()

    def test_attrs_set_on_runtime(self, tmp_path: Path) -> None:
        rt = _runtime(tmp_path)
        assert hasattr(rt, "_todo_store")
        assert hasattr(rt, "_session_runtime")
        assert hasattr(rt, "_model_provider")

    def test_assemble_coding_slots_idempotent_registration(self, tmp_path: Path) -> None:
        rt = _runtime(tmp_path)
        # 重复装配会触发"已注册"防线（双注册表漂移防护）
        from lingclaude.engine.coding_wiring import CodingWiringContext

        try:
            assemble_coding_slots(
                CodingWiringContext(runtime=rt, sandbox_policy=None), rt._slot_manager
            )
            raise AssertionError("应触发已注册防线")
        except ValueError as e:
            assert "已注册" in str(e)


# ---------------------------------------------------------------------------
# 双轨制消灭：主干 __init__ 无内联 register
# ---------------------------------------------------------------------------
class TestNoInlineRegistration:
    def test_init_uses_manifest_not_inline_register(self) -> None:
        import inspect

        from lingclaude.engine import coding

        src = inspect.getsource(coding.CodingRuntime.__init__)
        # __init__ 不应再出现 self._slot_manager.register( 内联调用
        # （应改调 assemble_coding_slots）
        assert "assemble_coding_slots" in src
        assert ".register(" not in src.split("assemble_coding_slots")[0].split("__init__")[-1] or True
        # 强校验：__init__ 体内无 _slot_manager.register(
        body = src.split("def __init__", 1)[1]
        assert "_slot_manager.register(" not in body
