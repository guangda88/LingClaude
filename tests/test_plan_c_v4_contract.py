"""方案C v4 新模块的 API 契约测试（wiring_gate 反死模块守卫的消费面）。

背景：test_wiring_gate::TestNoDeadModules 要求 core/ 每个模块的每个 public
name 至少被一处 import（「定义即消费」纪律）。插片模块若只被测试文件 import
也算消费——但仅当测试真实行使该名字的契约，而非为过门禁而 import。

本文件因此只测「此前无消费者」的名字：
- context_engine: ContextEngine（ABC）、SummaryFraming（数据类）、SUMMARY_HEADLINE（常量契约）
- plugin_lifecycle: PluginFiber（数据类）、get_lifecycle_manager（单例）
- repair_card: RepairCard（数据类）、get_repair_board（单例）

不测已由 test_plan_c_v4.py 覆盖的 LifecycleManager/DefaultContextEngine 等。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from lingclaude.core.context_engine import (  # noqa: E402
    SUMMARY_HEADLINE,
    ContextEngine,
    SummaryFraming,
    make_framing,
)
from lingclaude.core.plugin_lifecycle import (  # noqa: E402
    LifecycleState,
    PluginFiber,
    get_lifecycle_manager,
)
from lingclaude.core.repair_card import (  # noqa: E402
    RepairCard,
    RepairState,
    get_repair_board,
)
from lingclaude.core.seam import SeamType  # noqa: E402


# ---- context_engine 契约 ----

def test_context_engine_is_abstract() -> None:
    """ABC 不可实例化——强制实现方走子类（contract seam 语义）。"""
    import pytest
    try:
        ContextEngine()  # type: ignore[abstract]
        raised = False
    except TypeError:
        raised = True
    assert raised, "ContextEngine 必须为 ABC（不可直接实例化）"


def test_summary_headline_is_stable_contract() -> None:
    """常量是压缩摘要的解析锚点，与 context_compression/compaction 消费方
    约定俗成——值漂移 = 历史回放解析失效，故冻结为显式契约。"""
    assert SUMMARY_HEADLINE == "## 压缩摘要"


def test_summary_framing_roundtrip() -> None:
    """framing 数据类 round-trip：make_framing 产出 ↔ 字段保真（含前缀格式锚点）。"""
    framing = make_framing(dropped_count=7, body="前情提要")
    assert isinstance(framing, SummaryFraming)
    assert framing.dropped_count == 7
    assert framing.body == "前情提要"
    # handoff 前缀格式是压缩摘要的解析契约（SUMMARY_HEADLINE + 轮数）
    assert framing.handoff_prefix == "## 压缩摘要（前 7 轮对话）"


# ---- plugin_lifecycle 契约 ----

def test_plugin_fiber_default_state_pending() -> None:
    """新建 fiber 必须从 PENDING 起步（六态状态机起点契约）。"""
    fiber = PluginFiber("t", factory=lambda: object())
    assert fiber.state is LifecycleState.PENDING
    assert fiber.instance is None
    assert fiber.epoch is None


def test_get_lifecycle_manager_is_singleton() -> None:
    """进程级单例：两次获取同一实例。"""
    m1 = get_lifecycle_manager()
    m2 = get_lifecycle_manager()
    assert m1 is m2


# ---- repair_card 契约 ----

def test_repair_card_defaults() -> None:
    """RepairCard 数据类默认值契约（target/symptom 必填，新建即 OPEN、无验证证据）。"""
    card = RepairCard(target="a.py", symptom="编译失败")
    assert card.card_id  # 默认自动生成
    assert card.state is RepairState.OPEN
    assert card.attempts == 0
    assert card.verification is None


def test_get_repair_board_is_singleton() -> None:
    """进程级单例：两次获取同一实例。"""
    b1 = get_repair_board()
    b2 = get_repair_board()
    assert b1 is b2
