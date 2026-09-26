"""Tests for DataFlywheel — M1数据飞轮"""
from __future__ import annotations

from pathlib import Path

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


class TestRepoDataDecoyGuard:
    """C3 护栏：仓库 data/ 诱饵库路径形态告警"""

    def test_decoy_form_triggers_warning(self, tmp_path, caplog):
        """db_path 指向仓库 data/data_flywheel.db 形态时告警（直测告警方法，不真实构造——否则测试自己会重建诱饵库）"""
        import logging
        from lingclaude.core import data_flywheel as df_mod

        fw = DataFlywheel(db_path=str(tmp_path / "normal.db"))
        fw.db_path = (
            Path(df_mod.__file__).parent.parent.parent / "data" / df_mod.FLYWHEEL_DB_NAME
        )
        with caplog.at_level(logging.WARNING, logger="lingclaude.core.data_flywheel"):
            fw._warn_if_repo_data_decoy_form()
        assert any("[C3]" in r.message for r in caplog.records)

    def test_normal_paths_no_warning(self, tmp_path, caplog):
        """默认路径与 tmp 路径均不误报"""
        import logging

        with caplog.at_level(logging.WARNING, logger="lingclaude.core.data_flywheel"):
            DataFlywheel()  # 默认真库路径
            DataFlywheel(db_path=str(tmp_path / "normal.db"))
        assert not any("[C3]" in r.message for r in caplog.records)

    def test_no_repo_data_decoy_db(self):
        """仓库哨兵：data/ 下不得再现 flywheel 诱饵库（事故工件，再现即 fail）"""
        from lingclaude.core import data_flywheel as df_mod

        decoy = Path(df_mod.__file__).parent.parent.parent / "data" / df_mod.FLYWHEEL_DB_NAME
        assert not decoy.exists(), f"诱饵库再现（0字节分流温床）: {decoy}"


class TestErrorRateScopeFix:
    """口径分层回归 (2026-09-26)：AI 复发率只统计 hallucination_*，
    环境故障(tool_error)/防线拦截(permission_*)/打断(hard_interrupt) 剥离。
    此前 88.9% 重复 tool_error 喂出 98% 「错误复发率」虚警。"""

    def _log(self, fw, ptype, msg, n=1):
        for i in range(n):
            fw.log_error(ErrorPattern(
                pattern_type=ptype, file_path="x.py", error_message=msg,
                tool_name="bash", context="", session_id="s",
                occurred_at="2026-09-26T10:00:00"))

    def test_ai_rate_excludes_noise(self, flywheel):
        # 大量环境故障（同 message 重复 = 高噪声复发）
        self._log(flywheel, "tool_error", "bash timeout", n=50)
        # 少量真实 AI 犯错（互不重复）
        self._log(flywheel, "hallucination:hard_fact", "assert A")
        self._log(flywheel, "hallucination:hard_fact", "assert B")
        s = flywheel.get_stats()
        assert s.total_errors == 52          # 全量口径不变
        assert s.noise_error_total == 50     # 噪声剥离
        assert s.ai_error_total == 2         # 只剩真正的 AI 错误
        assert s.ai_recurrence_rate == 0.0   # 两条 AI 错误不重复 → 0%

    def test_ai_rate_counts_real_recurrence(self, flywheel):
        # 同一条 AI 断言复发 3 次 + 噪声
        self._log(flywheel, "tool_error", "bash timeout", n=10)
        self._log(flywheel, "hallucination:unsupported", "same claim", n=3)
        s = flywheel.get_stats()
        assert s.ai_error_total == 3
        assert s.ai_error_unique == 1
        # get_stats 对 rate round(…, 3)，容差需宽于 1e-6
        assert abs(s.ai_recurrence_rate - (2 / 3)) < 5e-3  # (3-1)/3

    def test_record_recurrence_uses_real_message(self, flywheel):
        # 埋点传真实 error_message → 同断言才计复发，不同断言不计
        flywheel.record_recurrence(
            session_id="s", fact_types=["hard_fact"],
            error_message="claim X about API")
        flywheel.record_recurrence(
            session_id="s", fact_types=["hard_fact"],
            error_message="claim X about API")   # 同断言复发
        flywheel.record_recurrence(
            session_id="s", fact_types=["hard_fact"],
            error_message="claim Y about DB")    # 不同断言
        s = flywheel.get_stats()
        assert s.ai_error_total == 3
        assert s.ai_error_unique == 2           # 真实 message 区分了 X 和 Y
        assert abs(s.ai_recurrence_rate - (1 / 3)) < 5e-3
