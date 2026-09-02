"""Tests for daemon knowledge write and behavior history persistence."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch


from lingclaude.self_optimizer.daemon import DaemonState, OptimizationCycle, OptimizationDaemon


def _make_cycle(**overrides):
    defaults = dict(
        cycle_id=1,
        triggered_at=datetime.now().isoformat(),
        trigger_reason="幻觉风险过高 (60%) — 回答代码问题时未使用工具",
        trigger_type="behavior",
        trigger_priority="high",
        best_score=85.0,
        best_params={"max_class_size": 200},
        experiments=5,
        duration_seconds=1.5,
        violations_before=10,
        violations_after=3,
        report_path=None,
    )
    defaults.update(overrides)
    return OptimizationCycle(**defaults)


class TestKnowledgeWrite:
    def test_write_cycle_creates_rule(self, tmp_path):
        with patch.object(OptimizationDaemon, "__init__", lambda self, *a, **kw: None):
            daemon = OptimizationDaemon.__new__(OptimizationDaemon)
            daemon.state_dir = tmp_path
            daemon.state_path = tmp_path / "daemon_state.json"
            cycle = _make_cycle()

            with patch("lingclaude.self_optimizer.learner.knowledge.KnowledgeBase") as MockKB:
                mock_kb = MagicMock()
                MockKB.return_value = mock_kb
                daemon._write_cycle_to_knowledge(cycle)
                mock_kb.add_rule.assert_called_once()
                rule = mock_kb.add_rule.call_args[0][0]
                assert rule.name == "自优化周期 #1"
                assert "幻觉" in rule.description

    def test_write_cycle_failure_does_not_crash(self, tmp_path):
        with patch.object(OptimizationDaemon, "__init__", lambda self, *a, **kw: None):
            daemon = OptimizationDaemon.__new__(OptimizationDaemon)
            daemon.state_dir = tmp_path
            cycle = _make_cycle()

            with patch("lingclaude.self_optimizer.learner.knowledge.KnowledgeBase", side_effect=Exception("db error")):
                daemon._write_cycle_to_knowledge(cycle)


class TestBehaviorHistory:
    def test_save_and_load_empty(self, tmp_path):
        with patch.object(OptimizationDaemon, "__init__", lambda self, *a, **kw: None):
            daemon = OptimizationDaemon.__new__(OptimizationDaemon)
            daemon.state_dir = tmp_path
            result = daemon.load_behavior_history()
            assert result.is_ok
            assert result.data["total_turns"] == 0

    def test_save_accumulates(self, tmp_path):
        with patch.object(OptimizationDaemon, "__init__", lambda self, *a, **kw: None):
            daemon = OptimizationDaemon.__new__(OptimizationDaemon)
            daemon.state_dir = tmp_path
            daemon.save_behavior_history({
                "total_turns": 5,
                "frustration_count": 2,
                "corrections_received": 1,
                "tool_error_count": 0,
            })
            daemon.save_behavior_history({
                "total_turns": 3,
                "frustration_count": 1,
                "corrections_received": 0,
                "tool_error_count": 1,
            })
            history_result = daemon.load_behavior_history()
            assert history_result.is_ok
            history = history_result.data
            assert history["total_turns"] == 8
            assert history["total_frustration"] == 3
            assert history["total_corrections"] == 1
            assert history["total_tool_errors"] == 1

    def test_build_context_includes_cumulative(self, tmp_path):
        with patch.object(OptimizationDaemon, "__init__", lambda self, *a, **kw: None):
            daemon = OptimizationDaemon.__new__(OptimizationDaemon)
            daemon.state_dir = tmp_path
            daemon.state = DaemonState()
            daemon._behavior_snapshot = {}

            daemon.save_behavior_history({
                "total_turns": 10,
                "frustration_count": 4,
                "corrections_received": 2,
                "tool_error_count": 1,
            })
            ctx = daemon.build_context({}, user_triggered=False)
            assert ctx["cumulative_frustration"] == 4
            assert ctx["cumulative_corrections"] == 2

    def test_snapshots_append_as_time_series(self, tmp_path):
        """行为快照时序化（系统论修正）：除累计存量外必须留流量时间线。"""
        with patch.object(OptimizationDaemon, "__init__", lambda self, *a, **kw: None):
            daemon = OptimizationDaemon.__new__(OptimizationDaemon)
            daemon.state_dir = tmp_path
            daemon.save_behavior_history({
                "total_turns": 5, "frustration_count": 1,
                "corrections_received": 0, "tool_error_count": 2,
            })
            daemon.save_behavior_history({
                "total_turns": 3, "frustration_count": 0,
                "corrections_received": 1, "tool_error_count": 0,
            })
            history = daemon.load_behavior_history().data
            assert history["total_turns"] == 8  # 存量行为不变（向后兼容）
            snaps = history["snapshots"]
            assert len(snaps) == 2
            assert snaps[0]["turns"] == 5 and snaps[0]["frustration"] == 1
            assert snaps[1]["turns"] == 3 and snaps[1]["corrections"] == 1
            assert snaps[0]["ts"] <= snaps[1]["ts"]

    def test_snapshots_capped(self, tmp_path):
        with patch.object(OptimizationDaemon, "__init__", lambda self, *a, **kw: None):
            daemon = OptimizationDaemon.__new__(OptimizationDaemon)
            daemon.state_dir = tmp_path
            for _ in range(230):
                daemon.save_behavior_history({
                    "total_turns": 1, "frustration_count": 0,
                    "corrections_received": 0, "tool_error_count": 0,
                })
            history = daemon.load_behavior_history().data
            assert len(history["snapshots"]) == 200

    def test_behavior_trend_rates(self, tmp_path):
        """趋势 = 窗口内摩擦合计 ÷ 回合合计；快照不足时诚实返回空。"""
        with patch.object(OptimizationDaemon, "__init__", lambda self, *a, **kw: None):
            daemon = OptimizationDaemon.__new__(OptimizationDaemon)
            daemon.state_dir = tmp_path

            # 不足 2 条快照 → 不伪造趋势
            daemon.save_behavior_history({
                "total_turns": 10, "frustration_count": 1,
                "corrections_received": 0, "tool_error_count": 0,
            })
            assert daemon.behavior_trend().data == {}

            daemon.save_behavior_history({
                "total_turns": 10, "frustration_count": 3,
                "corrections_received": 1, "tool_error_count": 2,
            })
            trend = daemon.behavior_trend().data
            assert trend["frustration"] == round(4 / 20, 4)
            assert trend["tool_errors"] == round(2 / 20, 4)

    def test_load_old_format_without_snapshots(self, tmp_path):
        """旧格式（无 snapshots 字段）文件必须能加载且不炸。"""
        import json as _json
        (tmp_path / "behavior_history.json").write_text(
            _json.dumps({"total_turns": 7, "total_frustration": 2,
                         "total_corrections": 1, "total_tool_errors": 0}),
            encoding="utf-8",
        )
        with patch.object(OptimizationDaemon, "__init__", lambda self, *a, **kw: None):
            daemon = OptimizationDaemon.__new__(OptimizationDaemon)
            daemon.state_dir = tmp_path
            history = daemon.load_behavior_history().data
            assert history["total_turns"] == 7
            assert history.get("snapshots") in (None, [])


class TestUsageTracking:
    def test_add_usage_accumulates(self):
        from lingclaude.core.models import UsageSummary
        u = UsageSummary()
        u = u.add_usage(100, 50)
        assert u.input_tokens == 100
        assert u.output_tokens == 50
        u = u.add_usage(200, 100)
        assert u.input_tokens == 300
        assert u.output_tokens == 150

    def test_add_turn_still_works(self):
        from lingclaude.core.models import UsageSummary
        u = UsageSummary()
        u = u.add_turn("hello world", "response text")
        assert u.input_tokens == 2
        assert u.output_tokens == 2
