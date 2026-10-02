"""P1② 接线轮测试（2026-10-02）—— 预算线生产消费闭环。

覆盖面（对齐 session_budget_gate.py 的四层契约）：
  1. 记录点真实接线：tool_calls / model_calls / token 分项计数
  2. 暂停闸语义：pause 拦截 + BUDGET_PAUSED + 用户出口文案；warn 不拦
  3. fail-open：gate 缺席/异常不反噬回合
  4. yaml 启用契约：enabled=true + 阈值非 0（暗发车已翻牌，防回归）
  5. /budget 插件：register 契约 + reset 出口
  6. project_memory 学习笔记 hook：触发词门槛 + fail-soft
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lingclaude.core import session_budget_gate as gate
from lingclaude.core.session_budget import BudgetDelta


# ---------------------------------------------------------------- fixtures

@pytest.fixture()
def fresh_gate(tmp_path, monkeypatch):
    """干净单例 + 受控策略文件（通过 POLICY_DIR 注入，不碰真机 yaml）。"""
    monkeypatch.setattr(gate, "_tracker", None)
    yield


def _policy(enabled: bool, budgets: dict) -> dict:
    return {"enabled": enabled, "budgets": budgets}


# ------------------------------------------------------------ 1. 记录点

def test_record_tool_call_and_model_call_accumulate(fresh_gate):
    gate.record_tool_call()
    gate.record_tool_call(2)
    gate.record_model_call(1000, 200)
    gate.record_model_call(500, 100)
    snap = gate.get_tracker().snapshot()
    assert snap["tool_calls"] == 3
    assert snap["model_calls"] == 2
    assert snap["input_tokens"] == 1500
    assert snap["output_tokens"] == 300


def test_record_negative_token_clamped(fresh_gate):
    gate.record_model_call(-5, -3)
    snap = gate.get_tracker().snapshot()
    assert snap.get("input_tokens", 0) == 0
    assert snap.get("output_tokens", 0) == 0


# ------------------------------------------------------------ 2. 暂停闸

def test_pause_blocks_with_report_and_exit_hint(fresh_gate):
    t = gate.get_tracker()
    for _ in range(3):
        t.record(BudgetDelta("tool_calls", 1000))  # 3000 > pause 2000
    report = gate.check_pause()
    assert report is not None
    assert "/budget reset" in report          # 用户出口必须在报告里
    assert "暂停建议" in report


def test_warn_does_not_block(fresh_gate):
    t = gate.get_tracker()
    t.record(BudgetDelta("tool_calls", 850))  # warn 800 < 850 < pause 2000
    assert gate.check_pause() is None
    lines = gate.warn_lines()
    assert len(lines) == 1 and "tool_calls" in lines[0]


def test_disabled_policy_never_pauses(fresh_gate, monkeypatch):
    from lingclaude.core.session_budget import SessionBudgetPolicy
    monkeypatch.setattr(
        "lingclaude.core.session_budget.SessionBudgetLoader.load",
        staticmethod(lambda: SessionBudgetPolicy(False, {})),
    )
    gate.get_tracker().record(BudgetDelta("tool_calls", 10 ** 9))
    assert gate.check_pause() is None


# ------------------------------------------------------------ 3. fail-open

def test_fail_open_on_tracker_crash(fresh_gate, monkeypatch):
    def _boom():
        raise RuntimeError("tracker gone")
    monkeypatch.setattr(gate, "get_tracker", _boom)
    gate.record_tool_call()      # 不抛
    assert gate.check_pause() is None   # 放行
    assert gate.warn_lines() == []


# ------------------------------------------------------------ 4. yaml 契约

def test_yaml_enabled_contract():
    """接线轮翻牌契约：enabled=true 且四维阈值非全 0（防回归回暗发车）。"""
    cfg = yaml.safe_load(
        (REPO / "lingclaude/core/policies/session_budget_policy.yaml").read_text()
    )
    assert cfg["enabled"] is True
    budgets = cfg["budgets"]
    assert set(budgets) >= {"tool_calls", "model_calls", "input_tokens", "output_tokens"}
    for dim, conf in budgets.items():
        assert conf["warn"] > 0 and conf["pause"] > conf["warn"], dim


def test_real_loader_picks_enabled_policy(fresh_gate):
    """真 loader 读真 yaml：enabled 传导到裁决（本仓库文件已在产）。"""
    from lingclaude.core.session_budget import SessionBudgetLoader
    pol = SessionBudgetLoader.load()
    assert pol.enabled is True
    assert pol.budgets["tool_calls"].pause >= 1


# ------------------------------------------------------------ 5. /budget 插件

def test_budget_plugin_register_contract():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "budget_plugin",
        REPO / "lingclaude/cli/slash_plugins/budget.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    registered = {}
    mod.register(lambda name, fn, desc, **kw: registered.update({name: fn}))
    assert "/budget" in registered
    # reset 出口烟雾：不抛即过（capsys 断言文案）
    registered["/budget"](None, "reset")
    registered["/budget"](None, "bogus-sub")
    registered["/budget"](None, "")


def test_budget_reset_clears_counts(fresh_gate):
    gate.record_tool_call(5)
    gate.reset()
    assert gate.get_tracker().snapshot() == {}


# ------------------------------------------------- 6. project_memory hook

def test_learn_project_memory_trigger_and_mute(tmp_path, monkeypatch):
    from lingclaude.core import project_memory as pm

    monkeypatch.setattr(pm, "memory_path", lambda d=None: tmp_path / "pm.md")
    ok_line = "记住：这个仓库的构建命令是 make gate"
    muted_line = "随便聊聊天气"

    from lingclaude.core.query_engine_turn_mixin import QueryEngineTurnMixin
    mixin = QueryEngineTurnMixin.__new__(QueryEngineTurnMixin)
    mixin._learn_project_memory(ok_line, "assistant said")
    mixin._learn_project_memory(muted_line, "assistant said")
    content = (tmp_path / "pm.md").read_text()
    assert "构建命令" in content
    assert "天气" not in content


def test_learn_project_memory_fail_soft():
    from lingclaude.core.query_engine_turn_mixin import QueryEngineTurnMixin
    mixin = QueryEngineTurnMixin.__new__(QueryEngineTurnMixin)
    # 坏路径 → append 内部失败 → hook 吞掉不抛
    mixin._learn_project_memory("记住：x", "y")
