# -*- coding: utf-8 -*-
"""会话预算线纯核心测试（P1②）。

锚点：纯核心无 I/O——policy 对象直接构造，loader 只测投影规则；
接线（record 点/消费点）不在本文件范围，见 session_budget.py 模块 docstring。
"""
from __future__ import annotations

import pytest

from lingclaude.core.session_budget import (
    _KNOWN_DIMENSIONS,
    BudgetDelta,
    BudgetThresholds,
    BudgetTracker,
    BudgetVerdict,
    SessionBudgetLoader,
    SessionBudgetPolicy,
    _verdict_level,
    _safe_int,
)

# ── 单维度裁决 ──

# ── 单维度裁决：pause 优先 / warn / ok / 0=不限 ──

class TestVerdictLevel:
    def test_pause_takes_precedence(self):
        thr = BudgetThresholds(warn=10, pause=20)
        assert _verdict_level(20, thr) == "pause"
        assert _verdict_level(25, thr) == "pause"

    def test_warn_when_above_warn_below_pause(self):
        thr = BudgetThresholds(warn=10, pause=20)
        assert _verdict_level(10, thr) == "warn"
        assert _verdict_level(19, thr) == "warn"

    def test_ok_below_thresholds(self):
        assert _verdict_level(5, BudgetThresholds(warn=10, pause=20)) == "ok"

    def test_zero_thresholds_unlimited(self):
        thr = BudgetThresholds(0, 0)
        assert _verdict_level(10_000, thr) == "ok"

    def test_only_pause_configured(self):
        assert _verdict_level(5, BudgetThresholds(0, 5)) == "pause"
        assert _verdict_level(4, BudgetThresholds(0, 5)) == "ok"


# ── 计数器：白名单 / 泛化记录 / 快照 ──

class TestTracker:
    def test_record_accumulates(self):
        t = BudgetTracker()
        t.record(BudgetDelta("tool_calls", 1))
        t.record(BudgetDelta("tool_calls", 1))
        t.record(BudgetDelta("model_calls", 3))
        assert t.snapshot() == {"tool_calls": 2, "model_calls": 3}

    def test_zero_amount_recorded(self):
        t = BudgetTracker()
        t.record(BudgetDelta("input_tokens", 0))
        assert t.snapshot() == {"input_tokens": 0}

    def test_unknown_dimension_rejected(self, caplog):
        t = BudgetTracker()
        t.record(BudgetDelta("nuclear_launches", 1))
        assert "nuclear_launches" not in t.snapshot()

    def test_known_dimensions_whitelist(self):
        assert _KNOWN_DIMENSIONS == frozenset(
            {"tool_calls", "model_calls", "input_tokens", "output_tokens"}
        )

    def test_snapshot_is_copy(self):
        t = BudgetTracker()
        t.record(BudgetDelta("tool_calls", 1))
        snap = t.snapshot()
        snap["tool_calls"] = 999
        assert t.snapshot()["tool_calls"] == 1


# ── 评估聚合：pause 优先 / 跨维度全列 / 未启用 ──

def _policy(enabled=True, **dims):
    return SessionBudgetPolicy(
        enabled=enabled,
        budgets={k: BudgetThresholds(**v) for k, v in dims.items()},
    )


class TestEvaluation:
    def test_disabled_policy_ok(self):
        t = BudgetTracker()
        t.record(BudgetDelta("tool_calls", 999))
        ev = t.evaluate(_policy(enabled=False, tool_calls={"warn": 1, "pause": 2}))
        assert ev.ok and not ev.enabled
        assert "未启用" in ev.summary()

    def test_pause_when_crossed(self):
        t = BudgetTracker()
        for _ in range(3):
            t.record(BudgetDelta("tool_calls", 1))
        ev = t.evaluate(_policy(tool_calls={"warn": 1, "pause": 3}))
        assert not ev.ok
        assert len(ev.pauses) == 1
        assert ev.pauses[0].dimension == "tool_calls"

    def test_cross_dimension_full_report(self):
        t = BudgetTracker()
        t.record(BudgetDelta("tool_calls", 5))
        t.record(BudgetDelta("model_calls", 5))
        ev = t.evaluate(_policy(
            tool_calls={"warn": 0, "pause": 5},
            model_calls={"warn": 2, "pause": 0},
            input_tokens={"warn": 0, "pause": 0},
        ))
        # pause(tool_calls) 与 warn(model_calls) 同时列报；input_tokens 未记录=0 恒 ok
        assert ev.pauses and ev.warns
        assert any(v.dimension == "input_tokens" and v.level == "ok" for v in ev.verdicts)
        s = ev.summary()
        assert "tool_calls" in s and "model_calls" in s

    def test_unevaluated_dimensions_report_zero(self):
        t = BudgetTracker()
        ev = t.evaluate(_policy(output_tokens={"warn": 100, "pause": 200}))
        assert ev.ok
        assert ev.verdicts[0].current == 0

    def test_evaluate_exception_fail_open(self, monkeypatch):
        t = BudgetTracker()
        # _evaluate 内部炸 → OK + reason，不抛（fail-open 契约）
        monkeypatch.setattr(
            type(t), "_evaluate",
            lambda self, p: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        ev = t.evaluate(_policy(enabled=True))
        assert ev.ok and "boom" in ev.reason


# ── loader 投影：缺文件暗发车 / 坏值容错 / 白名单外维度过滤 ──

class TestLoader:
    def test_missing_policy_dark_launch(self, monkeypatch, tmp_path):
        # policies 目录指到空 tmp → get 返回 {} → disabled 缺省
        monkeypatch.setattr(policy_loader_mod, "policies_dir", lambda: tmp_path)
        monkeypatch.setattr(policy_loader_mod, "get", lambda name: {})
        p = SessionBudgetLoader.load()
        assert p.enabled is False and p.budgets == {}

    def test_partial_yaml_projection(self, monkeypatch):
        raw = {
            "enabled": True,
            "budgets": {
                "tool_calls": {"warn": 10, "pause": 20},
                "bad_dim": {"warn": 1, "pause": 2},   # 非 dict 值下方过滤；此处维度未知→仍投影，评估侧白名单管
                "input_tokens": {"warn": None, "pause": "30"},
            },
        }
        monkeypatch.setattr(policy_loader_mod, "get", lambda name: raw)
        p = SessionBudgetLoader.load()
        assert p.enabled is True
        assert p.budgets["tool_calls"].warn == 10
        # None → 0；字符串数字 → 30
        assert p.budgets["input_tokens"].warn == 0
        assert p.budgets["input_tokens"].pause == 30

    def test_enabled_key_missing_disabled(self, monkeypatch):
        monkeypatch.setattr(policy_loader_mod, "get", lambda name: {"budgets": {}})
        p = SessionBudgetLoader.load()
        assert p.enabled is False

    def test_exception_fail_open_disabled(self, monkeypatch):
        def boom(name):
            raise RuntimeError("disk gone")
        monkeypatch.setattr(policy_loader_mod, "get", boom)
        p = SessionBudgetLoader.load()
        assert p.enabled is False


import lingclaude.core.policy_loader as policy_loader_mod  # noqa: E402
