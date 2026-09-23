"""G1 落库格式修正回归（2026-09-23）。

背景：corrections 流此前落的是元数据壳——original_error=prompt 截断、
correction="user correction xN" 计数，真实纠正内容与被纠正输出双缺，
记录对避错零价值。本组测试锁定：纠正内容必须真实入库。
"""

import sqlite3
from types import SimpleNamespace

import pytest

from lingclaude.core.turn_learner import record_turn_learnings


@pytest.fixture()
def flywheel_db(tmp_path, monkeypatch):
    """把 turn_learner 运行时构造的三个仓储都指向临时库。"""
    import lingclaude.core.data_flywheel as df_mod
    import lingclaude.self_optimizer.experiments as exp_mod
    import lingclaude.self_optimizer.learner.knowledge as kb_mod
    from lingclaude.core.data_flywheel import DataFlywheel
    from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase

    fw_path = tmp_path / "data_flywheel.db"
    kb_path = tmp_path / "knowledge.db"

    monkeypatch.setattr(
        df_mod, "DataFlywheel", lambda: DataFlywheel(str(fw_path))
    )
    monkeypatch.setattr(
        kb_mod, "KnowledgeBase", lambda: KnowledgeBase(str(kb_path))
    )

    class _NoPending:
        def current_pending_id(self):
            return None

    monkeypatch.setattr(exp_mod, "ExperimentLedger", _NoPending)
    return fw_path


def _fetch_corrections(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT original_error, correction FROM corrections"
        ).fetchall()
    finally:
        conn.close()


def test_correction_log_contains_real_content(flywheel_db):
    """纠正落库必须含真实内容：original_error=被纠正输出，correction=用户原话。"""
    behavior = SimpleNamespace(
        hallucination_risk=0.0,
        tool_error_count=0,
        corrections_received=1,
    )
    messages = [
        "第一轮用户输入",           # p1
        "第一轮 assistant 输出（含幻觉断言）",  # a1 ← 被纠正对象
        "你错了，55 个测试实际是 57 个",       # p2 ← 纠正原话
        "已修正的回复",             # a2
    ]
    record_turn_learnings(
        prompt=messages[-2],
        behavior=behavior,
        messages=messages,
        session_id="sess-abcdef12",
        response=messages[-1],
    )

    rows = _fetch_corrections(flywheel_db)
    assert len(rows) == 1
    original, correction = rows[0]
    # original_error 存被纠正的 assistant 输出（messages[-3]）
    assert original.startswith("turn_2 output: ")
    assert "含幻觉断言" in original
    # correction 存用户纠正原话 + 元数据后缀
    assert correction.startswith("你错了")
    assert "[user correction x1, session sess-abc]" in correction


def test_correction_fallback_to_response(flywheel_db):
    """messages 不足 3 条时（首轮即被纠正），被纠正输出回退取 response 参数。"""
    behavior = SimpleNamespace(
        hallucination_risk=0.0,
        tool_error_count=0,
        corrections_received=1,
    )
    messages = ["问一句", "答一句（首轮输出）"]
    record_turn_learnings(
        prompt="问一句",
        behavior=behavior,
        messages=messages,
        session_id="sess-12345678",
        response="答一句（首轮输出）",
    )

    rows = _fetch_corrections(flywheel_db)
    assert len(rows) == 1
    original, _ = rows[0]
    assert "首轮输出" in original
