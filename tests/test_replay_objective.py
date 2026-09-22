"""F1 回放目标函数 + behavior 优化闭环测试。

覆盖：
1. score_params 三段代价语义（终端容忍 / 干预摩擦 / 瞬态自愈）与 U 形内点。
2. load_traces：合成 SQLite fixture（真实 schema），按 session 聚合。
3. SynchronousOptimizer(goal=behavior) 端到端（db_path 注入隔离）。
4. _apply_behavior_params 写入器：report-only / 白名单 / 守卫拒绝路径。
5. 热加载契约：behavior_check 阈值函数读 policy_loader。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from lingclaude.self_optimizer.replay_objective import (
    ReplayObjective,
    SessionTrace,
    is_transient,
    load_traces,
    score_params,
)


# ---------- 1. 代价语义 ----------


class TestScoreParams:
    def _trace(self, runs: list[tuple[str, list[str]]]) -> list[SessionTrace]:
        """runs: [(tool, [error_msg, ...]), ...] → 单 session 轨迹"""
        events = [(t, e) for t, errs in runs for e in errs]
        return [SessionTrace(session_id="s1", events=events)]

    def test_terminal_fl1_blocks_early(self):
        # 终端长段(5)：fl=1 → 1次浪费+1次干预；fl=5 → 0.1*2+1*3+1干预
        tr = self._trace([("read", ["未找到匹配文本"] * 5)])
        c1 = score_params(tr, {"consecutive_fail_limit": 1})
        c5 = score_params(tr, {"consecutive_fail_limit": 5})
        assert c1 == pytest.approx(0.1 + 0.6)  # 容忍1(廉价)+干预
        assert c5 == pytest.approx(0.1 * 2 + 1.0 * 3 + 0.6)

    def test_transient_long_run_prefers_high_fl(self):
        # 瞬态长段(10)：fl 越大被拦越晚 → 代价单调下降
        tr = self._trace([("bash", ["Tool 'bash' timed out after 120s"] * 10)])
        costs = [
            score_params(tr, {"consecutive_fail_limit": fl})
            for fl in (1, 3, 5, 9)
        ]
        assert costs == sorted(costs, reverse=True)

    def test_mixed_runs_u_shape(self):
        # 海量单次终端段(摩擦压力推高 fl) + 少量长终端段(浪费压力压低 fl)
        # → U 形内点，且两端都不会是全局最优。
        traces: list[SessionTrace] = []
        for i in range(200):
            traces.append(
                SessionTrace(session_id=f"s{i}", events=[("read", "err")])
            )
        for i in range(10):
            traces.append(
                SessionTrace(
                    session_id=f"l{i}",
                    events=[("read", "err") for _ in range(9)],
                )
            )
        costs = {
            fl: score_params(traces, {"consecutive_fail_limit": fl})
            for fl in (1, 2, 3, 4, 5)
        }
        best = min(costs, key=costs.get)
        assert 2 <= best <= 4  # 内点，非边界

    def test_is_transient(self):
        assert is_transient("Tool 'bash' timed out after 120s")
        assert is_transient("rate limit 429")
        assert is_transient("Connection reset by peer")
        assert not is_transient("未找到匹配文本: foo.py")

    def test_param_clamp(self):
        tr = self._trace([("read", ["err"])])
        assert score_params(tr, {"consecutive_fail_limit": 0}) == score_params(
            tr, {"consecutive_fail_limit": 1}
        )


# ---------- 2. 真实 schema 聚合 ----------


class TestLoadTraces:
    def _make_db(self, tmp_path: Path) -> Path:
        db = tmp_path / "data_flywheel.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE error_log (id INTEGER PRIMARY KEY, pattern_type TEXT,"
            " file_path TEXT, error_message TEXT, tool_name TEXT,"
            " context TEXT, session_id TEXT, occurred_at TEXT)"
        )
        rows = [
            ("s1", "read", "err1", "2026-09-22T10:00:00"),
            ("s1", "read", "err2", "2026-09-22T10:01:00"),
            ("s2", "bash", "timed out", "2026-09-22T10:02:00"),
        ]
        conn.executemany(
            "INSERT INTO error_log (session_id, tool_name, error_message,"
            " occurred_at) VALUES (?,?,?,?)",
            rows,
        )
        conn.commit()
        conn.close()
        return db

    def test_grouping_and_order(self, tmp_path):
        traces = load_traces(self._make_db(tmp_path))
        assert len(traces) == 2
        by_sid = {t.session_id: t for t in traces}
        assert len(by_sid["s1"].events) == 2
        assert by_sid["s1"].events[0][1] == "err1"

    def test_missing_db_graceful(self, tmp_path):
        assert load_traces(tmp_path / "nope.db") == []

    def test_objective_empty_db_returns_zero(self, tmp_path):
        obj = ReplayObjective(db_path=tmp_path / "nope.db")
        assert obj.traces == []
        assert obj.evaluate({"consecutive_fail_limit": 3}) == 0.0


# ---------- 3. optimizer 端到端 ----------


class TestOptimizerBehaviorGoal:
    def test_end_to_end_synthetic(self, tmp_path):
        db = tmp_path / "fw.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE error_log (id INTEGER PRIMARY KEY, pattern_type TEXT,"
            " file_path TEXT, error_message TEXT, tool_name TEXT,"
            " context TEXT, session_id TEXT, occurred_at TEXT)"
        )
        rows = []
        # 30 个单次终端段（摩擦压力）+ 6 个长 8 段（浪费压力）→ 内点解
        for i in range(30):
            rows.append((f"a{i}", "read", "err", "2026-09-22T10:00:00"))
        for i in range(6):
            for k in range(8):
                rows.append((f"b{i}", "read", "err", "2026-09-22T11:00:00"))
        conn.executemany(
            "INSERT INTO error_log (session_id, tool_name, error_message,"
            " occurred_at) VALUES (?,?,?,?)",
            rows,
        )
        conn.commit()
        conn.close()

        from lingclaude.self_optimizer.optimizer import (
            OptimizationRequest,
            SynchronousOptimizer,
        )

        result = SynchronousOptimizer().optimize(
            OptimizationRequest(
                target=".",
                goal="behavior",
                params={},
                config={"max_experiments": 12, "db_path": str(db)},
            )
        )
        assert result.success
        assert result.best_params
        assert "consecutive_fail_limit" in result.best_params

    def test_search_space_has_behavior(self):
        from lingclaude.self_optimizer.optimizer import _build_search_space

        space = _build_search_space("behavior")
        assert space is not None


# ---------- 4. 写入器护栏 ----------


class TestApplyBehaviorParams:
    def _daemon(self, tmp_path):
        from lingclaude.self_optimizer.daemon import OptimizationDaemon

        return OptimizationDaemon(target=".", state_dir=tmp_path)

    def _policy_path(self, monkeypatch, tmp_path) -> Path:
        """policies_dir patch 到 tmp 副本（零真实文件污染）。"""
        import shutil

        policies = tmp_path / "policies"
        policies.mkdir(exist_ok=True)
        real = Path("lingclaude/core/policies/behavior_policy.yaml")
        shutil.copy(real, policies / "behavior_policy.yaml")
        monkeypatch.setattr(
            "lingclaude.core.policy_loader.policies_dir", lambda: policies
        )
        return policies / "behavior_policy.yaml"

    def test_report_only_by_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv("LINGCLAUDE_DAEMON_APPLY", raising=False)
        d = self._daemon(tmp_path)
        policy = self._policy_path(monkeypatch, tmp_path)
        before = policy.read_text(encoding="utf-8")
        d._apply_behavior_params({"consecutive_fail_limit": 2})
        assert policy.read_text(encoding="utf-8") == before  # 未动盘

    def test_invalid_key_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINGCLAUDE_DAEMON_APPLY", "1")
        d = self._daemon(tmp_path)
        policy = self._policy_path(monkeypatch, tmp_path)
        before = policy.read_text(encoding="utf-8")
        d._apply_behavior_params({"evil_key": 1})  # 白名单外 → 拒绝
        assert policy.read_text(encoding="utf-8") == before

    def test_guard_strict_blocks_write(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINGCLAUDE_DAEMON_APPLY", "1")
        d = self._daemon(tmp_path)
        policy = self._policy_path(monkeypatch, tmp_path)
        before = policy.read_text(encoding="utf-8")
        with patch(
            "lingclaude.core.permissions.PermissionContext.check_action",
            return_value=(False, "审批拒绝"),
        ):
            d._apply_behavior_params({"consecutive_fail_limit": 4})
        assert policy.read_text(encoding="utf-8") == before

    def test_apply_single_key_writes_yaml(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINGCLAUDE_DAEMON_APPLY", "1")
        d = self._daemon(tmp_path)
        policy = self._policy_path(monkeypatch, tmp_path)
        with patch(
            "lingclaude.core.permissions.PermissionContext.check_action",
            return_value=(True, ""),
        ):
            d._apply_behavior_params({"consecutive_fail_limit": 2})
        import yaml as _yaml

        data = _yaml.safe_load(policy.read_text(encoding="utf-8"))
        assert data["consecutive_fail_limit"] == 2
        assert "tool_repeat_limit" in data  # 其余键完整
        assert "edit_tools" in data


# ---------- 5. 热加载契约 ----------


class TestHotReloadContract:
    def test_behavior_check_reads_policy_loader(self):
        # behavior_check.py 以模块名 'behavior' 导入（属性遮蔽），
        # 直接读源码断言「阈值函数走 policy_loader 热加载」契约。
        src = Path("lingclaude/core/behavior_check.py").read_text(
            encoding="utf-8"
        )
        assert "policy_loader" in src
        assert "_repeat_limit_from_policy" in src
        assert "_fail_limit_from_policy" in src
        assert 'load("behavior_policy")' in src
