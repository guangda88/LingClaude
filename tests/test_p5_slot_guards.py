"""P5 红→绿验收：灵元插片守卫门禁化。

对应主方案 §六守卫：
- 槽数预算 ≤8（防"槽地狱"）
- G10 直构计数棘轮：CodingRuntime.__init__ 插片直构 = 0（只降不升）
- 插片注册数据化守卫：真插片槽注册必须走 SLOT_WIRING_MANIFEST（不许 __init__ 内联）

这些守卫防止 P1-P4 消灭的双轨制/直构随新提交回流。
"""

from __future__ import annotations

import inspect
import re

from lingclaude.core.slot import MAX_SLOTS
from lingclaude.engine.coding_wiring import SLOT_WIRING_MANIFEST


# ---------------------------------------------------------------------------
# 槽数预算守卫
# ---------------------------------------------------------------------------
class TestSlotBudgetGuard:
    def test_slot_wiring_manifest_within_budget(self) -> None:
        """SLOT_WIRING_MANIFEST 声明的真插片槽数 ≤ MAX_SLOTS（防槽地狱）。"""
        assert len(SLOT_WIRING_MANIFEST) <= MAX_SLOTS, (
            f"真插片槽 {len(SLOT_WIRING_MANIFEST)} 超过预算 {MAX_SLOTS} —— 槽地狱红线"
        )

    def test_max_slots_is_eight(self) -> None:
        assert MAX_SLOTS == 8


# ---------------------------------------------------------------------------
# G10 直构计数棘轮（基线 0，只降不升）
# ---------------------------------------------------------------------------
class TestG10NoInlineConstruction:
    """CodingRuntime.__init__ 不得直构插片类（须走 SLOT manifest / 轻通道）。

    已知的插片类（须经 manifest/工厂，不许 __init__ 内 `Class()` 直构）：
    SessionRuntime / TodoStore / PatternRecognizer / VerifyCadenceHook /
    _ToolLoopDetector / create_provider。
     SlotManager / LightChannelRuntime 是主干协议层（冻结，允许）。
    """

    _PLUG_CLASS_CALLS = (
        "SessionRuntime(",
        "TodoStore(",
        "PatternRecognizer(",
        "VerifyCadenceHook(",
        "_ToolLoopDetector(",
        "create_provider(",
        "VerificationGate(",  # 须走 from_config 工厂 / 轻通道，不许裸直构
    )

    def test_init_no_inline_plug_construction(self) -> None:
        from lingclaude.engine import coding

        src = inspect.getsource(coding.CodingRuntime.__init__)
        for call in self._PLUG_CLASS_CALLS:
            # 裸类名匹配：前面非字母/下划线（排除 LazyVerificationGate /
            # LazyPatternRecognizer 等包装器前缀），只抓真直构
            class_name = call[:-1]  # 去尾部 (
            hits = re.findall(rf"(?<![A-Za-z_]){re.escape(class_name)}\(", src)
            assert not hits, (
                f"G10 棘轮红：__init__ 裸直构 {call} —— 须走 SLOT_WIRING_MANIFEST"
                f"（真插片槽）或 LightChannelRuntime（轻通道）"
            )

    def test_init_uses_slot_manifest_assembly(self) -> None:
        """__init__ 必须经 assemble_coding_slots 注册真插片槽（不许 _slot_manager.register 内联）。"""
        from lingclaude.engine import coding

        src = inspect.getsource(coding.CodingRuntime.__init__)
        assert "assemble_coding_slots" in src, "G10：真插片槽须走 assemble_coding_slots（SLOT manifest）"
        assert "_slot_manager.register(" not in src, (
            "G10 棘轮红：__init__ 内联 _slot_manager.register —— 须走 SLOT_WIRING_MANIFEST 数据化"
        )


# ---------------------------------------------------------------------------
# 插片注册数据化守卫（SLOT_WIRING_MANIFEST 覆盖 3 真插片）
# ---------------------------------------------------------------------------
class TestRegistrationDatafication:
    def test_three_real_slots_in_manifest(self) -> None:
        names = [s.slot_name for s in SLOT_WIRING_MANIFEST]
        for expected in ("model_provider", "todo_store", "session_runtime"):
            assert expected in names, f"真插片槽 {expected} 未在 SLOT_WIRING_MANIFEST 声明"

    def test_no_duplicate_slot_names(self) -> None:
        names = [s.slot_name for s in SLOT_WIRING_MANIFEST]
        assert len(names) == len(set(names)), "SLOT_WIRING_MANIFEST 有重复槽名（双注册表漂移）"

    def test_all_slots_have_factory(self) -> None:
        for spec in SLOT_WIRING_MANIFEST:
            assert callable(spec.initial), f"槽 {spec.slot_name} 缺 initial 工厂"
