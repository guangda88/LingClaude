"""Tests for DataFlywheel — M1数据飞轮"""
from __future__ import annotations

import pytest

from datetime import datetime

from lingclaude.core.data_flywheel import DataFlywheel, ErrorPattern, CorrectionEntry, FlywheelStats


@pytest.fixture
def flywheel(tmp_path):
    fw = DataFlywheel(db_path=str(tmp_path / "test_flywheel.db"))
    yield fw
    fw.close()


class TestDataFlywheelBasics:

    def test_log_error(self, flywheel):
        error = ErrorPattern(
            pattern_type="syntax_error",
            file_path="test.py",
            error_message="SyntaxError: invalid syntax",
            tool_name="write",
            context="写入前检查",
            session_id="test-session",
            occurred_at="2026-04-15T10:00:00",
        )
        result = flywheel.log_error(error)
        assert result.is_ok
        assert result.data > 0

    def test_log_correction(self, flywheel):
        corr = CorrectionEntry(
            original_error="SyntaxError: invalid syntax",
            correction="修复括号匹配",
            source="verification_gate",
            confidence=0.9,
            applied_at="2026-04-15T10:00:01",
        )
        result = flywheel.log_correction(corr)
        assert result.is_ok
        assert result.data > 0

    def test_get_stats_empty(self, flywheel):
        stats = flywheel.get_stats()
        assert isinstance(stats, FlywheelStats)
        assert stats.total_errors == 0
        assert stats.total_corrections == 0

    def test_get_stats_with_errors(self, flywheel):
        for i in range(5):
            flywheel.log_error(ErrorPattern(
                pattern_type="syntax_error",
                file_path="foo.py",
                error_message=f"Error #{i}",
                tool_name="write",
                context="test",
                occurred_at=f"2026-04-15T10:00:0{i}",
            ))
        for i in range(2):
            flywheel.log_correction(CorrectionEntry(
                original_error="Error #0",
                correction=f"Fix #{i}",
                source="gate",
                confidence=0.8,
                applied_at=f"2026-04-15T10:01:0{i}",
            ))
        stats = flywheel.get_stats()
        assert stats.total_errors == 5
        assert stats.total_corrections == 2
        assert stats.correction_rate == 0.4
        assert "syntax_error" in stats.error_categories

    def test_recurring_errors(self, flywheel):
        for i in range(3):
            flywheel.log_error(ErrorPattern(
                pattern_type="syntax_error",
                file_path="recurring.py",
                error_message="Same error",
                tool_name="edit",
                context="test",
                occurred_at=f"2026-04-15T10:{i:02d}:00",
            ))
        flywheel.log_error(ErrorPattern(
            pattern_type="syntax_error",
            file_path="other.py",
            error_message="Different error",
            tool_name="write",
            context="test",
            occurred_at="2026-04-15T10:05:00",
        ))
        result = flywheel.get_recurring_errors(min_count=2)
        assert result.is_ok
        assert len(result.data) == 1
        assert result.data[0]["count"] == 3

    def test_should_alert(self, flywheel):
        assert not flywheel.should_alert()
        for i in range(5):
            flywheel.log_error(ErrorPattern(
                pattern_type="syntax_error",
                file_path="repeat.py",
                error_message="Same error",
                tool_name="write",
                context="test",
                occurred_at=f"2026-04-15T10:{i:02d}:00",
            ))
        assert flywheel.should_alert(threshold=0.3)

    def test_get_recent_errors(self, flywheel):
        for i in range(15):
            flywheel.log_error(ErrorPattern(
                pattern_type="test",
                file_path=f"file{i}.py",
                error_message=f"Error {i}",
                tool_name="write",
                context="",
                occurred_at=f"2026-04-15T10:{i:02d}:00",
            ))
        result = flywheel.get_recent_errors(limit=5)
        assert result.is_ok
        assert len(result.data) == 5

    def test_persistence(self, tmp_path):
        db_path = str(tmp_path / "persist.db")
        fw1 = DataFlywheel(db_path=db_path)
        fw1.log_error(ErrorPattern(
            pattern_type="test",
            file_path="persist.py",
            error_message="persistent error",
            tool_name="write",
            context="",
            occurred_at="2026-04-15T10:00:00",
        ))
        fw1.close()

        fw2 = DataFlywheel(db_path=db_path)
        stats = fw2.get_stats()
        assert stats.total_errors == 1
        fw2.close()


# ---------- R9: corrections 消费面 (2026-09-23) ----------


class TestGetRecentCorrections:
    """R9: get_recent_corrections 两路取样（24h 热记忆 + 冷唤醒）"""

    def test_reads_real_content(self, flywheel):
        """新格式语料可读出真实内容，且字段完整"""
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")
        for i in range(2):
            res = flywheel.log_correction(
                CorrectionEntry(
                    original_error=f"编造了数字{i}",
                    correction=f"必须先工具实证再报数{i}",
                    source="user",
                    confidence=0.8,
                    applied_at=now,
                )
            )
            assert res.is_ok
        got = flywheel.get_recent_corrections(limit=2)
        assert got.is_ok
        assert len(got.data) >= 2
        assert got.data[0]["correction"] == "必须先工具实证再报数1"  # id DESC, 最新在前
        assert "编造了数字1" in got.data[0]["original_error"]

    def test_stale_row_falls_to_cold_sample(self, flywheel):
        """48h 前的旧行不进 24h 窗，但可经冷唤醒（随机 1 条）浮出"""
        old = "2026-09-20T08:00:00.000000"
        res = flywheel.log_correction(
            CorrectionEntry(
                original_error="旧错误",
                correction="旧教训",
                source="user",
                confidence=0.7,
                applied_at=old,
            )
        )
        assert res.is_ok
        got = flywheel.get_recent_corrections(limit=2)
        assert got.is_ok
        # 24h 窗 0 条；冷唤醒 1 条必含旧行（库仅此一行）
        assert all("旧教训" not in r["correction"] for r in got.data[:0])  # 占位恒真
        assert len(got.data) == 1
        assert got.data[0]["correction"] == "旧教训"

    def test_empty_db_returns_ok_empty(self, flywheel):
        """空库 fail-soft：Result.ok([])，不抛异常"""
        got = flywheel.get_recent_corrections()
        assert got.is_ok
        assert got.data == []
