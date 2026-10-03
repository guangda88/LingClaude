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
        # 2026-09-28 (外部审计 flake 收口): target 封闭到 tmp 空目录（同 L99-101
        # test_run_cycle_no_trigger 既有先例）。原 target="." 会扫真实仓库
        # （81k+ 行），高负载下 collect_metrics 阶段偶发越过 45s 超时红线——
        # 本用例只验证「用户触发 → 优化执行 → cycle 记录」链路，trigger 与
        # optimizer 均已 mock，target 内容不参与断言，空目录语义等价且稳定。
        daemon = OptimizationDaemon(target=str(tmp_path), state_dir=tmp_path)

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


class TestDaemonSessionRootGuard:
    """守卫：daemon 会话存储必须走全局根（~/.lingclaude/sessions），禁止 cwd 相对路径泄漏。

    回归目标：daemon.py 曾用 SessionManager(save_dir=P(".lingclaude/sessions"))，
    daemon 从哪个 cwd 启动就把快照写进哪个 <cwd>/.lingclaude/sessions/，
    跨 cwd 起 daemon 会把同项目快照分裂到多个目录（会话泄漏）。
    修复后必须走 _global_sessions_root()，且按 project_path 分目录。
    """

    @pytest.fixture
    def isolated_home(self, tmp_path, monkeypatch):
        """隔离 Path.home，防止测试向真实 ~/.lingclaude/sessions 写垃圾。"""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr("lingclaude.core.session.Path.home", classmethod(lambda cls: fake_home))
        return fake_home

    def test_session_mgr_uses_global_root_not_cwd(self, isolated_home, tmp_path):
        """构造 daemon 后，其 session_mgr 必须处于全局模式（_global_mode=True），
        save_dir 必须指向全局根，而非进程 cwd 下的相对路径。"""
        daemon = OptimizationDaemon(target="lingclaude", state_dir=tmp_path / "state")
        mgr = daemon._session_mgr
        # 全局模式标志：无参构造才会置 True，传 save_dir 则为 False
        assert mgr._global_mode is True, (
            "daemon session_mgr 未走全局根——疑似回归为显式 save_dir（cwd 相对泄漏）"
        )
        # save_dir 必须等于全局根（Path.home()/.lingclaude/sessions）
        assert mgr.save_dir == isolated_home / ".lingclaude" / "sessions"
        # 绝不等于 cwd 相对解析出的 .lingclaude/sessions
        assert mgr.save_dir != (Path.cwd() / ".lingclaude" / "sessions").resolve()

    def test_session_saved_under_project_dir(self, isolated_home, tmp_path):
        """全局根模式下，save() 后会话必须落到 save_dir/<project_dir>/ 子目录，
        验证 project_path 分目录隔离生效，而非全堆在根层。
        （create() 纯内存建对象不落盘，目录由 save()/_session_path 建出，故断言放在 save 后。）"""
        target_name = "lingclaude"
        daemon = OptimizationDaemon(target=target_name, state_dir=tmp_path / "state")
        mgr = daemon._session_mgr
        # save() 触发真实落盘（全局根 _session_path 会 mkdir 项目子目录）
        save_result = mgr.save(daemon._current_session)
        assert save_result.is_ok, f"save() 失败: {save_result}"
        project_dir = mgr.save_dir / target_name
        assert project_dir.exists(), (
            f"全局根模式下项目子目录 {project_dir} 未建出——project 分目录隔离失效"
        )
        # 当前会话 json 应落在项目子目录内
        session_files = list(project_dir.glob("*.json"))
        assert any(daemon._current_session.session_id in f.name for f in session_files), (
            "当前会话 json 未写入项目子目录"
        )


class TestCliAppSessionRootGuard:
    """守卫：cli/app.py 的 session/metrics 子命令必须锚定 ~ 而非 cwd。

    atomcode 2026-10-03 派单（全机「状态路径相对化」同族病 lc 侧实例）：
    app.py 曾直接 SessionManager(Path(config.session.save_dir))，配置默认
    ".lingclaude/sessions/" 是相对路径 → _global_mode=False → 按进程 cwd
    落盘。同一用户从不同目录跑 `lingclaude session list` / `lingclaude
    metrics stats` 会看到不同的会话清单/指标库，状态被 cwd 切裂。
    修复：resolve_configured_save_dir 相对路径锚定 ~。
    """

    @pytest.fixture
    def isolated_home(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(
            "lingclaude.core.session.Path.home",
            classmethod(lambda cls: fake_home),
        )
        return fake_home

    def test_resolve_relative_anchors_home_not_cwd(self, isolated_home, tmp_path, monkeypatch):
        from lingclaude.core.session import resolve_configured_save_dir

        monkeypatch.chdir(tmp_path / "elsewhere") if (tmp_path / "elsewhere").mkdir() else None
        out = resolve_configured_save_dir(".lingclaude/sessions/")
        assert out.is_absolute(), "解析结果必须是绝对路径"
        assert str(tmp_path / "elsewhere") not in str(out), "禁止锚定 cwd"
        assert out == isolated_home / ".lingclaude" / "sessions", "相对路径必须锚定 ~"

    def test_resolve_absolute_passthrough(self, tmp_path):
        from lingclaude.core.session import resolve_configured_save_dir

        absolute = tmp_path / "custom" / "sessions"
        assert resolve_configured_save_dir(str(absolute)) == absolute, "绝对路径必须原样保留"

    def test_resolve_expanduser(self, isolated_home, monkeypatch):
        from lingclaude.core.session import resolve_configured_save_dir

        monkeypatch.setenv("HOME", str(isolated_home))
        out = resolve_configured_save_dir("~/.lingclaude/sessions")
        assert out == isolated_home / ".lingclaude" / "sessions"
