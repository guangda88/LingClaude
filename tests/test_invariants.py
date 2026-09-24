"""不变量守护框架测试（建闸期③）。

覆盖：三值判定 / strict-observe 双模式 / plugin_lifecycle 边表与实测赋值点
一致性 / 假随机序列状态机探索（固定种子，hypothesis 风格 property 冒烟）。
"""
from __future__ import annotations

import random

import pytest

from lingclaude.core.invariants import (
    CLASS_ILLEGAL,
    CLASS_LEGAL,
    CLASS_UNCLASSIFIED,
    PLUGIN_LIFECYCLE_TABLE,
    StateInvariantGuard,
    TransitionTable,
    UndefinedTransitionError,
)


def test_three_value_classification():
    t = TransitionTable(edges={"A": {"B"}}, name="t")
    assert t.classify("A", "B") == CLASS_LEGAL
    assert t.classify("A", "C") == CLASS_ILLEGAL
    assert t.classify("Z", "B") == CLASS_UNCLASSIFIED  # 表未覆盖
    assert t.classify(None, "A") == CLASS_UNCLASSIFIED


def test_define_extends_edges():
    t = TransitionTable(edges={"A": {"B"}})
    t.define("B", {"A", "C"})
    assert t.classify("B", "C") == CLASS_LEGAL
    assert t.classify("A", "B") == CLASS_LEGAL


def test_strict_mode_raises_on_illegal():
    g = StateInvariantGuard(
        table=TransitionTable(edges={"A": {"B"}}), mode="strict")
    g.observe_transition("s1", "A", "B")  # legal 通过
    with pytest.raises(UndefinedTransitionError):
        g.observe_transition("s2", "A", "C")


def test_observe_mode_records_never_raises():
    g = StateInvariantGuard(
        table=TransitionTable(edges={"A": {"B"}}), mode="observe")
    g.observe_transition("s1", "A", "C")          # illegal
    g.observe_transition("s2", "Z", "C")          # unclassified
    assert len(g.violations) == 2
    assert g.violations[0].classification == CLASS_ILLEGAL
    assert g.violations[1].classification == CLASS_UNCLASSIFIED


def test_plugin_table_covers_real_assignments():
    """预置边表必须覆盖 plugin_lifecycle 实测全部赋值路径。"""
    t = TransitionTable(edges=PLUGIN_LIFECYCLE_TABLE, name="plugin")
    # attach 创建 → PENDING
    assert t.classify(None, "PENDING") == CLASS_LEGAL
    # _activate: PENDING/INACTIVE/FAILED → LOADING → ACTIVE/FAILED
    for frm in ("PENDING", "INACTIVE", "FAILED"):
        assert t.classify(frm, "LOADING") == CLASS_LEGAL
    assert t.classify("LOADING", "ACTIVE") == CLASS_LEGAL
    assert t.classify("LOADING", "FAILED") == CLASS_LEGAL  # _activate 异常→FAILED
    # _unload: ACTIVE/INACTIVE/FAILED → UNLOADING → INACTIVE
    for frm in ("ACTIVE", "INACTIVE", "FAILED"):
        assert t.classify(frm, "UNLOADING") == CLASS_LEGAL
    assert t.classify("UNLOADING", "INACTIVE") == CLASS_LEGAL
    # refresh 熔断拒绝：PENDING/INACTIVE/FAILED → INACTIVE
    for frm in ("PENDING", "INACTIVE", "FAILED"):
        assert t.classify(frm, "INACTIVE") == CLASS_LEGAL
    # 明确非法：ACTIVE → LOADING（必须先 UNLOADING）；ACTIVE → INACTIVE 直跳
    assert t.classify("ACTIVE", "LOADING") == CLASS_ILLEGAL
    assert t.classify("ACTIVE", "PENDING") == CLASS_ILLEGAL
    # UNLOADING 期禁止重新激活
    assert t.classify("UNLOADING", "LOADING") == CLASS_ILLEGAL


def test_property_smoke_random_sequences():
    """假随机 property 冒烟：随机动作序列下守卫与真实状态机零冲突、零误报。

    （hypothesis 未安装时的固定种子替代；安装后可无缝换 @given 策略）
    """
    rng = random.Random(20260925)
    t = TransitionTable(edges=PLUGIN_LIFECYCLE_TABLE, name="plugin")

    # 简化驱动：模拟 LifecycleManager 的合法动作集。
    # 观察粒度 = 真实赋值粒度：_activate 必然先 LOADING 再 ACTIVE/FAILED
    # （plugin_lifecycle.py:435→447/:452），模型同样发两次观察。
    actions = ["attach", "activate_ok", "activate_fail", "unload", "gate_deny"]
    ACTIVE_STATE = "ACTIVE"
    for _round in range(30):
        g = StateInvariantGuard(table=t, mode="strict")
        state = None
        for _step in range(40):
            a = rng.choice(actions)
            frm = state
            if a == "attach":
                if frm is not None:
                    # 真实语义（attach :167-170）：同名覆盖先卸旧 fiber
                    # （X→UNLOADING→INACTIVE），再建新 fiber（None→PENDING）
                    g.observe_transition("fiber", frm, "UNLOADING")
                    g.observe_transition("fiber", "UNLOADING", "INACTIVE")
                state = "PENDING"
                g.observe_transition("fiber", None, "PENDING")
            elif a in ("activate_ok", "activate_fail"):
                if frm is None:
                    continue
                if frm is ACTIVE_STATE:
                    # 真实语义（refresh :508-509）：ACTIVE fiber 再激活必须
                    # 先卸旧代（ACTIVE→UNLOADING→INACTIVE）才 _activate
                    g.observe_transition("fiber", "ACTIVE", "UNLOADING")
                    g.observe_transition("fiber", "UNLOADING", "INACTIVE")
                    frm = "INACTIVE"
                # 观察粒度=赋值粒度：LOADING 先行，再落 ACTIVE/FAILED
                g.observe_transition("fiber", frm, "LOADING")
                state = "ACTIVE" if a == "activate_ok" else "FAILED"
                g.observe_transition("fiber", "LOADING", state)
            elif a == "unload":
                if frm is None:
                    continue
                g.observe_transition("fiber", frm, "UNLOADING")
                state = "INACTIVE"
                g.observe_transition("fiber", "UNLOADING", state)
            elif a == "gate_deny":
                state = ("INACTIVE"
                         if frm in ("PENDING", "INACTIVE", "FAILED") else frm)
                if state != frm:
                    g.observe_transition("fiber", frm, state)
            else:
                continue
        assert g.violations == []


def test_guard_mode_validation():
    with pytest.raises(ValueError):
        StateInvariantGuard(mode="bogus")
