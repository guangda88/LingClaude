"""Tests for P2-2 session projection (session_projection.py).

The persisted Session.messages is a flat alternating tuple of plain strings:
[user_prompt, assistant_output, user_prompt, assistant_output, ...].
No role prefixes exist on the strings; index parity carries the role.
"""

from lingclaude.core.session import Session
from lingclaude.core.session_projection import (
    TokenProjection,
    ToolProjection,
    RoundProjection,
    project_tokens,
    project_tools,
    project_rounds,
    project_session,
    aggregate_sessions,
)


def _session(messages=(), input_tokens=0, output_tokens=0):
    return Session(
        session_id="s1",
        messages=tuple(messages),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def test_project_tokens_basic():
    s = _session(("u1", "a1", "u2", "a2"), input_tokens=100, output_tokens=60)
    p = project_tokens(s)
    assert isinstance(p, TokenProjection)
    assert p.total_input_tokens == 100
    assert p.total_output_tokens == 60
    assert p.total_tokens == 160
    assert p.rounds == 4
    assert p.avg_input_per_round == 25.0
    assert p.avg_output_per_round == 15.0


def test_project_tokens_empty_session():
    p = project_tokens(_session(input_tokens=0, output_tokens=0))
    assert p.total_tokens == 0
    assert p.rounds == 1
    assert p.avg_input_per_round == 0.0


def test_project_rounds_uses_index_parity():
    # Even indices = user, odd indices = assistant, regardless of content.
    s = _session(("prompt-a", "reply-a", "prompt-b", "reply-b", "prompt-c"))
    p = project_rounds(s)
    assert isinstance(p, RoundProjection)
    assert p.total_messages == 5
    assert p.user_messages == 3
    assert p.assistant_messages == 2
    assert p.other_messages == 0
    assert p.turns == 3
    assert p.avg_messages_per_turn == round(5 / 3, 2)


def test_project_rounds_single_turn():
    p = project_rounds(_session(("hello", "hi")))
    assert p.user_messages == 1
    assert p.assistant_messages == 1
    assert p.turns == 1
    assert p.avg_messages_per_turn == 2.0


def test_project_rounds_empty_has_one_turn():
    p = project_rounds(_session())
    assert p.total_messages == 0
    assert p.turns == 1  # non-zero denominator


def test_project_tools_counts_and_success():
    s = _session((
        "please run bash with command ls",
        'tool_name="bash" file_path="/tmp/x" ran ok',
        "now run edit",
        'tool_name="edit" file_path="/tmp/x" failed: not found',
    ))
    p = project_tools(s)
    assert isinstance(p, ToolProjection)
    assert p.total_calls == 2
    assert p.unique_tools == 2
    assert p.overall_success_rate == 0.5
    by_name = {t.name: t for t in p.by_name}
    assert by_name["bash"].calls == 1
    assert by_name["bash"].successes == 1
    assert by_name["bash"].failures == 0
    assert by_name["edit"].calls == 1
    assert by_name["edit"].failures == 1


def test_project_tools_no_calls():
    p = project_tools(_session(("hello", "world")))
    assert p.total_calls == 0
    assert p.unique_tools == 0
    assert p.overall_success_rate == 0.0
    assert p.by_name == ()


def test_to_dict_shapes():
    s = _session(("u", "a"), input_tokens=5, output_tokens=3)
    t = project_tokens(s).to_dict()
    assert t["total_tokens"] == 8
    r = project_rounds(s).to_dict()
    assert r["user_messages"] == 1
    tp = project_tools(s).to_dict()
    assert "by_name" in tp


def test_project_session_aggregate_report():
    s = _session(("u", 'tool_name="bash" ran ok'), input_tokens=10, output_tokens=4)
    report = project_session(s)
    assert report["session_id"] == "s1"
    assert "tokens" in report and "tools" in report and "rounds" in report


def test_aggregate_sessions():
    s1 = _session(("u1", 'tool_name="bash" ok'), input_tokens=10, output_tokens=5)
    s2 = _session(("u2", 'tool_name="edit" ok', "u3", "a3"), input_tokens=20, output_tokens=15)
    agg = aggregate_sessions([s1, s2])
    assert agg["session_count"] == 2
    assert agg["total_input_tokens"] == 30
    assert agg["total_output_tokens"] == 20
    assert agg["total_tokens"] == 50
    assert agg["total_messages"] == 6
    assert agg["tool_call_counts"] == {"bash": 1, "edit": 1}
