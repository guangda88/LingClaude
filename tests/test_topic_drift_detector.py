from __future__ import annotations

from pathlib import Path

import pytest

from lingclaude.core.topic_drift_detector import (
    TopicDriftAlert,
    TopicDriftDetector,
    extract_active_tasks_from_handover,
)


@pytest.fixture
def tmp_handover(tmp_path: Path) -> Path:
    """Create a minimal handover.md for testing."""
    content = """# 灵克 Handover

## 当前用户任务

| 任务 | 状态 | 产出 |
|------|------|------|
| 多指标Phase 1 | in_progress | degradation_detector.py完成 |
| 灵壳设计 | in_progress | v3设计 |

## 其他

无关内容
"""
    p = tmp_path / "handover.md"
    p.write_text(content, encoding="utf-8")
    return p


class TestTopicDriftDetector:
    def test_no_drift_with_empty_history(self, tmp_handover: Path) -> None:
        d = TopicDriftDetector(handover_path=tmp_handover)
        assert d.detect_drift() == []

    def test_no_drift_below_min_calls(self, tmp_handover: Path) -> None:
        d = TopicDriftDetector(handover_path=tmp_handover)
        for i in range(5):
            d.record_call(i, "edit", {"file_path": "/tmp/foo.py"}, "ok")
        assert d.detect_drift() == []

    def test_no_drift_on_topic_calls(self, tmp_handover: Path) -> None:
        d = TopicDriftDetector(
            handover_path=tmp_handover,
            drift_threshold=0.7,
            window_size=20,
        )
        d.update_active_topics(["degradation", "多指标", "灵壳"])
        for i in range(20):
            d.record_call(i, "edit", {"file_path": "/tmp/degradation.py"}, "ok")
        alerts = d.detect_drift()
        assert alerts == []

    def test_drift_detected_on_off_topic(self, tmp_handover: Path) -> None:
        d = TopicDriftDetector(
            handover_path=tmp_handover,
            drift_threshold=0.7,
            window_size=20,
        )
        d.update_active_topics(["degradation", "多指标"])
        for i in range(20):
            d.record_call(i, "bash", {"command": "ls /tmp"}, "output")
        alerts = d.detect_drift()
        assert len(alerts) >= 1
        assert alerts[0].drift_percent >= 0.7

    def test_partial_drift_no_alert(self, tmp_handover: Path) -> None:
        d = TopicDriftDetector(
            handover_path=tmp_handover,
            drift_threshold=0.7,
            window_size=20,
        )
        d.update_active_topics(["degradation"])
        for i in range(10):
            d.record_call(i, "edit", {"file_path": "degradation.py"}, "ok")
        for i in range(10):
            d.record_call(10 + i, "bash", {"command": "ls"}, "output")
        alerts = d.detect_drift()
        assert alerts == []

    def test_update_active_topics(self, tmp_handover: Path) -> None:
        d = TopicDriftDetector(handover_path=tmp_handover)
        d.update_active_topics(["新任务A", "新任务B"])
        assert "新任务A" in d._active_topics

    def test_reset_clears_history(self, tmp_handover: Path) -> None:
        d = TopicDriftDetector(handover_path=tmp_handover)
        for i in range(15):
            d.record_call(i, "edit", {"file_path": "/f.py"}, "ok")
        d.reset()
        assert len(d._call_history) == 0

    def test_extract_topic_from_edit(self, tmp_handover: Path) -> None:
        d = TopicDriftDetector(handover_path=tmp_handover)
        topic = d._extract_topic("edit", {"file_path": "/tmp/degradation.py"})
        assert "degradation.py" in topic

    def test_extract_topic_from_grep(self, tmp_handover: Path) -> None:
        d = TopicDriftDetector(handover_path=tmp_handover)
        topic = d._extract_topic("grep", {"pattern": "def.*drift"})
        assert "drift" in topic

    def test_extract_topic_from_bash(self, tmp_handover: Path) -> None:
        d = TopicDriftDetector(handover_path=tmp_handover)
        topic = d._extract_topic("bash", {"command": "pytest tests/"})
        assert "pytest" in topic

    def test_is_topic_active_match(self, tmp_handover: Path) -> None:
        d = TopicDriftDetector(handover_path=tmp_handover)
        d.update_active_topics(["degradation"])
        assert d._is_topic_active("file:degradation.py") is True

    def test_is_topic_active_no_match(self, tmp_handover: Path) -> None:
        d = TopicDriftDetector(handover_path=tmp_handover)
        d.update_active_topics(["degradation"])
        assert d._is_topic_active("bash:ls") is False

    def test_is_topic_active_empty_list(self, tmp_handover: Path) -> None:
        d = TopicDriftDetector(handover_path=tmp_handover)
        d.update_active_topics([])
        # No active topics → everything is "active"
        assert d._is_topic_active("anything") is True

    def test_window_sliding(self, tmp_handover: Path) -> None:
        d = TopicDriftDetector(
            handover_path=tmp_handover,
            window_size=10,
        )
        d.update_active_topics(["degradation"])
        for i in range(50):
            d.record_call(i, "bash", {"command": "ls"}, "output")
        assert len(d._call_history) <= 20


class TestExtractActiveTasks:
    def test_extract_from_real_handover(self, tmp_handover: Path) -> None:
        tasks = extract_active_tasks_from_handover(tmp_handover)
        assert len(tasks) >= 2
        assert any("多指标" in t or "Phase 1" in t for t in tasks)

    def test_extract_missing_file(self, tmp_path: Path) -> None:
        assert extract_active_tasks_from_handover(tmp_path / "nonexistent.md") == []

    def test_extract_malformed(self, tmp_path: Path) -> None:
        p = tmp_path / "handover.md"
        p.write_text("no tables here", encoding="utf-8")
        assert extract_active_tasks_from_handover(p) == []


class TestTopicDriftAlert:
    def test_alert_fields(self) -> None:
        alert = TopicDriftAlert(
            topic="topic_drift",
            drift_percent=0.85,
            active_topics=["taskA"],
            evidence_count=17,
            total_calls=20,
            detail="85% off-topic",
            msg_index=42,
        )
        assert alert.drift_percent == 0.85
        assert alert.evidence_count == 17
        assert "85%" in alert.detail
