"""LINGKERNEL_v1 D2 - deriveMessages 接口测试 (MV-1 L-a)。"""

from __future__ import annotations

from lingclaude.core.model_request_log import (
    ModelRequestEvent,
    ModelRequestLog,
    check_model_visible_invariant,
    derive_model_messages,
)


def _ev(seq, prompt, msgs):
    return ModelRequestEvent(seq=seq, prompt=prompt, messages_snapshot=msgs)


def test_empty_log_derives_empty():
    log = ModelRequestLog()
    assert derive_model_messages(log) == []


def test_append_returns_event_with_seq():
    log = ModelRequestLog()
    ev = log.append("p", [{"role": "user", "content": "p"}])
    assert ev.seq == 1
    ev2 = log.append("q", [{"role": "user", "content": "q"}])
    assert ev2.seq == 2
    assert log.last_seq() == 2


def test_derive_returns_last_snapshot():
    log = ModelRequestLog()
    log.append("p", [{"role": "user", "content": "p"}])
    log.append("q", [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}])
    derived = derive_model_messages(log)
    assert derived == [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
    ]


def test_derive_with_upto_seq():
    """截至 seq=1 时, 重建的是 seq=1 的快照 (log.prefix 语义)。"""
    log = ModelRequestLog()
    log.append("p", [{"role": "user", "content": "p"}])
    log.append("q", [{"role": "user", "content": "q"}])
    assert derive_model_messages(log, 1) == [{"role": "user", "content": "p"}]
    assert derive_model_messages(log, 2) == [{"role": "user", "content": "q"}]


def test_prefix_filters():
    log = ModelRequestLog()
    log.append("a", [])
    log.append("b", [])
    log.append("c", [])
    assert [e.prompt for e in log.prefix(2)] == ["a", "b"]
    assert [e.prompt for e in log.prefix(1)] == ["a"]
    assert [e.prompt for e in log.prefix(99)] == ["a", "b", "c"]


def test_snapshot_is_copied_not_aliased():
    """append 后外部修改原 list 不影响 log 内快照 (不可变性)。"""
    msgs = [{"role": "user", "content": "x"}]
    log = ModelRequestLog()
    log.append("p", msgs)
    msgs[0]["content"] = "tampered"
    assert derive_model_messages(log)[0]["content"] == "x"


def test_invariant_pass():
    log = ModelRequestLog()
    actual = [{"role": "user", "content": "p"}]
    log.append("p", actual)
    ok, reason = check_model_visible_invariant(log, 1, actual)
    assert ok, reason


def test_invariant_fail_on_mismatch():
    log = ModelRequestLog()
    log.append("p", [{"role": "user", "content": "p"}])
    # 模型实际收到的不一样 -> 违反
    ok, reason = check_model_visible_invariant(log, 1, [{"role": "user", "content": "OTHER"}])
    assert not ok
    assert "MV-1 violated" in reason


def test_invariant_fail_on_missing_log():
    """发了模型但 log 里没有 -> 违反 (可见即记录的反向)。"""
    log = ModelRequestLog()
    ok, reason = check_model_visible_invariant(log, 1, [{"role": "user", "content": "x"}])
    assert not ok


def test_events_property_immutable_view():
    log = ModelRequestLog()
    log.append("p", [])
    events = log.events
    assert isinstance(events, tuple)


def test_event_fields_roundtrip():
    log = ModelRequestLog()
    log.append("p", [{"role": "user"}], tool_names=("read", "grep"), timestamp="T1")
    ev = log.events[0]
    assert ev.tool_names == ("read", "grep")
    assert ev.timestamp == "T1"
    assert ev.prompt == "p"