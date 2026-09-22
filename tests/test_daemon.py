from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from lingclaude.self_optimizer.daemon import (
    DaemonState,
    OptimizationCycle,
    OptimizationDaemon,
)
from lingclaude.self_optimizer.optimizer import OptimizationResult


class TestDaemonState:
    def test_default_state(self):
        state = DaemonState()
        assert state.total_cycles == 0
        assert state.total_improvements == 0
        assert state.last_optimization_time is None
        assert state.last_metrics == {}
        assert state.cycles == []

    def test_save_and_load(self, tmp_path):
        path = tmp_path / "state.json"
        state = DaemonState(
            last_optimization_time="2026-01-01T00:00:00",
            total_cycles=3,
            total_improvements=1,
            cycles=[{"cycle_id": 1}],
        )
        state.save(path)
        loaded = DaemonState.load(path)
        assert loaded.total_cycles == 3
        assert loaded.total_improvements == 1
        assert loaded.last_optimization_time == "2026-01-01T00:00:00"
        assert loaded.cycles == [{"cycle_id": 1}]

    def test_load_corrupted_file(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("not json {{{")
        state = DaemonState.load(path)
        assert state.total_cycles == 0

    def test_load_nonexistent(self, tmp_path):
        state = DaemonState.load(tmp_path / "nope.json")
        assert state.total_cycles == 0

    def test_cycles_capped_at_100(self, tmp_path):
        state = DaemonState()
        state.cycles = [{"cycle_id": i} for i in range(150)]
        path = tmp_path / "state.json"
        state.save(path)
        loaded = DaemonState.load(path)
        assert len(loaded.cycles) == 150

        state.total_cycles = 150
        cycle = OptimizationCycle(
            cycle_id=151,
            triggered_at="2026-01-01",
            trigger_reason="test",
            trigger_type="test",
            trigger_priority="low",
            best_score=0.0,
            best_params={},
            experiments=1,
            duration_seconds=0.1,
            violations_before=0,
            violations_after=0,
            report_path=None,
        )
        daemon = OptimizationDaemon.__new__(OptimizationDaemon)
        daemon.state = state
        daemon.state_path = path
        daemon._record_cycle(cycle)
        assert len(daemon.state.cycles) == 100


class TestOptimizationDaemon:
    def test_init_default(self, tmp_path):
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        assert daemon.target == "."
        assert daemon.state.total_cycles == 0

    def test_collect_metrics(self, tmp_path):
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        metrics_result = daemon.collect_metrics()
        assert metrics_result.is_ok
        assert "structure_violations" in metrics_result.data

    def test_build_context(self, tmp_path):
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        daemon.state.last_optimization_time = "2026-01-01T00:00:00"
        ctx = daemon.build_context({"violations": 0})
        assert ctx["last_optimization_time"] == "2026-01-01T00:00:00"

    def test_run_cycle_no_trigger(self, tmp_path):
        # 2026-09-22 F1 修正: target 封闭到 tmp 空目录。原 target="." 会随
        # 真实仓库增长偶发越过结构阈值（当天即复发），空目录 0 违规 → 稳定无触发。
        daemon = OptimizationDaemon(target=str(tmp_path), state_dir=tmp_path)
        result = daemon.run_cycle()
        assert result.is_ok
        assert result.data is None

    def test_run_cycle_user_trigger(self, tmp_path):
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)

        mock_result = OptimizationResult(
            success=True,
            best_params={"max_complexity": 20},
            best_score=0.0,
            experiments=3,
            duration=0.5,
            error=None,
            history=(),
        )

        with patch.object(daemon.trigger, "check_all_conditions") as mock_trigger:
            from lingclaude.self_optimizer.trigger import TriggerInfo
            mock_trigger.return_value = (
                True,
                TriggerInfo(
                    type="user",
                    reason="test",
                    priority="high",
                    current_value=None,
                    threshold=None,
                    metrics={},
                ),
            )
            with patch.object(daemon.optimizer, "optimize", return_value=mock_result):
                cycle_result = daemon.run_cycle()

        assert cycle_result.is_ok
        assert cycle_result.data is not None
        cycle = cycle_result.data
        assert cycle.cycle_id == 1
        assert cycle.best_score == 0.0
        assert daemon.state.total_cycles == 1

    def test_state_persists_across_instances(self, tmp_path):
        daemon1 = OptimizationDaemon(target=".", state_dir=tmp_path)
        daemon1.state.total_cycles = 5
        daemon1.state.last_optimization_time = "2026-01-01"
        daemon1.state.save(daemon1.state_path)

        daemon2 = OptimizationDaemon(target=".", state_dir=tmp_path)
        assert daemon2.state.total_cycles == 5

    def test_apply_params_no_config(self, tmp_path):
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        daemon._apply_params({"max_complexity": 20})

    def test_apply_params_with_config(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("model:\n  provider: openai\n")
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        # F1: OptimizerConfig 冻结，用 replace 重建；本用例测 legacy config.yaml 写路径，钉 goal=structure
        from dataclasses import replace as _dc_replace
        from lingclaude.core.config import load_config as _load_cfg
        _cfg = _load_cfg()
        daemon.config = _dc_replace(_cfg, optimizer=_dc_replace(_cfg.optimizer, goal="structure"))
        original_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp_path)
            # R2:写 config.yaml 是受控执行器，须显式授权
            os.environ["LINGCLAUDE_DAEMON_APPLY"] = "1"
            daemon._apply_params({"max_complexity": 20})
            import yaml
            raw = yaml.safe_load(config_path.read_text())
            assert raw["self_optimizer"]["triggers"]["max_complexity"] == 20
            # P0-N5: 写成功路径补审计留痕 — guard_pending.jsonl 出现已执行记录
            import json
            pending_path = Path(".lingclaude") / "guard_pending.jsonl"
            assert pending_path.exists(), "写配置成功应产生 guard_pending 留痕"
            recs = [
                json.loads(line)
                for line in pending_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            assert any(
                r["action"] == "optimize_write"
                and r["state"] == "approved_by_daemon"
                and r["params"]["applied_key"] == "max_complexity"
                and r["params"]["applied_value"] == 20
                for r in recs
            ), "成功写入后应有 state=approved_by_daemon 的闭环留痕"
        finally:
            os.chdir(original_cwd)
            os.environ.pop("LINGCLAUDE_DAEMON_APPLY", None)

    def test_apply_params_report_only_by_default(self, tmp_path):
        """R2:不设 LINGCLAUDE_DAEMON_APPLY 时绝不写 config.yaml（执行器限幅）。"""
        config_path = tmp_path / "config.yaml"
        original = "model:\n  provider: openai\n"
        config_path.write_text(original)
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        original_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp_path)
            os.environ.pop("LINGCLAUDE_DAEMON_APPLY", None)
            daemon._apply_params({"max_complexity": 99})
            assert config_path.read_text() == original, "report-only 模式不得改写配置"
        finally:
            os.chdir(original_cwd)

    def test_apply_params_single_param_cap(self, tmp_path):
        """R2:开启应用后每周期最多写 1 个参数（纠错幅度限幅，保归因与复测窗口）。"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("model:\n  provider: openai\n")
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        # F1: OptimizerConfig 冻结，用 replace 重建；本用例测 legacy config.yaml 写路径，钉 goal=structure
        from dataclasses import replace as _dc_replace
        from lingclaude.core.config import load_config as _load_cfg
        _cfg = _load_cfg()
        daemon.config = _dc_replace(_cfg, optimizer=_dc_replace(_cfg.optimizer, goal="structure"))
        original_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp_path)
            os.environ["LINGCLAUDE_DAEMON_APPLY"] = "1"
            daemon._apply_params({"max_complexity": 20, "coupling_limit": 7, "max_nesting_depth": 9})
            import yaml
            raw = yaml.safe_load(config_path.read_text())
            opt = raw["self_optimizer"]["optimization"]
            trg = raw["self_optimizer"]["triggers"]
            applied = [k for k, v in {**trg, **opt}.items()
                       if (k, v) in [("max_complexity", 20), ("coupling_limit", 7), ("max_nesting_depth", 9)]]
            assert len(applied) == 1, f"限幅失败，应用了 {len(applied)} 个参数"
        finally:
            os.chdir(original_cwd)
            os.environ.pop("LINGCLAUDE_DAEMON_APPLY", None)

    # ---- P0 (2026-09-23): _apply_behavior_params 写回分支测试证明 ----
    # 目的：F1 生效器存在，但 behavior_policy.yaml 写回分支（daemon.py:626-728）
    # 从未被测试覆盖。此组补齐——report-only 不动 yaml / APPLY=1 真落盘 /
    # 白名单外键拒绝 / int 强制 / 单键限幅 / 审计留痕。

    def _behavior_policy_path(self) -> "Path":
        from lingclaude.core.policy_loader import policies_dir
        return policies_dir() / "behavior_policy.yaml"

    @staticmethod
    def _yaml_get(raw_text: str, key: str):
        import yaml
        return yaml.safe_load(raw_text).get(key)

    def test_behavior_params_report_only_no_write(self, tmp_path):
        """P0-1: 默认 report-only，不得改写 behavior_policy.yaml。"""
        import os
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        policy = self._behavior_policy_path()
        original = policy.read_text(encoding="utf-8")
        os.environ.pop("LINGCLAUDE_DAEMON_APPLY", None)
        try:
            daemon._apply_behavior_params({"tool_repeat_limit": 9})
            assert policy.read_text(encoding="utf-8") == original, \
                "report-only 模式不得改写 behavior_policy.yaml"
        finally:
            os.environ.pop("LINGCLAUDE_DAEMON_APPLY", None)

    def test_behavior_params_apply_writes_yaml(self, tmp_path):
        """P0-2: APPLY=1 时写回 behavior_policy.yaml 且留痕。"""
        import os
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        policy = self._behavior_policy_path()
        original = policy.read_text(encoding="utf-8")
        os.environ["LINGCLAUDE_DAEMON_APPLY"] = "1"
        try:
            daemon._apply_behavior_params({"tool_repeat_limit": 4})
            import yaml
            raw = yaml.safe_load(policy.read_text(encoding="utf-8"))
            assert raw.get("tool_repeat_limit") == 4, \
                f"写回失败: tool_repeat_limit={raw.get('tool_repeat_limit')}"
        finally:
            # 恢复原文件，避免污染真实策略
            policy.write_text(original, encoding="utf-8")
            os.environ.pop("LINGCLAUDE_DAEMON_APPLY", None)

    def test_behavior_params_whitelist_rejects_unknown(self, tmp_path):
        """P0-3: 白名单外键不得写进 behavior_policy.yaml。"""
        import os
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        policy = self._behavior_policy_path()
        original = policy.read_text(encoding="utf-8")
        os.environ["LINGCLAUDE_DAEMON_APPLY"] = "1"
        try:
            daemon._apply_behavior_params({"max_class_size": 999})
            assert policy.read_text(encoding="utf-8") == original, \
                "白名单外键不得写进策略文件"
        finally:
            os.environ.pop("LINGCLAUDE_DAEMON_APPLY", None)

    def test_behavior_params_single_key_cap(self, tmp_path):
        """P0-4: 单键限幅——一周期最多写 1 个键。"""
        import os
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        policy = self._behavior_policy_path()
        original = policy.read_text(encoding="utf-8")
        os.environ["LINGCLAUDE_DAEMON_APPLY"] = "1"
        try:
            daemon._apply_behavior_params(
                {"tool_repeat_limit": 4, "consecutive_fail_limit": 3}
            )
            import yaml
            raw = yaml.safe_load(policy.read_text(encoding="utf-8"))
            changed = [k for k in ("tool_repeat_limit", "consecutive_fail_limit")
                       if raw.get(k) != self._yaml_get(original, k)]
            assert len(changed) <= 1, f"单键限幅失败，改动了 {len(changed)} 个键"
        finally:
            policy.write_text(original, encoding="utf-8")
            os.environ.pop("LINGCLAUDE_DAEMON_APPLY", None)

    def test_should_run_cycle_throttle(self, tmp_path):
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        # 从未跑过 → 该跑（诚实优先）
        assert daemon.should_run_cycle() is True
        # 2 小时前跑过 → 24h 节流内不跑
        from datetime import datetime, timedelta
        daemon.state.last_optimization_time = (
            datetime.now() - timedelta(hours=2)
        ).isoformat()
        assert daemon.should_run_cycle(min_interval_hours=24) is False
        # 48 小时前跑过 → 超过节流窗口
        daemon.state.last_optimization_time = (
            datetime.now() - timedelta(hours=48)
        ).isoformat()
        assert daemon.should_run_cycle(min_interval_hours=24) is True
        # 不可解析的时间戳 → 该跑（不因脏数据永久停摆）
        daemon.state.last_optimization_time = "not-a-date"
        assert daemon.should_run_cycle() is True

    def test_optimization_cycle_frozen(self):
        cycle = OptimizationCycle(
            cycle_id=1,
            triggered_at="2026-01-01",
            trigger_reason="test",
            trigger_type="user",
            trigger_priority="high",
            best_score=0.0,
            best_params={},
            experiments=1,
            duration_seconds=0.1,
            violations_before=0,
            violations_after=0,
            report_path=None,
        )
        with pytest.raises(AttributeError):
            cycle.cycle_id = 2

    def test_run_once_delegates(self, tmp_path):
        from lingclaude.core.types import Result
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        with patch.object(daemon, "run_cycle", return_value=Result.ok(None)) as mock:
            result = daemon.run_once()
        mock.assert_called_once()
        assert result.is_ok
        assert result.data is None

    # ---- F4 值守（2026-09-23）----

    def test_audit_watch_mounted(self, tmp_path):
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        assert daemon.audit_watch is not None
        assert daemon.audit_watch.state_path.parent == tmp_path

    def test_run_watch_calls_audit_watch(self, tmp_path):
        """run_watch 循环每轮调用值守（sleep 抛中断收一轮）。"""
        from lingclaude.core.types import Result

        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)
        with patch.object(daemon, "run_cycle", return_value=Result.ok(None)):
            with patch.object(
                daemon.audit_watch, "run_once",
                return_value={"exit": 0, "open_tasks": 0, "sweep_expired": []},
            ) as m:
                with patch(
                    "lingclaude.self_optimizer.daemon.time.sleep",
                    side_effect=KeyboardInterrupt,
                ):
                    daemon.run_watch(interval_seconds=1)  # 内部捕获中断优雅停止
        m.assert_called_once()

    def test_report_generated(self, tmp_path):
        daemon = OptimizationDaemon(target=".", state_dir=tmp_path)

        mock_result = OptimizationResult(
            success=True,
            best_params={"max_complexity": 20},
            best_score=0.0,
            experiments=3,
            duration=0.5,
            error=None,
            history=(),
        )

        with patch.object(daemon.trigger, "check_all_conditions") as mock_trigger:
            from lingclaude.self_optimizer.trigger import TriggerInfo
            mock_trigger.return_value = (
                True,
                TriggerInfo(
                    type="user",
                    reason="manual",
                    priority="high",
                    current_value=None,
                    threshold=None,
                    metrics={},
                ),
            )
            with patch.object(daemon.optimizer, "optimize", return_value=mock_result):
                cycle_result = daemon.run_cycle()

        assert cycle_result.is_ok
        assert cycle_result.data is not None
        cycle = cycle_result.data
        assert cycle.report_path is not None
        report_path = Path(cycle.report_path)
        assert report_path.exists()
        content = report_path.read_text()
        assert "Self-Optimization Report" in content
