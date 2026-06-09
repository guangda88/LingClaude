"""Tests for TopicStack — 议题栈."""
from __future__ import annotations

import json
from pathlib import Path


from lingclaude.core.topic_stack import Topic, TopicStack, TopicStatus


class TestTopic:
    def test_create_sets_opened_at(self):
        t = Topic(name="test", goal="pass")
        assert t.status == TopicStatus.OPEN
        assert t.opened_at != ""

    def test_create_with_explicit_opened_at(self):
        t = Topic(name="test", goal="pass", opened_at="2026-01-01T00:00:00")
        assert t.opened_at == "2026-01-01T00:00:00"


class TestTopicStackBasic:
    def test_push_and_current(self):
        stack = TopicStack()
        t = stack.push("audit", "fix SS-2/3/6/10")
        assert stack.current() is t
        assert t.status == TopicStatus.OPEN

    def test_close_by_name(self):
        stack = TopicStack()
        stack.push("audit", "fix SS-2/3/6/10")
        closed = stack.close("audit", summary="257/258 passed")
        assert closed is not None
        assert closed.status == TopicStatus.CLOSED
        assert closed.summary == "257/258 passed"
        assert closed.closed_at != ""
        assert stack.current() is None

    def test_close_current(self):
        stack = TopicStack()
        stack.push("audit", "fix bugs")
        closed = stack.close_current(summary="done")
        assert closed is not None
        assert closed.status == TopicStatus.CLOSED

    def test_close_nonexistent(self):
        stack = TopicStack()
        assert stack.close("ghost") is None

    def test_close_already_closed(self):
        stack = TopicStack()
        stack.push("audit", "fix bugs")
        stack.close("audit", summary="done")
        assert stack.close("audit", summary="again") is None

    def test_stats(self):
        stack = TopicStack()
        stack.push("a", "ga")
        stack.push("b", "gb")
        stack.close("a", summary="sa")
        assert stack.stats() == {"total": 2, "open": 1, "closed": 1}


class TestTopicStackMulti:
    def test_open_two_parallel(self):
        stack = TopicStack()
        stack.push("first", "g1")
        stack.push("second", "g2")
        assert len(stack.open_topics()) == 2

    def test_only_latest_is_current(self):
        stack = TopicStack()
        stack.push("first", "g1")
        stack.push("second", "g2")
        assert stack.current().name == "second"

    def test_close_oldest_first(self):
        stack = TopicStack()
        stack.push("first", "g1")
        stack.push("second", "g2")
        stack.close("first", summary="s1")
        stack.close("second", summary="s2")
        assert stack.stats() == {"total": 2, "open": 0, "closed": 2}


class TestTopicStackForceClose:
    def test_force_close_all(self):
        stack = TopicStack()
        stack.push("a", "ga")
        stack.push("b", "gb")
        count = stack.force_close_all(summary="interrupted")
        assert count == 2
        assert stack.stats() == {"total": 2, "open": 0, "closed": 2}

    def test_force_close_empty(self):
        stack = TopicStack()
        assert stack.force_close_all() == 0


class TestTopicStackPersistence:
    def test_persist_and_load(self, tmp_path: Path):
        path = tmp_path / "topics.json"
        stack = TopicStack(_persist_path=path)
        stack.push("audit", "fix bugs")
        stack.close("audit", summary="done")
        stack.push("research", "survey meta-X")

        loaded = TopicStack.load(path)
        assert loaded.stats() == {"total": 2, "open": 1, "closed": 1}
        assert loaded.current().name == "research"
        assert loaded.closed_topics()[0].summary == "done"

    def test_load_missing_file(self, tmp_path: Path):
        path = tmp_path / "nonexistent.json"
        stack = TopicStack.load(path)
        assert stack.stats() == {"total": 0, "open": 0, "closed": 0}

    def test_load_corrupt_file(self, tmp_path: Path):
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        stack = TopicStack.load(path)
        assert stack.stats() == {"total": 0, "open": 0, "closed": 0}

    def test_persist_without_path(self):
        stack = TopicStack()
        stack.push("test", "no crash")
        assert stack.stats()["open"] == 1


class TestTopicStackHandoff:
    def test_handoff_text_with_mixed(self):
        stack = TopicStack()
        stack.push("audit", "fix bugs")
        stack.close("audit", summary="257/258")
        stack.push("research", "meta-X survey")
        text = stack.to_handover_text()
        assert "audit" in text
        assert "257/258" in text
        assert "research" in text

    def test_handoff_empty(self):
        stack = TopicStack()
        assert stack.to_handover_text() == ""

    def test_compact_json_roundtrip(self):
        stack = TopicStack()
        stack.push("a", "ga")
        stack.close("a", summary="sa")
        raw = stack.to_compact_json()
        data = json.loads(raw)
        assert data[0]["name"] == "a"
        assert data[0]["status"] == "closed"


class TestTopicAge:
    def test_age_minutes_fresh(self):
        t = Topic(name="fresh", goal="test")
        assert t.age_minutes() < 1

    def test_age_minutes_old(self):
        t = Topic(name="old", goal="test", opened_at="2000-01-01T00:00:00+00:00")
        assert t.age_minutes() > 100000

    def test_check_stale_catches_old(self, caplog):
        stack = TopicStack(_stale_minutes=0.0)
        old = Topic(name="old", goal="test", opened_at="2000-01-01T00:00:00+00:00")
        stack._topics.append(old)
        with caplog.at_level("WARNING"):
            stale = stack.check_stale()
        assert len(stale) == 1
        assert stale[0].name == "old"

    def test_check_stale_skips_fresh(self):
        stack = TopicStack(_stale_minutes=9999)
        stack.push("fresh", "test")
        assert stack.check_stale() == []

    def test_check_stale_skips_closed(self):
        stack = TopicStack(_stale_minutes=0.0)
        old = Topic(name="old", goal="test", opened_at="2000-01-01T00:00:00+00:00")
        old.status = TopicStatus.CLOSED
        stack._topics.append(old)
        assert stack.check_stale() == []
