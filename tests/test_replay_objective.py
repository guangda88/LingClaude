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

    def _bilateral_trace(self, events: list[tuple[str, bool]]) -> list[SessionTrace]:
        """F6: 双边事件 → 单 session 轨迹。events: [(tool, success)]"""
        ev = [(t, "" if ok else "err", ok) for t, ok in events]
        return [SessionTrace(session_id="s1", events=ev)]

    # ---- F6 churn 反力项 ----

    def test_churn_penalty_pushes_rl_up(self):
        # 纯成功长段(6)：rl 越小误拦越多 → 代价单调下降（推高 rl）
        tr = self._bilateral_trace([("bash", True)] * 6)
        scores = {
            rl: score_params(tr, {"consecutive_fail_limit": 3, "tool_repeat_limit": rl})
            for rl in (2, 4, 6)
        }
        assert scores[2] > scores[4] > scores[6]
        assert scores[6] == 0.0  # rl ≥ 段长 → 无误拦

    def test_churn_zero_without_bilateral_data(self):
        # 单边语料（二元组，success 视为 False）→ churn 项归零，退化 F1 语义
        tr = self._trace([("bash", ["ok-ish"] * 6)])
        assert score_params(
            tr, {"consecutive_fail_limit": 3, "tool_repeat_limit": 2}
        ) == score_params(tr, {"consecutive_fail_limit": 3})
        # 对照：纯失败段不走 churn 分支，走误差分支
        assert score_params(
            tr, {"consecutive_fail_limit": 3}
        ) == pytest.approx(0.1 * 2 + 1.0 + 0.6)

    def test_bilateral_mixed_runs_scored_by_side(self):
        # 成功段(rl=4 误拦) + 失败段(fl=3 干预) 并存，两侧代价都计入
        tr = self._bilateral_trace(
            [("bash", True)] * 6 + [("read", False)] * 3
        )
        c = score_params(tr, {"consecutive_fail_limit": 3, "tool_repeat_limit": 4})
        # churn: (6-4)*0.4 = 0.8；terminal: 0.1*2+1.0+0.6 = 1.8
        assert c == pytest.approx(0.8 + 1.8)

    def test_fl_rl_independent(self):
        # fl 只影响失败段、rl 只影响成功段
        tr = self._bilateral_trace(
            [("bash", True)] * 5 + [("read", False)] * 4
        )
        base = score_params(tr, {"consecutive_fail_limit": 2, "tool_repeat_limit": 3})
        move_fl = score_params(tr, {"consecutive_fail_limit": 5, "tool_repeat_limit": 3})
        move_rl = score_params(tr, {"consecutive_fail_limit": 2, "tool_repeat_limit": 6})
        assert move_fl > base and move_rl < base

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

    # ---- F6 双边表优先 ----

    def _add_tool_events(self, db: Path, rows: list[tuple[str, str, int]]) -> None:
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS tool_events (id INTEGER PRIMARY KEY,"
            " session_id TEXT NOT NULL DEFAULT '', tool_name TEXT NOT NULL,"
            " success INTEGER NOT NULL DEFAULT 1, occurred_at TEXT NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO tool_events (session_id, tool_name, success,"
            " occurred_at) VALUES (?, ?, ?, ?)",
            [(sid, tool, succ, f"2026-09-23T10:{i:02d}:00")
             for i, (sid, tool, succ) in enumerate(rows)],
        )
        conn.commit()
        conn.close()

    def test_bilateral_table_preferred(self, tmp_path):
        db = self._make_db(tmp_path)
        # 双边表：s1 有 2 成功 + 1 失败；error_log 里的旧 s1 失败行不计
        self._add_tool_events(db, [("s1", "bash", 1), ("s1", "bash", 1),
                                   ("s1", "read", 0)])
        traces = load_traces(db)
        by_sid = {t.session_id: t for t in traces}
        assert len(by_sid["s1"].events) == 3  # 双边表覆盖，非 error_log 的 2
        assert [e[2] for e in by_sid["s1"].events] == [True, True, False]

    def test_empty_bilateral_falls_back(self, tmp_path):
        db = self._make_db(tmp_path)
        self._add_tool_events(db, [])  # 表存在但空 → 回退 error_log
        traces = load_traces(db)
        by_sid = {t.session_id: t for t in traces}
        assert len(by_sid["s1"].events) == 2
        assert all(not e[2] for e in by_sid["s1"].events)  # 回退侧全 False

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
