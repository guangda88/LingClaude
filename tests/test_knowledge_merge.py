"""F3-1 同名合并语义回归测试（2026-09-22）。

背景：旧 add_rule 按 INSERT OR REPLACE(id) 写入，而 turn_learner 的 id 含
turn_num/session_id，永不重复 → 同一条经验 N 万行（峰值 20392 行同名）。
新语义：同 id upsert（P0#2 聚合器依赖）；异 id 同名合并进首见行
（frequency 累加、conf/quality 取 max、pattern 刷新）。
"""

import sqlite3

import pytest

from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase
from lingclaude.self_optimizer.learner.models import FeedbackCategory, LearnedRule, Pattern


@pytest.fixture()
def kb(tmp_path):
    instance = KnowledgeBase(db_path=str(tmp_path / "kb_merge_test.db"))
    yield instance
    instance.close()


def _rule(rid: str, name: str, freq: int = 1, conf: float = 0.7) -> LearnedRule:
    return LearnedRule(
        id=rid,
        name=name,
        description=f"desc-{rid}",
        category=FeedbackCategory.BUG_RISK,
        pattern=Pattern(),
        tools=("t",),
        frequency=freq,
        confidence=conf,
        quality_score=conf,
        status="active",
    )


def _row_count(kb: KnowledgeBase) -> int:
    conn = sqlite3.connect(kb.db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0]
    finally:
        conn.close()


def test_same_name_different_id_merges_into_one_row(kb):
    """异 id 同名 → 合并不翻倍（20392 行重复病灶的回归防线）。"""
    assert kb.add_rule(_rule("a1", "同一经验", 1, 0.7)).is_ok
    assert kb.add_rule(_rule("b2", "同一经验", 3, 0.9)).is_ok
    assert kb.add_rule(_rule("c3", "同一经验", 2, 0.6)).is_ok
    assert _row_count(kb) == 1
    conn = sqlite3.connect(kb.db_path)
    try:
        freq, conf = conn.execute(
            "SELECT frequency, confidence FROM rules WHERE name='同一经验'"
        ).fetchone()
    finally:
        conn.close()
    assert freq == 1 + 3 + 2
    assert conf == pytest.approx(0.9)  # 取 max


def test_same_id_upsert_does_not_duplicate(kb):
    """同 id → upsert 覆盖（P0#2 聚合器幂等依赖的既有语义）。"""
    assert kb.add_rule(_rule("a1", "经验甲", 1, 0.7)).is_ok
    assert kb.add_rule(_rule("a1", "经验甲", 5, 0.8)).is_ok
    assert _row_count(kb) == 1
    conn = sqlite3.connect(kb.db_path)
    try:
        freq = conn.execute(
            "SELECT frequency FROM rules WHERE id='a1'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert freq == 5  # 整行替换


def test_different_names_stay_separate(kb):
    """异名规则互不合并。"""
    kb.add_rule(_rule("x1", "经验甲"))
    kb.add_rule(_rule("x2", "经验乙"))
    assert _row_count(kb) == 2
