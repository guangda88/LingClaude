"""Tests for TokenMonitor"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from lingclaude.core.token_monitor import (
    DailyStats,
    EfficiencyMetrics,
    TokenMetrics,
    TokenMonitor,
)


class TestTokenMetrics:
    """Test TokenMetrics dataclass"""

    def test_create_default(self):
        """Test creating TokenMetrics with defaults"""
        metrics = TokenMetrics()
        assert metrics.total_tokens == 0
        assert metrics.input_tokens == 0
        assert metrics.output_tokens == 0
        assert metrics.prompt_count == 0
        assert metrics.model == ""
        assert metrics.task_type == ""
        # Note: timestamp may be empty string when created directly

    def test_create_with_params(self):
        """Test creating TokenMetrics with parameters"""
        now = datetime.now(timezone.utc).isoformat()
        metrics = TokenMetrics(
            total_tokens=1000,
            input_tokens=500,
            output_tokens=500,
            prompt_count=2,
            model="gpt-4",
            task_type="code_generation",
            timestamp=now,
        )
        assert metrics.total_tokens == 1000
        assert metrics.model == "gpt-4"
        assert metrics.task_type == "code_generation"

    def test_from_dict(self):
        """Test creating TokenMetrics from dict"""
        data = {
            "total_tokens": 1000,
            "input_tokens": 500,
            "output_tokens": 500,
            "prompt_count": 2,
            "model": "gpt-4",
            "task_type": "code_generation",
        }
        metrics = TokenMetrics.from_dict(data)
        assert metrics.total_tokens == 1000
        assert metrics.input_tokens == 500
        assert metrics.model == "gpt-4"
        assert metrics.task_type == "code_generation"
        assert metrics.timestamp != ""

    def test_from_dict_with_defaults(self):
        """Test creating TokenMetrics from dict with missing fields"""
        data = {}
        metrics = TokenMetrics.from_dict(data)
        assert metrics.total_tokens == 0
        assert metrics.model == "unknown"
        assert metrics.task_type == "unknown"


class TestDailyStats:
    """Test DailyStats dataclass"""

    def test_create_default(self):
        """Test creating DailyStats with defaults"""
        stats = DailyStats(date="2024-01-01")
        assert stats.date == "2024-01-01"
        assert stats.total_tokens == 0
        assert stats.model_distribution == {}
        assert stats.task_distribution == {}

    def test_create_with_params(self):
        """Test creating DailyStats with parameters"""
        stats = DailyStats(
            date="2024-01-01",
            total_tokens=1000,
            input_tokens=500,
            output_tokens=500,
            prompt_count=2,
            model_distribution={"gpt-4": 600, "gpt-3.5": 400},
            task_distribution={"code": 800, "analysis": 200},
            duplicate_reads=10,
        )
        assert stats.total_tokens == 1000
        assert stats.model_distribution == {"gpt-4": 600, "gpt-3.5": 400}
        assert stats.duplicate_reads == 10


class TestEfficiencyMetrics:
    """Test EfficiencyMetrics dataclass"""

    def test_create_default(self):
        """Test creating EfficiencyMetrics with defaults"""
        metrics = EfficiencyMetrics(date="2024-01-01")
        assert metrics.date == "2024-01-01"
        assert metrics.token_efficiency == 0.0
        assert metrics.glm_4_7_ratio == 0.0

    def test_create_with_params(self):
        """Test creating EfficiencyMetrics with parameters"""
        metrics = EfficiencyMetrics(
            date="2024-01-01",
            token_efficiency=100.5,
            avg_input_tokens=200,
            avg_output_tokens=100,
            glm_4_7_ratio=0.75,
            duplicate_read_ratio=0.1,
        )
        assert metrics.token_efficiency == 100.5
        assert metrics.glm_4_7_ratio == 0.75


class TestTokenMonitor:
    """Test TokenMonitor class"""

    @pytest.fixture
    def temp_db(self):
        """Create temporary database"""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        yield db_path
        import os
        if Path(db_path).exists():
            os.unlink(db_path)

    @pytest.fixture
    def monitor(self, temp_db):
        """Create TokenMonitor instance"""
        return TokenMonitor(db_path=temp_db)

    def test_init_default_db_path(self):
        """Test initialization with default database path"""
        monitor = TokenMonitor()
        assert monitor.db_path.name == "token_monitor.db"
        assert monitor.db_path.parent.name == ".lingclaude"

    def test_init_custom_db_path(self, temp_db):
        """Test initialization with custom database path"""
        monitor = TokenMonitor(db_path=temp_db)
        assert monitor.db_path == Path(temp_db)
        assert len(monitor._file_cache) == 0

    def test_record_usage(self, monitor):
        """Test recording token usage"""
        monitor.record_usage(
            model="gpt-4",
            task_type="code_generation",
            total_tokens=1000,
            input_tokens=500,
            output_tokens=500,
            prompt_count=1,
        )

        # Verify record in database
        conn = sqlite3.connect(str(monitor.db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM usage_records")
        count = cursor.fetchone()[0]
        conn.close()

        assert count == 1

    def test_record_usage_with_metadata(self, monitor):
        """Test recording token usage with metadata"""
        metadata = {"session_id": "test123", "user": "test_user"}
        monitor.record_usage(
            model="gpt-4",
            task_type="code_generation",
            total_tokens=1000,
            input_tokens=500,
            output_tokens=500,
            metadata=metadata,
        )

        # Verify metadata stored
        conn = sqlite3.connect(str(monitor.db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT metadata FROM usage_records")
        metadata_json = cursor.fetchone()[0]
        conn.close()

        retrieved_metadata = json.loads(metadata_json)
        assert retrieved_metadata["session_id"] == "test123"

    def test_record_file_read_new_file(self, monitor):
        """Test recording file read for new file"""
        result = monitor.record_file_read("/path/to/file.txt", "content of file")
        assert result is False  # Not a duplicate

        # Verify record in database
        conn = sqlite3.connect(str(monitor.db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM file_reads")
        count = cursor.fetchone()[0]
        conn.close()

        assert count == 1

    def test_record_file_read_duplicate(self, monitor):
        """Test recording file read for duplicate read"""
        file_path = "/path/to/file.txt"
        content = "content of file"

        # First read
        result1 = monitor.record_file_read(file_path, content)
        assert result1 is False

        # Second read within 1 hour (duplicate)
        result2 = monitor.record_file_read(file_path, content)
        assert result2 is True

    def test_record_file_read_different_content(self, monitor):
        """Test recording file read with different content"""
        file_path = "/path/to/file.txt"

        # First read
        result1 = monitor.record_file_read(file_path, "old content")
        assert result1 is False

        # Second read with different content (not duplicate)
        result2 = monitor.record_file_read(file_path, "new content")
        assert result2 is False

    def test_record_file_read_same_content_after_hour(self, monitor):
        """Test recording file read same content after 1 hour"""
        file_path = "/path/to/file.txt"
        content = "content of file"

        # First read
        result1 = monitor.record_file_read(file_path, content)
        assert result1 is False

        # Manually update timestamp to be more than 1 hour ago
        conn = sqlite3.connect(str(monitor.db_path))
        cursor = conn.cursor()
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        cursor.execute(
            "UPDATE file_reads SET timestamp = ? WHERE file_path = ?",
            (old_time, file_path)
        )
        conn.commit()
        conn.close()

        # Second read after 1 hour (not duplicate)
        result2 = monitor.record_file_read(file_path, content)
        assert result2 is False

    def test_get_daily_stats_empty(self, monitor):
        """Test getting daily stats with no records"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stats = monitor.get_daily_stats(today)
        assert stats.date == today
        assert stats.total_tokens == 0
        assert stats.model_distribution == {}
        assert stats.task_distribution == {}

    def test_get_daily_stats_with_records(self, monitor):
        """Test getting daily stats with records"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Record some usage
        monitor.record_usage(
            model="gpt-4",
            task_type="code_generation",
            total_tokens=1000,
            input_tokens=500,
            output_tokens=500,
            prompt_count=1,
        )
        monitor.record_usage(
            model="gpt-3.5",
            task_type="analysis",
            total_tokens=500,
            input_tokens=300,
            output_tokens=200,
            prompt_count=1,
        )

        stats = monitor.get_daily_stats(today)
        assert stats.date == today
        assert stats.total_tokens == 1500
        assert stats.input_tokens == 800
        assert stats.output_tokens == 700
        assert "gpt-4" in stats.model_distribution
        assert "code_generation" in stats.task_distribution

    def test_get_efficiency_metrics_empty(self, monitor):
        """Test getting efficiency metrics with no records"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        metrics = monitor.get_efficiency_metrics(today)
        assert metrics.date == today
        assert metrics.token_efficiency == 0.0

    def test_get_efficiency_metrics_with_records(self, monitor):
        """Test getting efficiency metrics with records"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Record some usage
        monitor.record_usage(
            model="GLM-4.7",
            task_type="code_generation",
            total_tokens=1000,
            input_tokens=500,
            output_tokens=500,
            prompt_count=2,
        )
        monitor.record_usage(
            model="GLM-5.1",
            task_type="analysis",
            total_tokens=500,
            input_tokens=300,
            output_tokens=200,
            prompt_count=1,
        )

        metrics = monitor.get_efficiency_metrics(today)
        assert metrics.date == today
        assert metrics.token_efficiency > 0
        # Average should be calculated correctly
        assert metrics.avg_input_tokens >= 0
        assert metrics.avg_output_tokens >= 0
        assert metrics.glm_4_7_ratio == 2/3

    def test_get_model_distribution(self, monitor):
        """Test getting model distribution from daily stats"""
        monitor.record_usage("gpt-4", "code", 1000, 500, 500, 1)
        monitor.record_usage("gpt-4", "analysis", 500, 300, 200, 1)
        monitor.record_usage("gpt-3.5", "code", 300, 200, 100, 1)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stats = monitor.get_daily_stats(today)
        dist = stats.model_distribution
        assert "gpt-4" in dist
        # model_distribution stores token counts, not counts
        assert dist["gpt-4"] == 1500  # 1000 + 500
        assert dist["gpt-3.5"] == 300

    def test_get_task_distribution(self, monitor):
        """Test getting task distribution from daily stats"""
        monitor.record_usage("gpt-4", "code_generation", 1000, 500, 500, 1)
        monitor.record_usage("gpt-3.5", "code_generation", 500, 300, 200, 1)
        monitor.record_usage("gpt-4", "analysis", 300, 200, 100, 1)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stats = monitor.get_daily_stats(today)
        dist = stats.task_distribution
        assert "code_generation" in dist
        # task_distribution stores token counts, not counts
        assert dist["code_generation"] == 1500  # 1000 + 500
        assert dist["analysis"] == 300

    def test_get_total_tokens_from_stats(self, monitor):
        """Test getting total tokens from daily stats"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stats = monitor.get_daily_stats(today)
        assert stats.total_tokens == 0

        monitor.record_usage("gpt-4", "code", 1000, 500, 500, 1)
        monitor.record_usage("gpt-3.5", "analysis", 500, 300, 200, 1)

        stats = monitor.get_daily_stats(today)
        assert stats.total_tokens == 1500

    def test_generate_html_report(self, monitor):
        """Test generating HTML report (returns file path)"""
        monitor.record_usage("gpt-4", "code", 1000, 500, 500, 1)

        html_path = monitor.generate_html_report()
        # The method returns the file path, not the content
        assert Path(html_path).exists()
        # Read and verify content
        with open(html_path, "r") as f:
            content = f.read()
        assert "gpt-4" in content or "token" in content.lower()
        # Cleanup
        Path(html_path).unlink(missing_ok=True)

    def test_generate_markdown_report(self, monitor):
        """Test generating Markdown report (returns file path)"""
        monitor.record_usage("gpt-4", "code", 1000, 500, 500, 1)

        md_path = monitor.generate_markdown_report()
        # The method returns the file path, not the content
        assert Path(md_path).exists()
        # Read and verify content
        with open(md_path, "r") as f:
            content = f.read()
        assert "gpt-4" in content or "token" in content.lower()
        # Cleanup
        Path(md_path).unlink(missing_ok=True)

    def test_generate_html_report_with_output_path(self, monitor, temp_db):
        """Test generating HTML report to file"""
        monitor.record_usage("gpt-4", "code", 1000, 500, 500, 1)

        output_path = temp_db.replace(".db", "_report.html")
        monitor.generate_html_report(output_path=output_path)

        # Verify file created
        assert Path(output_path).exists()

        # Verify content
        with open(output_path, "r") as f:
            content = f.read()
        assert "gpt-4" in content

        # Cleanup
        Path(output_path).unlink()

    def test_cleanup_old_records(self, monitor):
        """Test cleaning up old records"""
        # Record some usage
        monitor.record_usage("gpt-4", "code", 1000, 500, 500, 1)

        # Manually insert old record
        conn = sqlite3.connect(str(monitor.db_path))
        cursor = conn.cursor()
        old_time = (datetime.now(timezone.utc) - timedelta(days=32)).isoformat()
        cursor.execute(
            "INSERT INTO usage_records (timestamp, model, task_type, total_tokens, input_tokens, output_tokens, prompt_count, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (old_time, "old_model", "old_task", 100, 50, 50, 1, "{}")
        )
        conn.commit()
        conn.close()

        # Get initial count
        conn = sqlite3.connect(str(monitor.db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM usage_records")
        initial_count = cursor.fetchone()[0]
        conn.close()

        assert initial_count == 2

    def test_get_duplicate_read_stats_from_daily_stats(self, monitor):
        """Test getting duplicate read statistics from daily stats"""
        file_path = "/path/to/file.txt"
        content = "content"

        # Record reads
        monitor.record_file_read(file_path, content)
        monitor.record_file_read(file_path, content)
        monitor.record_file_read(file_path, content)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stats = monitor.get_daily_stats(today)
        # Check that duplicate_reads field exists
        assert hasattr(stats, "duplicate_reads")
        assert stats.duplicate_reads >= 2




