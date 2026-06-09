"""Tests for intel system: IntelItem, IntelCollector, DailyDigest, IntelRelay."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingclaude.core.intel import (
    DailyDigestGenerator,
    IntelCategory,
    IntelCollector,
    IntelItem,
    IntelPriority,
    IntelRelay,
)
from lingclaude.core.query_engine import QueryEngine, QueryEngineConfig


class TestIntelItem:
    def test_create_sets_timestamp(self) -> None:
        item = IntelItem.create(
            category=IntelCategory.BEHAVIOR,
            priority=IntelPriority.INFO,
            source="test",
            content="test content",
        )
        assert item.timestamp
        assert item.category == IntelCategory.BEHAVIOR
        assert item.priority == IntelPriority.INFO
        assert item.source == "test"

    def test_frozen(self) -> None:
        item = IntelItem.create(
            category=IntelCategory.ERROR,
            priority=IntelPriority.WARNING,
            source="test",
            content="err",
        )
        with pytest.raises(AttributeError):
            item.content = "changed"  # type: ignore[misc]

    def test_to_dict(self) -> None:
        item = IntelItem.create(
            category=IntelCategory.SECURITY,
            priority=IntelPriority.CRITICAL,
            source="scanner",
            content="hardcoded secret found",
            metadata=(("file", "config.py"), ("line", "42")),
        )
        d = item.to_dict()
        assert d["category"] == "security"
        assert d["priority"] == "critical"
        assert d["metadata"]["file"] == "config.py"
        assert isinstance(d["metadata"], dict)

    def test_metadata_defaults_empty(self) -> None:
        item = IntelItem.create(
            category=IntelCategory.BEHAVIOR,
            priority=IntelPriority.INFO,
            source="test",
            content="no metadata",
        )
        assert item.metadata == ()


class TestIntelCollectorFromBehavior:
    def test_high_hallucination_risk(self) -> None:
        collector = IntelCollector()
        items = collector.from_behavior({
            "hallucination_risk": 0.5,
            "total_turns": 10,
            "frustration_rate": 0.0,
            "tool_error_rate": 0.0,
            "tool_use_rate": 0.3,
            "corrections_received": 0,
        })
        assert len(items) == 1
        assert items[0].category == IntelCategory.BEHAVIOR
        assert "幻觉" in items[0].content

    def test_high_frustration_rate(self) -> None:
        collector = IntelCollector()
        items = collector.from_behavior({
            "hallucination_risk": 0.1,
            "total_turns": 10,
            "frustration_rate": 0.3,
            "frustration_count": 3,
            "tool_error_rate": 0.0,
            "tool_use_rate": 0.5,
            "corrections_received": 0,
        })
        assert len(items) == 1
        assert "挫败" in items[0].content

    def test_high_tool_error_rate(self) -> None:
        collector = IntelCollector()
        items = collector.from_behavior({
            "hallucination_risk": 0.1,
            "total_turns": 10,
            "frustration_rate": 0.0,
            "tool_error_rate": 0.4,
            "tool_error_count": 4,
            "tool_use_rate": 0.5,
            "corrections_received": 0,
        })
        assert len(items) == 1
        assert items[0].category == IntelCategory.ERROR

    def test_low_tool_use_rate(self) -> None:
        collector = IntelCollector()
        items = collector.from_behavior({
            "hallucination_risk": 0.1,
            "total_turns": 5,
            "frustration_rate": 0.0,
            "tool_error_rate": 0.0,
            "tool_use_rate": 0.1,
            "corrections_received": 0,
        })
        assert len(items) == 1
        assert "工具使用率偏低" in items[0].content

    def test_multiple_corrections(self) -> None:
        collector = IntelCollector()
        items = collector.from_behavior({
            "hallucination_risk": 0.1,
            "total_turns": 10,
            "frustration_rate": 0.0,
            "tool_error_rate": 0.0,
            "tool_use_rate": 0.5,
            "corrections_received": 3,
        })
        assert len(items) == 1
        assert items[0].priority == IntelPriority.CRITICAL

    def test_all_normal_no_items(self) -> None:
        collector = IntelCollector()
        items = collector.from_behavior({
            "hallucination_risk": 0.1,
            "total_turns": 10,
            "frustration_rate": 0.05,
            "tool_error_rate": 0.05,
            "tool_use_rate": 0.5,
            "corrections_received": 0,
        })
        assert len(items) == 0

    def test_accumulates_items(self) -> None:
        collector = IntelCollector()
        collector.from_behavior({
            "hallucination_risk": 0.5,
            "total_turns": 10,
            "frustration_rate": 0.3,
            "frustration_count": 3,
            "tool_error_rate": 0.4,
            "tool_error_count": 4,
            "tool_use_rate": 0.5,
            "corrections_received": 3,
        })
        assert len(collector.items) == 4


class TestIntelCollectorFromSources:
    def test_from_file_change(self) -> None:
        collector = IntelCollector()
        item = collector.from_file_change("src/main.py", "modified", lines_added=10, lines_removed=3)
        assert item.category == IntelCategory.FILE_CHANGE
        assert "modified" in item.content
        assert item.priority == IntelPriority.INFO

    def test_from_file_change_deleted_is_warning(self) -> None:
        collector = IntelCollector()
        item = collector.from_file_change("src/old.py", "deleted")
        assert item.priority == IntelPriority.WARNING

    def test_from_pattern_security(self) -> None:
        collector = IntelCollector()
        item = collector.from_pattern(
            "HardcodedSecret", "config.py", "API key found in source",
            severity="critical",
        )
        assert item.category == IntelCategory.SECURITY
        assert item.priority == IntelPriority.WARNING

    def test_from_pattern_code(self) -> None:
        collector = IntelCollector()
        item = collector.from_pattern(
            "LongMethod", "service.py", "method exceeds 50 lines",
        )
        assert item.category == IntelCategory.CODE_PATTERN
        assert item.priority == IntelPriority.INFO

    def test_from_error(self) -> None:
        collector = IntelCollector()
        item = collector.from_error(
            "FileNotFoundError", "config.yaml not found",
            tool_name="read",
        )
        assert item.category == IntelCategory.ERROR
        assert item.priority == IntelPriority.WARNING

    def test_from_optimization(self) -> None:
        collector = IntelCollector()
        item = collector.from_optimization(
            "violations reduced from 5 to 1",
            improvement_score=0.8,
            violations_before=5,
            violations_after=1,
        )
        assert item.category == IntelCategory.OPTIMIZATION
        assert item.priority == IntelPriority.INFO

    def test_from_structure(self) -> None:
        collector = IntelCollector()
        item = collector.from_structure(
            total_files=25, total_packages=4, violations=2, max_complexity=8,
        )
        assert item.category == IntelCategory.STRUCTURE
        assert "25 文件" in item.content

    def test_from_quality_low(self) -> None:
        collector = IntelCollector()
        item = collector.from_quality(35.0, details="high complexity")
        assert item.priority == IntelPriority.CRITICAL

    def test_from_quality_good(self) -> None:
        collector = IntelCollector()
        item = collector.from_quality(85.0)
        assert item.priority == IntelPriority.INFO

    def test_collect_all_returns_tuple(self) -> None:
        collector = IntelCollector()
        collector.from_error("err", "msg")
        collector.from_file_change("f.py", "created")
        result = collector.collect_all()
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_clear(self) -> None:
        collector = IntelCollector()
        collector.from_error("err", "msg")
        collector.clear()
        assert len(collector.items) == 0


class TestDailyDigest:
    def test_generate_empty(self) -> None:
        digest = DailyDigestGenerator.generate((), "2025-01-01")
        assert digest.report_date == "2025-01-01"
        assert len(digest.items) == 0
        assert "无情报" in digest.summary

    def test_generate_with_items(self) -> None:
        items = (
            IntelItem.create(
                category=IntelCategory.BEHAVIOR,
                priority=IntelPriority.WARNING,
                source="test",
                content="hallucination risk high",
            ),
            IntelItem.create(
                category=IntelCategory.ERROR,
                priority=IntelPriority.CRITICAL,
                source="test",
                content="tool crash",
            ),
        )
        digest = DailyDigestGenerator.generate(items)
        assert len(digest.items) == 2
        assert len(digest.key_findings) > 0
        assert len(digest.recommendations) > 0

    def test_category_counts(self) -> None:
        items = (
            IntelItem.create(
                category=IntelCategory.BEHAVIOR,
                priority=IntelPriority.INFO,
                source="t", content="a",
            ),
            IntelItem.create(
                category=IntelCategory.BEHAVIOR,
                priority=IntelPriority.INFO,
                source="t", content="b",
            ),
            IntelItem.create(
                category=IntelCategory.ERROR,
                priority=IntelPriority.WARNING,
                source="t", content="c",
            ),
        )
        digest = DailyDigestGenerator.generate(items)
        cat_dict = dict(digest.category_counts)
        assert cat_dict["behavior"] == 2
        assert cat_dict["error"] == 1

    def test_to_dict(self) -> None:
        items = (
            IntelItem.create(
                category=IntelCategory.QUALITY,
                priority=IntelPriority.INFO,
                source="test",
                content="quality ok",
            ),
        )
        digest = DailyDigestGenerator.generate(items, "2025-06-01")
        d = digest.to_dict()
        assert d["report_date"] == "2025-06-01"
        assert d["total_items"] == 1
        assert isinstance(d["items"], list)

    def test_to_markdown(self) -> None:
        items = (
            IntelItem.create(
                category=IntelCategory.ERROR,
                priority=IntelPriority.WARNING,
                source="test",
                content="something broke",
                metadata=(("tool", "grep"),),
            ),
        )
        digest = DailyDigestGenerator.generate(items)
        md = digest.to_markdown()
        assert "# 灵克情报日报" in md
        assert "something broke" in md
        assert "grep" in md

    def test_security_recommendation(self) -> None:
        items = (
            IntelItem.create(
                category=IntelCategory.SECURITY,
                priority=IntelPriority.CRITICAL,
                source="scanner",
                content="secret found",
            ),
        )
        digest = DailyDigestGenerator.generate(items)
        assert any("安全" in r for r in digest.recommendations)


class TestIntelRelay:
    def test_relay_writes_files(self, tmp_path: Path) -> None:
        relay = IntelRelay(output_dir=tmp_path / "intel")
        items = (
            IntelItem.create(
                category=IntelCategory.BEHAVIOR,
                priority=IntelPriority.INFO,
                source="test",
                content="test intel",
            ),
        )
        digest = DailyDigestGenerator.generate(items, "2025-01-15")
        result = relay.relay(digest)
        assert result.is_ok

        json_file = tmp_path / "intel" / "digest_2025-01-15.json"
        md_file = tmp_path / "intel" / "digest_2025-01-15.md"
        manifest_file = tmp_path / "intel" / "manifest.json"

        assert json_file.exists()
        assert md_file.exists()
        assert manifest_file.exists()

        data = json.loads(json_file.read_text(encoding="utf-8"))
        assert data["total_items"] == 1

        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        assert len(manifest["entries"]) == 1
        assert manifest["entries"][0]["date"] == "2025-01-15"

    def test_load_digest(self, tmp_path: Path) -> None:
        relay = IntelRelay(output_dir=tmp_path / "intel")
        items = (
            IntelItem.create(
                category=IntelCategory.ERROR,
                priority=IntelPriority.WARNING,
                source="test",
                content="load test",
            ),
        )
        digest = DailyDigestGenerator.generate(items, "2025-03-20")
        relay.relay(digest)

        loaded = relay.load_digest("2025-03-20")
        assert loaded.is_ok
        assert loaded.data is not None
        assert len(loaded.data.items) == 1
        assert loaded.data.items[0].content == "load test"

    def test_load_nonexistent_digest(self, tmp_path: Path) -> None:
        relay = IntelRelay(output_dir=tmp_path / "intel")
        result = relay.load_digest("2099-12-31")
        assert result.is_error
        assert result.code == "DIGEST_NOT_FOUND"

    def test_list_digests(self, tmp_path: Path) -> None:
        relay = IntelRelay(output_dir=tmp_path / "intel")
        for d in ["2025-01-01", "2025-01-02", "2025-01-03"]:
            digest = DailyDigestGenerator.generate((), d)
            relay.relay(digest)

        result = relay.list_digests()
        assert result.is_ok
        assert result.data is not None
        assert len(result.data) == 3
        assert result.data[0] == "2025-01-01"

    def test_list_digests_empty_dir(self, tmp_path: Path) -> None:
        relay = IntelRelay(output_dir=tmp_path / "nonexistent")
        result = relay.list_digests()
        assert result.is_ok
        assert result.data == ()

    def test_manifest_update_replaces_existing_date(self, tmp_path: Path) -> None:
        relay = IntelRelay(output_dir=tmp_path / "intel")

        items = (
            IntelItem.create(
                category=IntelCategory.BEHAVIOR,
                priority=IntelPriority.INFO,
                source="test",
                content="v1",
            ),
        )
        digest1 = DailyDigestGenerator.generate(items, "2025-05-01")
        relay.relay(digest1)

        items2 = (
            IntelItem.create(
                category=IntelCategory.BEHAVIOR,
                priority=IntelPriority.INFO,
                source="test",
                content="v2",
            ),
            IntelItem.create(
                category=IntelCategory.ERROR,
                priority=IntelPriority.WARNING,
                source="test",
                content="err",
            ),
        )
        digest2 = DailyDigestGenerator.generate(items2, "2025-05-01")
        relay.relay(digest2)

        manifest = json.loads(
            (tmp_path / "intel" / "manifest.json").read_text(encoding="utf-8")
        )
        entries = [e for e in manifest["entries"] if e["date"] == "2025-05-01"]
        assert len(entries) == 1
        assert entries[0]["total_items"] == 2


class TestEndToEndIntelPipeline:
    def test_full_pipeline(self, tmp_path: Path) -> None:
        collector = IntelCollector()

        collector.from_behavior({
            "hallucination_risk": 0.5,
            "total_turns": 20,
            "frustration_rate": 0.25,
            "frustration_count": 5,
            "tool_error_rate": 0.1,
            "tool_use_rate": 0.6,
            "corrections_received": 3,
        })
        collector.from_file_change("src/core.py", "modified", lines_added=50)
        collector.from_pattern("HardcodedSecret", ".env", "API key exposed", severity="critical")
        collector.from_error("PermissionError", "access denied", tool_name="bash")
        collector.from_optimization("structure improved", improvement_score=0.65)
        collector.from_structure(total_files=30, total_packages=5, violations=3)

        all_items = collector.collect_all()
        assert len(all_items) >= 5

        digest = DailyDigestGenerator.generate(all_items, "2025-12-25")

        relay = IntelRelay(output_dir=tmp_path / "intel")
        result = relay.relay(digest)
        assert result.is_ok

        loaded = relay.load_digest("2025-12-25")
        assert loaded.is_ok
        assert loaded.data is not None
        assert len(loaded.data.items) == len(all_items)

        md = digest.to_markdown()
        assert "# 灵克情报日报 — 2025-12-25" in md
        assert any("关键" in f for f in digest.key_findings)
        assert any("安全" in r for r in digest.recommendations)


class TestSessionHistory:
    def test_submit_creates_session_history(self, tmp_path: Path) -> None:
        history_path = tmp_path / "data" / "session_history.json"
        engine = QueryEngine(config=QueryEngineConfig())
        engine.set_session_history_path(history_path)

        engine.submit("帮我看看 main.py")
        engine.submit("读一下 config.yaml")

        assert history_path.exists()
        data = json.loads(history_path.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["query"] == "帮我看看 main.py"
        assert data[1]["query"] == "读一下 config.yaml"
        assert "timestamp" in data[0]
        assert "session_id" in data[0]

    def test_session_history_appends_to_existing(self, tmp_path: Path) -> None:
        history_path = tmp_path / "data" / "session_history.json"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history_path.write_text(
            json.dumps([{"query": "old query", "timestamp": "2025-01-01T00:00:00"}]),
            encoding="utf-8",
        )

        engine = QueryEngine(config=QueryEngineConfig())
        engine.set_session_history_path(history_path)
        engine.submit("new query")

        data = json.loads(history_path.read_text(encoding="utf-8"))
        assert len(data) == 2
        assert data[0]["query"] == "old query"
        assert data[1]["query"] == "new query"

    def test_session_history_lingyi_compatible(self, tmp_path: Path) -> None:
        history_path = tmp_path / "data" / "session_history.json"
        engine = QueryEngine(config=QueryEngineConfig())
        engine.set_session_history_path(history_path)

        engine.submit("灵依能读到这条吗？")

        data = json.loads(history_path.read_text(encoding="utf-8"))
        entry = data[0]
        assert "query" in entry or "title" in entry
        assert "timestamp" in entry or "created_at" in entry

    def test_session_history_truncates_long_query(self, tmp_path: Path) -> None:
        history_path = tmp_path / "data" / "session_history.json"
        engine = QueryEngine(config=QueryEngineConfig())
        engine.set_session_history_path(history_path)

        long_query = "x" * 500
        engine.submit(long_query)

        data = json.loads(history_path.read_text(encoding="utf-8"))
        assert len(data[0]["query"]) <= 200
        assert len(data[0]["title"]) <= 80

    def test_corrupt_history_does_not_crash(self, tmp_path: Path) -> None:
        history_path = tmp_path / "data" / "session_history.json"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history_path.write_text("NOT JSON", encoding="utf-8")

        engine = QueryEngine(config=QueryEngineConfig())
        engine.set_session_history_path(history_path)
        engine.submit("should still work")

        assert True


class TestIntelCollectorIntegration:
    def test_engine_behavior_feeds_intel(self, tmp_path: Path) -> None:
        engine = QueryEngine(config=QueryEngineConfig())
        for _ in range(3):
            engine._track_behavior("胡说八道，你根本没读代码", "wrong answer", used_tools=False)

        items = engine._intel_collector.collect_all()
        assert len(items) >= 1
        behavior_items = [i for i in items if i.category == IntelCategory.BEHAVIOR]
        assert len(behavior_items) >= 1

    def test_collect_daily_digest_with_relay(self, tmp_path: Path) -> None:
        engine = QueryEngine(config=QueryEngineConfig())
        engine.init_intel(output_dir=tmp_path / "intel")

        for _ in range(2):
            engine._track_behavior("不对，你搞错了", "wrong", used_tools=False)

        result = engine.collect_daily_digest("2025-06-15")
        assert result.is_ok
        assert result.data is not None
        assert len(result.data.items) >= 1

        json_file = tmp_path / "intel" / "digest_2025-06-15.json"
        assert json_file.exists()

    def test_intel_collector_cleared_after_digest(self, tmp_path: Path) -> None:
        engine = QueryEngine(config=QueryEngineConfig())
        engine.init_intel(output_dir=tmp_path / "intel")

        engine._intel_collector.from_error("TestError", "test error for clearing")
        assert len(engine._intel_collector.items) >= 1

        engine.collect_daily_digest()
        assert len(engine._intel_collector.items) == 0
