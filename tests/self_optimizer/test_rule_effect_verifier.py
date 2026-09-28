"""断点② 规则行为回放验证测试。

覆盖：
  - knowledge：rule_injection 表创建、record_injection、get_first_injection_time、
    update_rule_confidence
  - verifier：各判定分支（improved / no_effect / harmful / insufficient-无注入 /
    insufficient-样本不足 / insufficient-无基线）、升 active、deprecated 退役、
    fail-soft（飞轮库缺失不抛异常）
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase
from lingclaude.self_optimizer.learner.models import (
    FeedbackCategory,
    LearnedRule,
    Pattern,
)
from lingclaude.self_optimizer.rule_effect_verifier import (
    CONF_FLOOR,
    RuleEffectVerifier,
)


def _make_rule(rule_id: str, sig: str, tool: str = "bash",
               conf: float = 0.6, status: str = "draft") -> LearnedRule:
    return LearnedRule(
        id=rule_id,
        name=f"失败归因:{tool}",
        description=f"同型失败聚类：{sig}（测试）",
        category=FeedbackCategory.TOOL_ERROR,
        pattern=Pattern(context_keywords=(tool, "failure_cluster", sig)),
        tools=(tool,),
        frequency=5,
        confidence=conf,
        status=status,
    )


@pytest.fixture()
def kb(tmp_path: Path):
    k = KnowledgeBase(db_path=str(tmp_path / "knowledge.db"))
    yield k
    k.close()


def _mk_flywheel(tmp_path: Path, rows: list[tuple[str, str, str]]) -> Path:
    """造一个最小飞轮库。rows: (session_id, error_message, occurred_at)"""
    db = tmp_path / "data_flywheel.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE error_log (id INTEGER PRIMARY KEY, session_id TEXT,"
        " error_message TEXT, occurred_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO error_log (session_id, error_message, occurred_at) VALUES (?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db


# --------------------------------------------------------------------------- #
# knowledge 层
# --------------------------------------------------------------------------- #
class TestKnowledgeInjection:
    def test_injection_table_created(self, kb):
        conn = kb._get_connection()
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='rule_injection'"
        )
        assert cur.fetchone() is not None

    def test_record_and_first_injection(self, kb):
        kb.add_rule(_make_rule("failure_cluster_bash_abc123", "NoneType"))
        assert kb.record_injection(
            "failure_cluster_bash_abc123", session_id="s1", keyword="k", lane="hot"
        ).is_ok
        first = kb.get_first_injection_time("failure_cluster_bash_abc123")
        assert first.is_ok and first.data is not None

    def test_first_injection_none_when_never(self, kb):
        r = kb.get_first_injection_time("nonexistent_rule")
        assert r.is_ok and r.data is None

    def test_first_injection_is_min(self, kb):
        kb.add_rule(_make_rule("failure_cluster_bash_x", "sig"))
        conn = kb._get_connection()
        # 手动插两条不同时间，确认 MIN 取最早
        conn.execute(
            "INSERT INTO rule_injection (rule_id, session_id, keyword, lane, injected_at)"
            " VALUES ('failure_cluster_bash_x','s','','hot','2026-09-20T10:00:00')"
        )
        conn.execute(
            "INSERT INTO rule_injection (rule_id, session_id, keyword, lane, injected_at)"
            " VALUES ('failure_cluster_bash_x','s','','hot','2026-09-25T10:00:00')"
        )
        conn.commit()
        first = kb.get_first_injection_time("failure_cluster_bash_x")
        assert first.data == "2026-09-20T10:00:00"

    def test_update_rule_confidence(self, kb):
        kb.add_rule(_make_rule("failure_cluster_bash_c", "sig", conf=0.6))
        assert kb.update_rule_confidence("failure_cluster_bash_c", 0.75).is_ok
        res = kb.get_all_rules(limit=10)
        rule = [r for r in res.data if r.id == "failure_cluster_bash_c"][0]
        assert rule.confidence == pytest.approx(0.75)

    def test_update_rule_confidence_clamps(self, kb):
        kb.add_rule(_make_rule("failure_cluster_bash_d", "sig", conf=0.9))
        kb.update_rule_confidence("failure_cluster_bash_d", 1.5)
        res = kb.get_all_rules(limit=10)
        rule = [r for r in res.data if r.id == "failure_cluster_bash_d"][0]
        assert rule.confidence == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# verifier 判定分支
# --------------------------------------------------------------------------- #
def _inject_at(kb, rule_id: str, iso_time: str):
    conn = kb._get_connection()
    conn.execute(
        "INSERT INTO rule_injection (rule_id, session_id, keyword, lane, injected_at)"
        " VALUES (?,?,?,?,?)",
        (rule_id, "s", "", "hot", iso_time),
    )
    conn.commit()


def _dense_failures(sig: str, sessions: int, center: datetime, days_span: int,
                    fails_per_session: int):
    """在 center±days_span 内造 sessions 个会话，每个 fails_per_session 次同类失败。"""
    rows = []
    for i in range(sessions):
        t = (center - timedelta(days=days_span / 2) + timedelta(hours=i)).isoformat()
        for _ in range(fails_per_session):
            rows.append((f"sess{i}", f"Error: {sig} happened", t))
    return rows


class TestVerifierBranches:
    def test_no_injection_insufficient(self, tmp_path):
        kb_path = tmp_path / "k.db"
        kb = KnowledgeBase(db_path=str(kb_path))
        kb.add_rule(_make_rule("failure_cluster_bash_a", "sigA"))
        kb.close()
        fly = _mk_flywheel(tmp_path, [])
        v = RuleEffectVerifier(flywheel_db=fly, knowledge_db=kb_path).verify()
        assert result_verdict(v, "failure_cluster_bash_a") == "insufficient"

    def test_flywheel_missing_fails_soft(self, tmp_path):
        kb_path = tmp_path / "k.db"
        kb = KnowledgeBase(db_path=str(kb_path))
        kb.add_rule(_make_rule("failure_cluster_bash_b", "sigB"))
        _inject_at(kb, "failure_cluster_bash_b", datetime.now().isoformat())
        kb.close()
        # 飞轮库不存在 → 不应抛异常
        v = RuleEffectVerifier(
            flywheel_db=tmp_path / "nope.db", knowledge_db=kb_path
        ).verify()
        assert v.error == ""
        assert result_verdict(v, "failure_cluster_bash_b") == "insufficient"

    def test_improved_promotes_to_active(self, tmp_path):
        kb_path = tmp_path / "k.db"
        kb = KnowledgeBase(db_path=str(kb_path))
        kb.add_rule(_make_rule("failure_cluster_bash_imp", "timeout", conf=0.6))
        t0 = datetime.now()
        _inject_at(kb, "failure_cluster_bash_imp", t0.isoformat())
        kb.close()
        # 前窗口 8 会话×5 失败（密度5），后窗口 8 会话×1 失败（密度1）→ 改善80%
        rows = _dense_failures("timeout", 8, t0 - timedelta(days=3), 4, 5)
        rows += _dense_failures("timeout", 8, t0 + timedelta(days=3), 4, 1)
        fly = _mk_flywheel(tmp_path, rows)
        v = RuleEffectVerifier(flywheel_db=fly, knowledge_db=kb_path).verify()
        assert result_verdict(v, "failure_cluster_bash_imp") == "improved"
        assert v.promoted == 1
        # 确认升 active
        kb2 = KnowledgeBase(db_path=str(kb_path))
        rule = [r for r in kb2.get_all_rules(limit=10).data
                if r.id == "failure_cluster_bash_imp"][0]
        assert rule.status == "active"
        assert rule.confidence > 0.6
        kb2.close()

    def test_no_effect_lowers_confidence(self, tmp_path):
        kb_path = tmp_path / "k.db"
        kb = KnowledgeBase(db_path=str(kb_path))
        kb.add_rule(_make_rule("failure_cluster_bash_ne", "perm", conf=0.6))
        t0 = datetime.now()
        _inject_at(kb, "failure_cluster_bash_ne", t0.isoformat())
        kb.close()
        # 前后密度相同 → 无效
        rows = _dense_failures("perm", 6, t0 - timedelta(days=3), 4, 3)
        rows += _dense_failures("perm", 6, t0 + timedelta(days=3), 4, 3)
        fly = _mk_flywheel(tmp_path, rows)
        v = RuleEffectVerifier(flywheel_db=fly, knowledge_db=kb_path).verify()
        assert result_verdict(v, "failure_cluster_bash_ne") == "no_effect"
        kb2 = KnowledgeBase(db_path=str(kb_path))
        rule = [r for r in kb2.get_all_rules(limit=10).data
                if r.id == "failure_cluster_bash_ne"][0]
        assert rule.confidence < 0.6
        kb2.close()

    def test_harmful_double_downweight(self, tmp_path):
        kb_path = tmp_path / "k.db"
        kb = KnowledgeBase(db_path=str(kb_path))
        kb.add_rule(_make_rule("failure_cluster_bash_h", "crash", conf=0.6))
        t0 = datetime.now()
        _inject_at(kb, "failure_cluster_bash_h", t0.isoformat())
        kb.close()
        # 前 1/会话，后 5/会话 → 反升 400% → harmful
        rows = _dense_failures("crash", 6, t0 - timedelta(days=3), 4, 1)
        rows += _dense_failures("crash", 6, t0 + timedelta(days=3), 4, 5)
        fly = _mk_flywheel(tmp_path, rows)
        v = RuleEffectVerifier(flywheel_db=fly, knowledge_db=kb_path).verify()
        assert result_verdict(v, "failure_cluster_bash_h") == "harmful"

    def test_insufficient_sessions(self, tmp_path):
        kb_path = tmp_path / "k.db"
        kb = KnowledgeBase(db_path=str(kb_path))
        kb.add_rule(_make_rule("failure_cluster_bash_few", "sigF", conf=0.6))
        t0 = datetime.now()
        _inject_at(kb, "failure_cluster_bash_few", t0.isoformat())
        kb.close()
        # 后窗口只有 1 会话 < min_sessions(4)
        rows = _dense_failures("sigF", 8, t0 - timedelta(days=3), 4, 3)
        rows += _dense_failures("sigF", 1, t0 + timedelta(days=3), 4, 1)
        fly = _mk_flywheel(tmp_path, rows)
        v = RuleEffectVerifier(
            flywheel_db=fly, knowledge_db=kb_path, min_sessions=4
        ).verify()
        assert result_verdict(v, "failure_cluster_bash_few") == "insufficient"

    def test_no_baseline_insufficient(self, tmp_path):
        kb_path = tmp_path / "k.db"
        kb = KnowledgeBase(db_path=str(kb_path))
        kb.add_rule(_make_rule("failure_cluster_bash_nb", "sigN", conf=0.6))
        t0 = datetime.now()
        _inject_at(kb, "failure_cluster_bash_nb", t0.isoformat())
        kb.close()
        # 前窗口无此类失败（基线 0）→ insufficient
        rows = _dense_failures("sigN", 6, t0 + timedelta(days=3), 4, 2)
        fly = _mk_flywheel(tmp_path, rows)
        v = RuleEffectVerifier(flywheel_db=fly, knowledge_db=kb_path).verify()
        assert result_verdict(v, "failure_cluster_bash_nb") == "insufficient"

    def test_confidence_floor_deprecates(self, tmp_path):
        kb_path = tmp_path / "k.db"
        kb = KnowledgeBase(db_path=str(kb_path))
        # 起始置信度已在下限附近，无效一轮即触底退役
        kb.add_rule(_make_rule("failure_cluster_bash_dep", "sigD",
                               conf=CONF_FLOOR + 0.02))
        t0 = datetime.now()
        _inject_at(kb, "failure_cluster_bash_dep", t0.isoformat())
        kb.close()
        rows = _dense_failures("sigD", 6, t0 - timedelta(days=3), 4, 3)
        rows += _dense_failures("sigD", 6, t0 + timedelta(days=3), 4, 3)
        fly = _mk_flywheel(tmp_path, rows)
        v = RuleEffectVerifier(flywheel_db=fly, knowledge_db=kb_path).verify()
        assert v.deprecated == 1
        kb2 = KnowledgeBase(db_path=str(kb_path))
        rule = [r for r in kb2.get_all_rules(limit=10).data
                if r.id == "failure_cluster_bash_dep"][0]
        assert rule.status == "deprecated"
        kb2.close()

    def test_non_cluster_rules_skipped(self, tmp_path):
        """非 failure_cluster_* 规则不进入回放（无 error_signature 可归因）。"""
        kb_path = tmp_path / "k.db"
        kb = KnowledgeBase(db_path=str(kb_path))
        kb.add_rule(LearnedRule(
            id="generated_plugin_x", name="生成插片", description="d",
            category=FeedbackCategory.BEST_PRACTICE,
            pattern=Pattern(), tools=(), frequency=1, confidence=0.9, status="active",
        ))
        kb.close()
        fly = _mk_flywheel(tmp_path, [])
        v = RuleEffectVerifier(flywheel_db=fly, knowledge_db=kb_path).verify()
        assert v.examined == 0
        assert v.verdicts == []


def result_verdict(v, rule_id: str) -> str:
    for x in v.verdicts:
        if x.rule_id == rule_id:
            return x.verdict
    return "NOT_FOUND"
