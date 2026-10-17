"""RSI 阶段二（P2）专属测试：件9 Strategy 入 Target / 件10 跨组件诊断 / 件11 经验三属性。

对照 docs/research/rsi_round1_20261016/FINAL_synthesis.md 阶段二规格（:73-82）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import lingclaude.self_optimizer.daemon as daemon_mod
from lingclaude.self_optimizer.daemon import (
    P2_META_MIN_SAMPLES,
    P2_STRATEGY_WHITELIST,
    DaemonState,
    OptimizationDaemon,
)
from lingclaude.self_optimizer.experiments import ExperimentLedger
from lingclaude.self_optimizer.failure_cluster_analyzer import (
    COMPONENTS,
    FailureClusterAnalyzer,
    _classify_component,
    _intervention,
)
from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase
from lingclaude.self_optimizer.learner.models import (
    FeedbackCategory,
    LearnedRule,
    Pattern,
)
from lingclaude.self_optimizer.rule_effect_verifier import RuleEffectVerifier


# ── 件9a: 策略选择与白名单 ──────────────────────────────────────────────

def _bare_daemon(state: DaemonState) -> OptimizationDaemon:
    d = OptimizationDaemon.__new__(OptimizationDaemon)
    d.state = state
    return d


def test_pick_strategy_whitelist_filters_hacked_keys():
    """状态文件被手改注入非法策略名 → 白名单过滤，绝不返回白名单外策略。"""
    st = DaemonState()
    st.strategy_weights = {"tpe": 5.0, "evil_strategy": 99.0, "grid": 0.0}
    d = _bare_daemon(st)
    picks = {d._pick_strategy() for _ in range(60)}
    assert picks <= set(P2_STRATEGY_WHITELIST)
    assert "evil_strategy" not in picks
    assert "tpe" in picks  # 高权重策略被采到


def test_pick_strategy_empty_falls_back_uniform():
    st = DaemonState()
    d = _bare_daemon(st)
    picks = {d._pick_strategy() for _ in range(80)}
    assert picks <= set(P2_STRATEGY_WHITELIST)


def test_strategy_weights_persist_roundtrip(tmp_path: Path):
    """DaemonState.load 读回 strategy_weights（asdict 落盘 + 显式 load 字段）。"""
    p = tmp_path / "state.json"
    st = DaemonState()
    st.strategy_weights = {"tpe": 0.6, "random": 0.1}
    st.save(p)
    st2 = DaemonState.load(p)
    assert st2.strategy_weights == {"tpe": 0.6, "random": 0.1}
    # 旧状态文件无字段 → None 哨兵（兼容）
    p2 = tmp_path / "old.json"
    p2.write_text(json.dumps({"total_cycles": 3}), encoding="utf-8")
    assert DaemonState.load(p2).strategy_weights is None


# ── 件9b: meta-optimizer 权重表 ─────────────────────────────────────────

def _ledger_with_stats(tmp_path: Path) -> ExperimentLedger:
    led = ExperimentLedger(db_path=tmp_path / "exp.db")
    for _ in range(4):
        e = led.start(1, {"p": 1}, 10.0, strategy="tpe")
        led.settle(e, "accepted", 9.0)
    for _ in range(4):
        e = led.start(1, {"p": 1}, 10.0, strategy="random")
        led.settle(e, "rejected", 11.0)
    return led


def test_meta_adjust_updates_weights_by_acceptance(tmp_path: Path):
    d = OptimizationDaemon.__new__(OptimizationDaemon)
    d.experiments = _ledger_with_stats(tmp_path)
    st = DaemonState()
    st.total_cycles = 10  # 命中调权周期
    d.state = st
    d._maybe_adjust_strategy_weights()
    w = st.strategy_weights
    assert w is not None
    assert w["tpe"] == 0.6    # 75%*1.5 → 封顶
    assert w["random"] == 0.1  # 0% → 保底
    assert w["grid"] == 0.2    # 证据不足（<MIN_SAMPLES）维持先验


def test_meta_adjust_skips_when_not_due(tmp_path: Path):
    d = OptimizationDaemon.__new__(OptimizationDaemon)
    d.experiments = _ledger_with_stats(tmp_path)
    st = DaemonState()
    st.total_cycles = 7  # 非调权周期
    d.state = st
    d._maybe_adjust_strategy_weights()
    assert st.strategy_weights is None


def test_meta_adjust_respects_min_samples(tmp_path: Path):
    """只有 1 条已决单的策略 → 证据不足不动权重。"""
    led = ExperimentLedger(db_path=tmp_path / "exp2.db")
    e = led.start(1, {"p": 1}, 10.0, strategy="bayesian")
    led.settle(e, "accepted", 9.0)
    d = OptimizationDaemon.__new__(OptimizationDaemon)
    d.experiments = led
    st = DaemonState()
    st.total_cycles = 10
    d.state = st
    d._maybe_adjust_strategy_weights()
    assert st.strategy_weights is None  # 全部策略都证据不足 → 不更新


# ── 件10: 跨组件诊断 ────────────────────────────────────────────────────

def test_classify_component_dimensions():
    assert _classify_component("proxy3", "x") == "model"
    assert _classify_component("retriever", "x") == "context"
    assert _classify_component("bash", "connection refused") == "model"
    assert _classify_component("bash", "blocked by permissions") == "harness"
    assert _classify_component("read", "no such file: /x") == "data"
    assert _classify_component("webfetch", "some weird crash") == "tool_api"


def test_intervention_triple_complete():
    for comp in COMPONENTS:
        iv = _intervention(comp, "sig")
        assert set(iv) == {"target", "expected_metric", "disprove_if"}
        assert iv["target"] and iv["expected_metric"] and iv["disprove_if"]


def test_cluster_carries_component_and_intervention(tmp_path: Path):
    """聚类产物带组件标签 + 干预三元组；backlog 条目落四字段。"""
    from datetime import timedelta as td
    base = datetime.now() - td(hours=1)
    db = tmp_path / "flywheel.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """CREATE TABLE error_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, pattern_type TEXT,
            file_path TEXT, error_message TEXT, tool_name TEXT,
            context TEXT, session_id TEXT DEFAULT '', occurred_at TEXT)"""
    )
    for i, sess in enumerate(("s1", "s2", "s3")):
        conn.execute(
            "INSERT INTO error_log (error_message, tool_name, session_id, occurred_at)"
            " VALUES (?, ?, ?, ?)",
            (
                "Tool 'bash' timed out after 120s",
                "bash",
                sess,
                (base + td(minutes=i)).isoformat(timespec="seconds"),
            ),
        )
    conn.commit()
    conn.close()
    backlog = tmp_path / "backlog.jsonl"
    a = FailureClusterAnalyzer(db, backlog, min_occurrences=3, min_sessions=2)
    r = a.analyze(write=True)
    assert r.actionable == 1
    c = r.clusters[0]
    assert c.component in COMPONENTS
    assert set(c.intervention) == {"target", "expected_metric", "disprove_if"}
    rec = json.loads(backlog.read_text(encoding="utf-8").splitlines()[0])
    assert rec["component"] == c.component
    assert rec["disprove_if"]  # 反证条件落队（受控对比纪律）


# ── 件11: 经验三属性 ────────────────────────────────────────────────────

def _rule(rid: str, status: str = "draft", attrs: dict | None = None) -> LearnedRule:
    return LearnedRule(
        id=rid,
        name=f"r-{rid}",
        description="d",
        category=FeedbackCategory.TOOL_ERROR,
        pattern=Pattern(context_keywords=("t", "failure_cluster", "sig")),
        tools=("t",),
        frequency=3,
        confidence=0.6,
        status=status,
        attrs=attrs or {},
    )


def test_attrs_roundtrip_via_metadata_json(tmp_path: Path):
    kb = KnowledgeBase(db_path=str(tmp_path / "kb.db"))
    kb.add_rule(_rule("failure_cluster_a_1", attrs={"legality": "auto", "difficulty": "low"}))
    got = kb.get_rule("failure_cluster_a_1")
    assert got.is_ok
    assert got.data.attrs["legality"] == "auto"
    # verifier 回写 gain → 再 upsert 同规则，gain 不被冲掉（合并语义）
    kb.update_rule_attrs("failure_cluster_a_1", {"persistent_gain": 0.42})
    kb.add_rule(_rule("failure_cluster_a_1", attrs={"legality": "auto"}))
    got2 = kb.get_rule("failure_cluster_a_1").data
    assert got2.attrs["persistent_gain"] == 0.42
    kb.close()


def test_promotion_gate_blocks_incomplete_attrs(tmp_path: Path):
    """三属性不全 → 即便回放改善也持 draft（铁律 6 强化版）。"""
    kb = KnowledgeBase(db_path=str(tmp_path / "kb.db"))
    kb.add_rule(_rule("failure_cluster_b_1"))  # 无 attrs
    v = RuleEffectVerifier()
    incomplete = v._verify_one(
        None, kb, "failure_cluster_b_1", "sig", "t", "draft", 0.6, attrs={}
    )
    assert incomplete.attrs_incomplete
    # 完整属性 → 不置闸标记
    complete = v._verify_one(
        None, kb, "failure_cluster_b_1", "sig", "t", "draft", 0.6,
        attrs={"legality": "auto", "difficulty": "low", "persistent_gain": 0.5},
    )
    assert not complete.attrs_incomplete
    kb.close()


def test_demote_stale_active_90d(tmp_path: Path):
    kb = KnowledgeBase(db_path=str(tmp_path / "kb.db"))
    old = (datetime.now() - timedelta(days=95)).isoformat(timespec="seconds")
    # 1) 老 + 无收益 → 降
    kb.add_rule(_rule("failure_cluster_c_1", status="active"))
    kb.update_rule_attrs("failure_cluster_c_1", {"persistent_gain": -0.1, "last_verified_at": old})
    # 2) 老 + 有收益 → 不降
    kb.add_rule(_rule("failure_cluster_c_2", status="active"))
    kb.update_rule_attrs("failure_cluster_c_2", {"persistent_gain": 0.5, "last_verified_at": old})
    # 3) 新 + 无收益 → 不降（还没到期）
    kb.add_rule(_rule("failure_cluster_c_3", status="active"))
    kb.update_rule_attrs("failure_cluster_c_3", {"persistent_gain": 0.0, "last_verified_at": datetime.now().isoformat()})
    v = RuleEffectVerifier()
    n = v._demote_stale_active(kb)
    assert n == 1
    assert kb.get_rule("failure_cluster_c_1").data.status == "draft"
    assert kb.get_rule("failure_cluster_c_2").data.status == "active"
    assert kb.get_rule("failure_cluster_c_3").data.status == "active"
    kb.close()
