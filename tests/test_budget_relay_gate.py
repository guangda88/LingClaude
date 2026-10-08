"""R1/R2 预算预警与遗言轮测试（panel_20261008_budget FINAL_synthesis 定案）。

覆盖面：
  1. warn_injection 档位推进：75% 注入检查点档、90% 注入交接档、幂等（同档只注一次）
  2. try_last_rites 资格：无交接物拒绝 / 用后即焚 / reset 重置闩锁（codex 修正）
  3. fail-open：策略 disabled 时不注入不拦
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lingclaude.core import session_budget_gate as gate
from lingclaude.core.session_budget import BudgetDelta


@pytest.fixture()
def fresh_gate(monkeypatch):
    """干净单例 + 干净闩锁。"""
    monkeypatch.setattr(gate, "_tracker", None)
    gate._relay_state["warn_stage"] = 0
    gate._relay_state["handover_used"] = False
    yield


def _inject_tracker(monkeypatch, fraction: float, pause: int = 1000):
    """注入受控策略 + 指定用量（不依赖真机 yaml 的 20M 阈值）。"""
    import lingclaude.core.session_budget as sb

    pol = sb.SessionBudgetPolicy(
        enabled=True,
        budgets={"input_tokens": sb.BudgetThresholds(warn=int(pause * 0.5), pause=pause)},
    )
    monkeypatch.setattr(sb.SessionBudgetLoader, "load", staticmethod(lambda: pol))
    t = gate.get_tracker()
    t.record(BudgetDelta("input_tokens", int(pause * fraction)))
    return t


class TestWarnInjection:
    def test_75_stage_injects_checkpoint_text(self, fresh_gate, monkeypatch):
        _inject_tracker(monkeypatch, 0.80)  # 80% ≥ 75% 档
        text = gate.warn_injection()
        assert text is not None and "检查点" in text

    def test_90_stage_injects_handover_text(self, fresh_gate, monkeypatch):
        gate._relay_state["warn_stage"] = 1  # 第一档已注入
        _inject_tracker(monkeypatch, 0.92)
        text = gate.warn_injection()
        assert text is not None and "交接" in text and "硬挂起" in text

    def test_idempotent_same_stage(self, fresh_gate, monkeypatch):
        _inject_tracker(monkeypatch, 0.80)
        first = gate.warn_injection()
        assert first is not None
        assert gate.warn_injection() is None  # 同档幂等：不再注入

    def test_below_threshold_silent(self, fresh_gate, monkeypatch):
        _inject_tracker(monkeypatch, 0.50)
        assert gate.warn_injection() is None

    def test_disabled_failopen(self, fresh_gate, monkeypatch):
        monkeypatch.setattr(
            type(gate.get_tracker()), "evaluate_current",
            lambda self: None, raising=False,
        )
        # evaluate_current 异常/缺席路径必须返回 None（fail-open）
        monkeypatch.setattr(
            gate, "get_tracker",
            lambda: (_ for _ in ()).throw(RuntimeError("no tracker")),
        )
        assert gate.warn_injection() is None


class TestLastRites:
    def test_no_artifacts_rejected(self, fresh_gate):
        assert gate.try_last_rites(has_relay_marker=False, has_todos=False) is False
        assert gate._relay_state["handover_used"] is False  # 拒绝不焚

    def test_granted_then_burned(self, fresh_gate):
        assert gate.try_last_rites(has_relay_marker=True, has_todos=False) is True
        assert gate.try_last_rites(has_relay_marker=True, has_todos=True) is False  # 用后即焚

    def test_reset_relocks(self, fresh_gate):
        gate.try_last_rites(has_relay_marker=True, has_todos=True)
        gate._relay_state["warn_stage"] = 2
        gate.reset()
        assert gate._relay_state["handover_used"] is False  # codex 修正：reset 重置闩锁
        assert gate._relay_state["warn_stage"] == 0
        assert gate.try_last_rites(has_relay_marker=True, has_todos=False) is True

    def test_marker_alone_suffices(self, fresh_gate):
        assert gate.try_last_rites(has_relay_marker=True, has_todos=False) is True
