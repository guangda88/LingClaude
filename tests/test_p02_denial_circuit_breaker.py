"""P0.2 (V3 §五) — 修活 5b denial 熔断的行为测试。

E1 债（V3 §三）：execute_tool._blocks 里对 _session_runtime/_loop_detector
的双重 hasattr 在 CodingRuntime 上永远为 False（两个属性从未初始化），
observe_denial 熔断是死代码；且 _denial_abort_log 原写入点全库无消费者。

本文件证明修复后熔断真实触发：
1. __init__ 真的建了两个依赖（接线存在性）
2. 同 rule_id 连续 2 次 denial → 熔断信号挂到工具结果上（模型可见）
3. 熔断信号消费即清零；放行后不再携带旧信号（不泄漏）
4. denial 经 SessionRuntime 真的进了 DataFlywheel（R2 回路不悬空）
5. 未被拦的工具不计数（strict 模式下 read 正常放行）

运行: pytest tests/test_p02_denial_circuit_breaker.py -q
"""
from __future__ import annotations

import sqlite3

import pytest

from lingclaude.core import permissions as _perm
from lingclaude.core.config import lingclaudeConfig
from lingclaude.core.data_flywheel import DataFlywheel
from lingclaude.core.permissions import (
    record_permission_decision,
    reset_permission_stores,
    set_permission_mode,
)
from lingclaude.engine.coding import CodingRuntime


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """隔离三件事：approvals.json 落盘、permission mode 全局态、飞轮 DB。"""
    # _PERSIST_PATH 是 import 时固化的模块常量（指向真实库 data/），
    # 必须 patch 模块属性才能拦住 set_permission_mode 的落盘
    monkeypatch.setattr(_perm, "_PERSIST_PATH", tmp_path / "approvals.json")
    reset_permission_stores()
    set_permission_mode("ask")
    # DataFlywheel 默认路径绑定源码树位置（__file__ 相对），chdir 隔离不了，
    # 强制所有实例落 tmp（含 SessionRuntime.log_denial 的函数内 import）。
    _orig_init = DataFlywheel.__init__

    def _patched_init(self, db_path=None):
        _orig_init(self, db_path=str(tmp_path / "flywheel.db"))

    monkeypatch.setattr(DataFlywheel, "__init__", _patched_init)
    yield
    reset_permission_stores()
    set_permission_mode("ask")


@pytest.fixture()
def runtime(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return CodingRuntime(config=lingclaudeConfig())


class TestP02DenialCircuitBreaker:
    def test_init_wires_detector_and_session_runtime(self, runtime):
        """接线存在性：E1 根因是两个属性从未初始化，双重 hasattr 永假。"""
        assert hasattr(runtime, "_loop_detector")
        assert hasattr(runtime, "_session_runtime")
        assert runtime._denial_abort_log is None

    def test_second_denial_trips_breaker_and_is_model_visible(self, runtime):
        """红→绿主验收：同 rule_id 连续 2 次 denial → 熔断信号模型可见。"""
        set_permission_mode("strict")
        record_permission_decision("default", "bash", "deny")

        r1 = runtime.execute_tool("bash", command="echo hi")
        r2 = runtime.execute_tool("bash", command="echo hi")

        assert "blocked by permissions" in r1.get("error", "")
        assert "denial_circuit_breaker" not in r1
        assert "denial_circuit_breaker" in r2, (
            "第 2 次同 rule_id denial 必须触发熔断并挂到结果上"
        )
        assert "denial_abort" in r2["denial_circuit_breaker"]
        assert "rule_id=session.deny_tools.exact" in r2["denial_circuit_breaker"]
        # 原死代码写入点不应再有值（信号改写实例属性）
        assert not hasattr(runtime._loop_detector, "_denial_abort_log")

    def test_breaker_signal_not_leaked_after_allow(self, runtime):
        """熔断信号消费即清零；放行后的正常调用不携带旧信号。"""
        set_permission_mode("strict")
        record_permission_decision("default", "bash", "deny")
        runtime.execute_tool("bash", command="echo a")
        tripped = runtime.execute_tool("bash", command="echo b")
        assert "denial_circuit_breaker" in tripped

        # 放行后：结果干净，无熔断残留
        record_permission_decision("default", "bash", "always_allow")
        ok = runtime.execute_tool("bash", command="echo ok")
        assert "denial_circuit_breaker" not in ok
        assert runtime._denial_abort_log is None

    def test_denial_reaches_flywheel(self, runtime):
        """R2 回路：denial 经 SessionRuntime 真的落进 DataFlywheel。"""
        set_permission_mode("strict")
        record_permission_decision("default", "bash", "deny")
        runtime.execute_tool("bash", command="echo hi")
        runtime.execute_tool("bash", command="echo hi")

        fw = DataFlywheel()  # __init__ 已被 fixture 指到 tmp
        conn = sqlite3.connect(str(fw.db_path))
        try:
            rows = conn.execute(
                "SELECT pattern_type, tool_name, session_id FROM error_log "
                "WHERE pattern_type LIKE 'permission%'"
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) >= 2, "两次 denial 都应入飞轮"
        assert all(rt[0].startswith("permission_") for rt in rows)
        assert all(rt[1] == "bash" for rt in rows)
        assert all(rt[2] == runtime.session_id for rt in rows)

    def test_allowed_tool_does_not_count(self, runtime):
        """未被拦的工具不参与 denial 计数。"""
        set_permission_mode("strict")
        res = runtime.execute_tool("read", path="no_such_file_xyz.txt")
        assert "denial_circuit_breaker" not in res
        assert runtime._loop_detector._denial_streak == {}
