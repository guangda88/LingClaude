"""第3批⑧⑨（2026-10-29）：EscalationProvider 成本矩阵/P_th 门 + worktree 收口状态机。

背景（诚实纪律）：
- 本地 decisions 台账 meta.outcome 是门控自写回声（"accepted"=过了门），
  不是验证后结果——不得用其反解成本矩阵参数。P_th 门默认关。
- worktree 只开不关 5 周实证 188MB 泄漏（gw-* 残骸），收口状态机：
  只有正向证据（有 diff）才允许保留，失败/零产物才允许清理。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from lingclaude.model import fast_lane


def _load_server_module():
    spec = importlib.util.spec_from_file_location(
        "gw_server_for_settle",
        Path("/home/ai/lingclaude/lingclaude/plugins/agents/"
             "proj_agent_gateway/server.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── ⑧ 成本矩阵数学（PLAN.md:644 不对称公式）─────────────────────────

def test_p_th_default_laya_value():
    """Laya 经验值矩阵 → P_th=0.625（(−0.5+3)/(1+3+0)=2.5/4）。"""
    m = fast_lane._DEFAULT_COST_MATRIX
    assert abs(m.p_threshold() - 0.625) < 1e-9


def test_p_th_degenerate_matrix_conservative():
    """真·退化矩阵（act 与 escalate 收益完全相同，denom=0）→ 1.0 永远升级（保守）。"""
    m = fast_lane.DecisionCostMatrix(act_correct=1.0, act_wrong=-1.0,
                                     escalate_cost_correct=1.0,
                                     escalate_cost_wrong=-1.0)
    assert m.p_threshold() == 1.0


def test_p_th_zero_when_escalate_equals_act_wrong():
    """升级损失==判错损失（esc≡act_wrong）→ P_th=0（永远 act 是公式正确解）。"""
    m = fast_lane.DecisionCostMatrix(act_correct=1.0, act_wrong=-1.0,
                                     escalate_cost_correct=-1.0,
                                     escalate_cost_wrong=-1.0)
    assert m.p_threshold() == 0.0


def test_p_th_clamped():
    """异常矩阵（act_wrong 高于 esc_wrong）→ 钳制在 [0,1]，不产生负阈值。"""
    m = fast_lane.DecisionCostMatrix(act_correct=1.0, act_wrong=0.9,
                                     escalate_cost_correct=-0.1,
                                     escalate_cost_wrong=-0.2)
    assert 0.0 <= m.p_threshold() <= 1.0


# ── ⑧ P_th 门（默认关 / 显式开 / max 语义）──────────────────────────

def test_p_th_gate_off_by_default():
    """策略未开启 → None（门不接线，零行为漂移）。"""
    assert fast_lane._p_th_threshold({}) is None
    assert fast_lane._p_th_threshold({"fast_lane_p_th_enabled": False}) is None


def test_p_th_gate_enabled():
    params = {"fast_lane_p_th_enabled": True}
    assert abs(fast_lane._p_th_threshold(params) - 0.625) < 1e-9


def test_p_th_gate_bad_matrix_fails_open():
    """矩阵配置坏 → 回退缺省矩阵（fail-open），不抛异常。"""
    params = {"fast_lane_p_th_enabled": True,
              "fast_lane_cost_matrix": {"act_wrong": "not-a-number"}}
    assert abs(fast_lane._p_th_threshold(params) - 0.625) < 1e-9


def test_gate_verdict_p_th_blocks(tmp_path, monkeypatch):
    """门开启时：0.4 < P_th 0.625 → fallback（旧行为会放行）。"""
    monkeypatch.setattr(fast_lane, "_b2_gate_params",
                        lambda: {"fast_lane_p_th_enabled": True})
    result = {"answers": {"domain": {"choice": "writing",
                                     "confidence": 0.4}}}
    assert fast_lane._gate_verdict(result) is None


def test_gate_verdict_p_th_disabled_keeps_legacy(tmp_path, monkeypatch):
    """门关闭时：0.4 ≥ min 0.1 → 放行（存量行为零漂移）。"""
    monkeypatch.setattr(fast_lane, "_b2_gate_params", lambda: {})
    result = {"answers": {"domain": {"choice": "writing",
                                     "confidence": 0.4}}}
    assert fast_lane._gate_verdict(result) is result


# ── ⑧ shadow 对照（落台账 / fail-open / verdict 透传）────────────────

def test_shadow_compare_records_alignment(monkeypatch):
    calls = []

    def fake_record(domain, action, verdict, reason="", p=None, meta=None):
        calls.append({"domain": domain, "action": action,
                      "verdict": verdict, "meta": meta or {}})

    monkeypatch.setattr(fast_lane, "record_decision", fake_record)
    v = {"answers": {"domain": {"choice": "code", "confidence": 0.8}}}
    out = fast_lane.shadow_compare(v, "chitchat")  # 关键词轨给 OTHER 类
    assert out is v
    # chitchat 不在粗对齐表 → divergent（供人工抽查），verdict=gray
    assert calls[0]["action"] == "shadow_align"
    assert calls[0]["meta"]["outcome"] == "divergent"


def test_shadow_compare_fail_open(monkeypatch):
    """台账故障 → 返回原 verdict，绝不上抛。"""
    def boom(*a, **k):
        raise RuntimeError("ledger down")
    monkeypatch.setattr(fast_lane, "record_decision", boom)
    v = {"answers": {"domain": {"choice": "code", "confidence": 0.8}}}
    assert fast_lane.shadow_compare(v, "code_generation") is v


# ── ⑨ 收口状态机（四裁定：failed清/ok无产物清/ok有产物留/未知留）─────

class _FakeTask:
    def __init__(self, branch="gw-x-abc", diff=""):
        self.task_id = "gw-x"
        self.branch = branch
        self.path = Path("/tmp/nowhere")
        self.merged = False
        self.discarded = False
        self._diff = diff


class _FakeSession:
    def __init__(self, tasks):
        self.tasks = tasks
        self.cleaned = []

    def diff(self, wt):
        return wt._diff

    def cleanup(self, wt, remove_branch=True):
        self.cleaned.append(wt.task_id)
        wt.discarded = True


def _run_settlement(server, tasks, healths):
    ws = _FakeSession(tasks)
    settle = server._make_settle_for_test(ws, dict(tasks))
    return settle(healths), ws


def test_settle_failed_agent_cleaned():
    """failed → cleanup（分支保留：remove_branch=False）。"""
    server = _load_server_module()
    t = _FakeTask()
    settled, ws = _run_settlement(server, {"a": t}, {"a": "failed"})
    assert settled[0]["action"] == "cleanup"
    assert t.task_id in ws.cleaned


def test_settle_ok_no_diff_cleaned():
    """ok 但零 diff（无产物）→ cleanup + note=no_diff。"""
    server = _load_server_module()
    t = _FakeTask(diff="")
    settled, ws = _run_settlement(server, {"a": t}, {"a": "ok"})
    assert settled[0]["action"] == "cleanup"
    assert settled[0]["note"] == "no_diff"
    assert t.task_id in ws.cleaned


def test_settle_ok_with_diff_kept_pending_merge():
    """ok 且有 diff → 保留待人工 merge（正向证据不许自动丢弃）。"""
    server = _load_server_module()
    t = _FakeTask(diff=" src/x.py | 3 +++")
    settled, ws = _run_settlement(server, {"a": t}, {"a": "ok"})
    assert settled[0]["action"] == "keep"
    assert "pending_merge" in settled[0]["note"]
    assert ws.cleaned == []


def test_settle_unknown_health_kept():
    """健康未知 → 保留（宁可漏清理不可误丢工作）。"""
    server = _load_server_module()
    t = _FakeTask(diff="")
    settled, ws = _run_settlement(server, {"a": t}, {})
    assert settled[0]["action"] == "keep"
    assert ws.cleaned == []


def test_settle_single_failure_no_cascade():
    """单棵 cleanup 异常 → 不连坐其余。"""
    server = _load_server_module()

    class _BoomSession(_FakeSession):
        def cleanup(self, wt, remove_branch=True):
            if wt.task_id == "gw-bad":
                raise RuntimeError("git locked")
            super().cleanup(wt, remove_branch=remove_branch)

    bad, good = _FakeTask(), _FakeTask(diff="")
    bad.task_id, good.task_id = "gw-bad", "gw-good"
    ws = _BoomSession({"bad": bad, "good": good})
    settle = server._make_settle_for_test(ws, {"bad": bad, "good": good})
    settled = settle({"bad": "failed", "good": "ok"})
    by_agent = {s["agent"]: s for s in settled}
    assert by_agent["bad"]["action"] == "keep"      # 清不掉→保留+报错注记
    assert "cleanup_error" in by_agent["bad"]["note"]
    assert by_agent["good"]["action"] == "cleanup"  # 不连坐
