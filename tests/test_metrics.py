from __future__ import annotations

from pathlib import Path

import pytest

from lingclaude.core.metrics import (
    MetricPoint,
    MetricsStore,
    QualityScorer,
)


@pytest.fixture
def store(tmp_path: Path) -> MetricsStore:
    s = MetricsStore(tmp_path / "test_metrics.db")
    yield s
    s.close()


class TestMetricPoint:
    def test_creation(self) -> None:
        p = MetricPoint(
            timestamp="2026-01-01T00:00:00Z",
            category="quality",
            name="overall",
            value=0.85,
            tags=(("source", "auto"),),
        )
        assert p.category == "quality"
        assert p.value == 0.85
        assert p.tags == (("source", "auto"),)

    def test_frozen(self) -> None:
        p = MetricPoint(timestamp="t", category="c", name="n", value=1.0)
        with pytest.raises(AttributeError):
            p.value = 0.5  # type: ignore[misc]


class TestMetricsStoreRecord:
    def test_record_single(self, store: MetricsStore) -> None:
        result = store.record("quality", "overall", 0.85)
        assert result.is_ok
        assert result.data.value == 0.85
        assert result.data.category == "quality"

    def test_record_with_tags(self, store: MetricsStore) -> None:
        result = store.record("behavior", "hallucination", 0.1, source="auto")
        assert result.is_ok
        assert ("source", "auto") in result.data.tags

    def test_record_batch(self, store: MetricsStore) -> None:
        points = (
            MetricPoint("t1", "q", "a", 0.8),
            MetricPoint("t2", "q", "a", 0.9),
            MetricPoint("t3", "q", "a", 0.7),
        )
        result = store.record_batch(points)
        assert result.is_ok
        assert result.data == 3


class TestMetricsStoreQuery:
    def test_query_all(self, store: MetricsStore) -> None:
        store.record("q", "a", 0.8)
        store.record("q", "b", 0.9)
        result = store.query()
        assert result.is_ok
        assert len(result.data) == 2

    def test_query_by_category(self, store: MetricsStore) -> None:
        store.record("quality", "x", 1.0)
        store.record("behavior", "y", 0.5)
        result = store.query(category="quality")
        assert result.is_ok
        assert len(result.data) == 1
        assert result.data[0].category == "quality"

    def test_query_by_name(self, store: MetricsStore) -> None:
        store.record("q", "overall", 0.8)
        store.record("q", "safety", 0.9)
        result = store.query(category="q", name="overall")
        assert result.is_ok
        assert len(result.data) == 1
        assert result.data[0].name == "overall"

    def test_query_with_limit(self, store: MetricsStore) -> None:
        for i in range(20):
            store.record("q", "x", float(i))
        result = store.query(limit=5)
        assert result.is_ok
        assert len(result.data) == 5

    def test_query_empty(self, store: MetricsStore) -> None:
        result = store.query(category="nonexistent")
        assert result.is_ok
        assert result.data == ()

    def test_query_by_time_range(self, store: MetricsStore) -> None:
        store.record("q", "a", 0.8)
        result = store.query(since="2020-01-01", until="2099-12-31")
        assert result.is_ok
        assert len(result.data) >= 1


class TestMetricsStoreTrend:
    def test_trend_up(self, store: MetricsStore) -> None:
        for v in [0.5, 0.6, 0.7, 0.8, 0.9]:
            store.record("q", "score", v)
        result = store.get_trend("q", "score")
        assert result.is_ok
        assert result.data.direction == "up"
        assert result.data.delta > 0
        assert len(result.data.points) == 5

    def test_trend_down(self, store: MetricsStore) -> None:
        for v in [0.9, 0.7, 0.5, 0.3, 0.1]:
            store.record("q", "score", v)
        result = store.get_trend("q", "score")
        assert result.is_ok
        assert result.data.direction == "down"
        assert result.data.delta < 0

    def test_trend_flat(self, store: MetricsStore) -> None:
        for _ in range(5):
            store.record("q", "score", 0.5)
        result = store.get_trend("q", "score")
        assert result.is_ok
        assert result.data.direction == "flat"

    def test_trend_empty(self, store: MetricsStore) -> None:
        result = store.get_trend("q", "nonexistent")
        assert result.is_ok
        assert result.data.direction == "flat"
        assert result.data.points == ()

    def test_trend_with_window(self, store: MetricsStore) -> None:
        for v in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
            store.record("q", "x", v)
        result = store.get_trend("q", "x", window=3)
        assert result.is_ok
        assert len(result.data.points) == 3


