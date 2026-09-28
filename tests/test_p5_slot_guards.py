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


# ---------------------------------------------------------------------------
# G10 主干基线守卫（口径 A，2026-09-28 裁定）
# ---------------------------------------------------------------------------
class TestG10TrunkBaseline:
    """全主干文件级直构棘轮（对齐裁决②，替代「只扫 __init__」的窄口径）。

    三区语义（data/arch_ledger/g10_trunk_baseline.yaml）：
    - assembly_layer    coding.py / coding_wiring.py：插片构造只允许出现在
                        manifest 工厂函数体内（_initial_* / assemble_*），区外命中即红。
    - controlled_rebuild query_engine*.py：create_provider 受控重建，计数只降不升。
    - loop_locals       loop/*.py：_ToolLoopDetector() 函数内局部构造，计数只降不升。

    tool_handlers/*.py 是主干装配的插片（J1 合法变化面），不在本守卫扫描面。
    """

    _BASELINE_PATH = "data/arch_ledger/g10_trunk_baseline.yaml"

    # 装配层豁免工厂：这些函数体内的构造是合法装配行为
    _FACTORY_FN_PREFIXES = ("_initial_", "_resolve_", "assemble_")

    # 各区监控的插片类构造名
    _PLUG_CLASSES = (
        "SessionRuntime",
        "TodoStore",
        "PatternRecognizer",
        "VerifyCadenceHook",
        "VerificationGate",
        "_ToolLoopDetector",
        "create_provider",
    )

    @staticmethod
    def _load_baseline() -> dict:
        from pathlib import Path

        import yaml

        p = Path(__file__).resolve().parents[1] / TestG10TrunkBaseline._BASELINE_PATH
        assert p.is_file(), f"G10 基线缺失：{p}"
        return yaml.safe_load(p.read_text(encoding="utf-8"))

    @staticmethod
    def _repo_root():
        from pathlib import Path

        return Path(__file__).resolve().parents[1]

    def _count_calls_per_function(self, rel_path: str) -> dict[str, int]:
        """AST 解析：{函数名: 该函数体内插片类构造调用总数}。

        裸类名调用（排除 Lazy*/包装器：Call.func 为 Name 且名恰为类名）。
        嵌套函数归入其自身函数名（AST 自然递归）。
        """
        import ast

        src = (self._repo_root() / rel_path).read_text(encoding="utf-8")
        tree = ast.parse(src, filename=rel_path)
        counts: dict[str, int] = {}
        plug = set(self._PLUG_CLASSES)

        class _FnVisitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.stack: list[str] = []

            def _visit_fn(self, node) -> None:
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

            visit_FunctionDef = _visit_fn
            visit_AsyncFunctionDef = _visit_fn

            def visit_Call(self, node) -> None:
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else (
                    fn.attr if isinstance(fn, ast.Attribute) else None
                )
                if name in plug:
                    owner = self.stack[-1] if self.stack else "<module>"
                    counts[owner] = counts.get(owner, 0) + 1
                self.generic_visit(node)

        _FnVisitor().visit(tree)
        return counts

    def test_baseline_file_wellformed(self) -> None:
        b = self._load_baseline()
        assert isinstance(b.get("assembly_layer"), list) and b["assembly_layer"]
        assert isinstance(b.get("controlled_rebuild"), dict)
        assert isinstance(b.get("loop_locals"), dict)
        for rel in list(b["assembly_layer"]) + list(b["controlled_rebuild"]) + list(b["loop_locals"]):
            assert (self._repo_root() / rel).is_file(), f"基线文件清单中的 {rel} 不存在"

    def test_assembly_layer_zero_outside_factories(self) -> None:
        """严格区：插片构造只允许在 manifest 工厂函数体内，区外命中即红。"""
        b = self._load_baseline()
        violations: list[str] = []
        for rel in b["assembly_layer"]:
            for fn, n in self._count_calls_per_function(rel).items():
                if fn == "<module>" or not fn.startswith(self._FACTORY_FN_PREFIXES):
                    violations.append(f"{rel}::{fn} ×{n}")
        assert not violations, (
            "G10 棘轮红（装配层）：插片构造出现在工厂函数体外 —— "
            + "; ".join(violations)
            + "。须移入 SLOT_WIRING_MANIFEST 工厂或下调此调用。"
        )

    def test_controlled_rebuild_ratchets_only_down(self) -> None:
        """受控重建区：create_provider 计数 ≤ 基线（只降不升）。"""
        b = self._load_baseline()
        for rel, ceiling in b["controlled_rebuild"].items():
            total = sum(self._count_calls_per_function(rel).values())
            assert total <= ceiling, (
                f"G10 棘轮红（受控重建）：{rel} 构造计数 {total} > 基线 {ceiling} —— "
                f"新增 provider 重建点须走槽机制；合法减少请下调基线（只紧不松）"
            )

    def test_loop_locals_ratchets_only_down(self) -> None:
        """循环层局部构造区：_ToolLoopDetector 计数 ≤ 基线（只降不升）。"""
        b = self._load_baseline()
        for rel, ceiling in b["loop_locals"].items():
            total = sum(self._count_calls_per_function(rel).values())
            assert total <= ceiling, (
                f"G10 棘轮红（循环层）：{rel} 构造计数 {total} > 基线 {ceiling} —— "
                f"合法减少请下调基线（只紧不松）"
            )
