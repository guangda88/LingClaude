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
    """T9: 拦截路径下 DataFlywheel 不会建 corrections 表——无表即 0 条。"""
    conn = sqlite3.connect(db_path)
    try:
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "corrections" not in tables:
            return []
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
        prompt="不对，答错了",  # T9: 门槛要求本轮输入本身是纠正语义
        behavior=behavior,
        messages=messages,
        session_id="sess-12345678",
        response="答一句（首轮输出）",
    )

    rows = _fetch_corrections(flywheel_db)
    assert len(rows) == 1
    original, _ = rows[0]
    assert "首轮输出" in original


# ---------- T9 语义门槛（2026-10-10）：corrections 流反灌水 ----------

def _bm(corr: int):
    return SimpleNamespace(
        hallucination_risk=0.0, tool_error_count=0, corrections_received=corr,
    )


def test_t9_greeting_after_correction_not_logged(flywheel_db):
    """核心缺陷场景：会话里纠正过（累计=3），本轮只说「你好」→ 不得落库。

    旧逻辑累计门槛 >0 导致此后每轮都落垃圾 correction（实测「你好」带
    x3 计数入库、recurrence_count 一路涨）。门槛必须与会话累计解耦。
    """
    record_turn_learnings(
        prompt="你好", behavior=_bm(3), messages=["p1", "a1", "p2", "a2"],
        session_id="sess-t9greet1", response="答",
    )
    assert len(_fetch_corrections(flywheel_db)) == 0


def test_t9_no_correction_intent_not_logged(flywheel_db):
    """累计=0 且本轮非纠正语义（普通提问）→ 不落库（门槛解耦的双向确认）。"""
    record_turn_learnings(
        prompt="帮我看看这个文件", behavior=_bm(0), messages=["p1", "a1"],
        session_id="sess-t9ask01", response="答",
    )
    assert len(_fetch_corrections(flywheel_db)) == 0


def test_t9_greeting_umlaut_variants_blocked(flywheel_db):
    """防线②：整串寒暄各变体（谢谢/嗯/ok/在吗）即使误判也不落库。"""
    for i, greeting in enumerate(["谢谢", "嗯嗯", "ok", "在吗"]):
        record_turn_learnings(
            prompt=greeting, behavior=_bm(2), messages=["p1", "a1"],
            session_id=f"sess-t9var{i:02d}", response="答",
        )
    assert len(_fetch_corrections(flywheel_db)) == 0


def test_t9_real_correction_still_logged(flywheel_db):
    """正向守卫：真实纠正语义（累计=2，本轮也是纠正）→ 仍然正常落库。"""
    record_turn_learnings(
        prompt="你又没读代码就瞎说，文件根本不存在", behavior=_bm(2),
        messages=["p1", "a1", "p2", "a2"], session_id="sess-t9real1",
        response="答",
    )
    rows = _fetch_corrections(flywheel_db)
    assert len(rows) == 1
    assert "没读代码" in rows[0][1]