class TestMetricsStoreHelpers:
    def test_get_categories(self, store: MetricsStore) -> None:
        store.record("quality", "a", 0.8)
        store.record("behavior", "b", 0.5)
        store.record("structure", "c", 0.3)
        result = store.get_categories()
        assert result.is_ok
        assert set(result.data) == {"quality", "behavior", "structure"}

    def test_get_latest(self, store: MetricsStore) -> None:
        store.record("q", "x", 0.5)
        store.record("q", "x", 0.9)
        result = store.get_latest("q", "x")
        assert result.is_ok
        assert result.data is not None
        assert result.data.value == 0.9

    def test_get_latest_nonexistent(self, store: MetricsStore) -> None:
        result = store.get_latest("q", "none")
        assert result.is_ok
        assert result.data is None

    def test_get_statistics(self, store: MetricsStore) -> None:
        store.record("quality", "a", 0.8)
        store.record("quality", "b", 0.9)
        store.record("behavior", "c", 0.5)
        result = store.get_statistics()
        assert result.is_ok
        assert result.data["total_points"] == 3
        assert result.data["categories"]["quality"] == 2
        assert result.data["categories"]["behavior"] == 1

    def test_prune(self, store: MetricsStore) -> None:
        store.record("q", "a", 0.8)
        store.record("q", "a", 0.9)
        result = store.prune(before="2099-01-01")
        assert result.is_ok
        assert result.data == 2

    def test_close_and_reopen(self, tmp_path: Path) -> None:
        db = tmp_path / "persist.db"
        s1 = MetricsStore(db)
        s1.record("q", "x", 0.85)
        s1.close()
        s2 = MetricsStore(db)
        result = s2.query(category="q")
        s2.close()
        assert result.is_ok
        assert len(result.data) == 1
        assert result.data[0].value == 0.85


class TestQualityScorer:
    def test_score_structure_clean(self) -> None:
        scorer = QualityScorer()
        score = scorer.score_structure({"violations": 0, "avg_complexity": 5.0, "large_classes": 0})
        assert score >= 0.9

    def test_score_structure_violations(self) -> None:
        scorer = QualityScorer()
        score = scorer.score_structure({"violations": 10, "avg_complexity": 5.0, "large_classes": 0})
        assert score <= 0.1

    def test_score_structure_high_complexity(self) -> None:
        scorer = QualityScorer()
        score = scorer.score_structure({"violations": 0, "avg_complexity": 20.0, "large_classes": 0})
        assert score <= 0.5

    def test_score_behavior_good(self) -> None:
        scorer = QualityScorer()
        score = scorer.score_behavior({
            "hallucination_risk": 0.0,
            "frustration_rate": 0.0,
            "tool_error_rate": 0.0,
        })
        assert score >= 0.99

    def test_score_behavior_bad(self) -> None:
        scorer = QualityScorer()
        score = scorer.score_behavior({
            "hallucination_risk": 0.8,
            "frustration_rate": 0.5,
            "tool_error_rate": 0.7,
        })
        assert score <= 0.35

    def test_score_safety_good(self) -> None:
        scorer = QualityScorer()
        score = scorer.score_safety({
            "hard_stops": 0,
            "verification_passes": 10,
            "verification_total": 10,
        })
        assert score >= 0.99

    def test_score_safety_hard_stops(self) -> None:
        scorer = QualityScorer()
        score = scorer.score_safety({
            "hard_stops": 3,
            "verification_passes": 10,
            "verification_total": 10,
        })
        assert score < 0.5

    def test_score_knowledge_active(self) -> None:
        scorer = QualityScorer()
        score = scorer.score_knowledge({
            "total_rules": 50,
            "by_status": {"active": 40},
            "average_quality": 0.9,
        })
        assert score >= 0.7

    def test_score_knowledge_empty(self) -> None:
        scorer = QualityScorer()
        score = scorer.score_knowledge({
            "total_rules": 0,
            "by_status": {},
            "average_quality": 0.0,
        })
        assert score < 0.3

    def test_compute_overall_with_all(self) -> None:
        scorer = QualityScorer()
        score = scorer.compute_overall(
            structure={"violations": 2, "avg_complexity": 8.0, "large_classes": 1},
            behavior={"hallucination_risk": 0.1, "frustration_rate": 0.05, "tool_error_rate": 0.1},
            safety={"hard_stops": 0, "verification_passes": 20, "verification_total": 20},
            knowledge={"total_rules": 30, "by_status": {"active": 25}, "average_quality": 0.8},
        )
        assert 0.0 <= score.overall <= 1.0
        assert score.safety > 0.5
        assert score.behavior > 0.5
        assert len(score.timestamp) > 0

    def test_compute_overall_defaults(self) -> None:
        scorer = QualityScorer()
        score = scorer.compute_overall()
        assert score.overall == 0.5
        assert score.safety == 0.5

    def test_compute_overall_records_to_store(self, store: MetricsStore) -> None:
        scorer = QualityScorer(store)
        scorer.compute_overall(
            structure={"violations": 0, "avg_complexity": 5.0, "large_classes": 0},
        )
        result = store.query(category="quality")
        assert result.is_ok
        assert len(result.data) >= 1

    def test_compute_overall_weights(self) -> None:
        scorer = QualityScorer()
        score = scorer.compute_overall(
            structure={"violations": 0, "avg_complexity": 5.0, "large_classes": 0},
            behavior={"hallucination_risk": 0.0, "frustration_rate": 0.0, "tool_error_rate": 0.0},
            safety={"hard_stops": 0, "verification_passes": 10, "verification_total": 10},
            knowledge={"total_rules": 50, "by_status": {"active": 50}, "average_quality": 1.0},
        )
        assert score.overall > 0.9
