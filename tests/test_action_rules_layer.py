"""NanoJev rules 层测试：decision_thresholds / action_budget / success_gate 表驱动。

隔离纪律（2026-10-22 教训内建）：
  - action_budget 是进程内全局计数器 → 所有计数用例独占会话 id + 用后 release；
  - thresholds 经 PolicyLoader mtime 缓存 → monkeypatch 掉缓存目录级隔离不现实，
    改为直接 monkeypatch get_thresholds 返回值（单测语义），热更链路由
    test_thresholds_yaml_parsable 保证 yaml 本体可解析。
"""
from __future__ import annotations

import pytest
import yaml

from lingclaude.core import action_budget
from lingclaude.core.decision_thresholds import get_value, get_thresholds

_POLICY = "lingclaude/core/policies/decision_thresholds.yaml"


class TestThresholds:
    def test_yaml_parsable_and_keys(self):
        data = yaml.safe_load(open(_POLICY, encoding="utf-8"))
        assert isinstance(data, dict)
        assert data["success"]["gray_base"] == 0.5
        assert data["limits"]["max_actions_per_session"] == 500

    def test_read_via_loader(self):
        th = get_thresholds()
        assert th.get("limits", {}).get("max_actions_per_session") == 500

    def test_clamped(self, monkeypatch):
        # yaml 抬成天文数字也过不了代码侧钳制（阈值表是调参面，不是提权面）
        monkeypatch.setattr(
            "lingclaude.core.decision_thresholds.get_thresholds",
            lambda: {"success": {"gray_base": 9.9}, "limits": {"max_actions_per_session": -5}},
        )
        assert get_value("success", "gray_base") == 0.95
        assert get_value("limits", "max_actions_per_session") == 1.0

    def test_missing_falls_back(self, monkeypatch):
        monkeypatch.setattr(
            "lingclaude.core.decision_thresholds.get_thresholds", lambda: {}
        )
        assert get_value("success", "gray_base") == 0.5
        assert get_value("limits", "max_actions_per_session") == 500.0


class TestActionBudget:
    def setup_method(self):
        action_budget._COUNTERS.clear()
        action_budget._WARNED.clear()

    def teardown_method(self):
        action_budget._COUNTERS.clear()
        action_budget._WARNED.clear()

    def test_bump_counts(self):
        used, limit = action_budget.bump("sess-budget-a")
        assert (used, limit) == (1, 500)
        used, _ = action_budget.bump("sess-budget-a")
        assert used == 2
        # 独立会话互不干扰
        used2, _ = action_budget.bump("sess-budget-b")
        assert used2 == 1

    def test_burn_out_denies(self, monkeypatch):
        monkeypatch.setattr(action_budget, "_limit", lambda: 3)
        guard = action_budget.make_step_budget_guard(lambda: "sess-burn")
        dec = None
        for _ in range(3):
            dec = guard(None, None)
            assert dec.decision == "abstain"
        dec = guard(None, None)  # 第 4 步 → 烧尽
        assert dec.decision == "deny"
        assert "硬闸" in dec.reason

    def test_warn_recorded_once(self, monkeypatch):
        events = []
        monkeypatch.setattr(action_budget, "_limit", lambda: 10)
        monkeypatch.setattr(action_budget, "_warn_ratio", lambda: 0.5)

        import lingclaude.core.decision_log as dl

        monkeypatch.setattr(
            dl, "record_decision",
            lambda **kw: events.append(kw.get("action")),
        )
        guard = action_budget.make_step_budget_guard(lambda: "sess-warn")
        for _ in range(5):
            assert guard(None, None).decision == "abstain"
        assert events == ["step_budget_warn"]  # 第 5 步触发一次
        guard(None, None)
        guard(None, None)
        assert events == ["step_budget_warn"]  # 不重复预警

    def test_guard_exception_fail_open(self, monkeypatch):
        def boom():
            raise RuntimeError("provider exploded")

        guard = action_budget.make_step_budget_guard(boom)
        dec = guard(None, None)
        assert dec.decision == "abstain"

    def test_release_session(self):
        action_budget.bump("sess-rel")
        assert action_budget.current("sess-rel") == 1
        action_budget.release_session("sess-rel")
        assert action_budget.current("sess-rel") == 0
        assert "sess-rel" not in action_budget._WARNED


class TestSuccessGateTableDriven:
    def test_gray_base_from_table(self, monkeypatch):
        from lingclaude.core.success_gate import gate_success

        # 关掉 spec_decision noul 兜底（本机启用中会拦截灰区），单测表驱动分支
        monkeypatch.setattr("lingclaude.core.policy_loader.get", lambda name: {})
        monkeypatch.setattr(
            "lingclaude.core.decision_thresholds.get_thresholds",
            lambda: {"success": {"gray_base": 0.7}},
        )
        v = gate_success("bash", {"stdout": "hello world"})  # 无强信号 → 灰区分支
        assert v.p_success == 0.7
        assert v.is_success is None  # 保守不定论语义不变
        assert v.source == "neutral"
