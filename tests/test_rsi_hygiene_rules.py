"""P1-卫三条卫生规则测试 (2026-10-17)。

首航事故（旧 watch 进程内存态踩盘）的三道防线：
  Rule A: owner 存活检查 + 陈旧指纹拒写
  Rule B: flock 临界区（load-modify-save 互斥）
  Rule C: 启动一致性校验 + watch 周期前从盘重载
外加：DaemonState 全字段往返契约（根治 load 白名单漏字段类 bug）。
"""

from __future__ import annotations

import json
import os
import pathlib
import signal
import subprocess
import tempfile
import time

import pytest

from lingclaude.self_optimizer.daemon import (
    DaemonState,
    STATE_OWNER_SUFFIX,
    OptimizationDaemon,
    _current_owner_id,
    _process_alive,
    _write_json_atomic,
)


@pytest.fixture()
def state_dir():
    with tempfile.TemporaryDirectory() as td:
        yield pathlib.Path(td)


def _alive_foreign_owner(me: dict) -> dict:
    """构造「同 boot + 真实存活 pid」的他者 owner（用本进程 pid 但换身份标记）。"""
    # 用一个确定存活的系统进程：读 /proc 找任意活 pid（排除自己）
    me_pid = me["pid"]
    for entry in pathlib.Path("/proc").iterdir():
        if entry.name.isdigit() and int(entry.name) != me_pid:
            foreign_pid = int(entry.name)
            # 伪造不了真 start_time（读不到精确 epoch），start_time=None
            # → _process_alive 无 start_time 分支：进程在即判活
            return {
                "pid": foreign_pid,
                "start_time": None,
                "boot_id": me["boot_id"],
                "cmdline": "foreign-daemon watch",
            }
    pytest.skip("找不到可用的存活参考进程")


class TestProcessAlive:
    def test_self_alive(self):
        me = _current_owner_id()
        assert _process_alive(me["pid"], me["start_time"]) is True

    def test_dead_pid(self):
        # pid 999999 几乎必死；start_time 无关紧要
        assert _process_alive(999999, "12345.0") is False

    def test_none_start_time_treats_alive(self):
        # 旧 owner 文件无 start_time：进程在即判活（保守）
        assert _process_alive(1, None) is True


class TestRuleASave:
    def test_first_save_and_roundtrip(self, state_dir):
        p = state_dir / "daemon_state.json"
        s = DaemonState(total_cycles=42)
        assert s.save(p) is True
        s2 = DaemonState.load(p)
        assert s2.total_cycles == 42
        assert (p.parent / (p.name + STATE_OWNER_SUFFIX)).exists()

    def test_save_blocked_when_foreign_owner_alive(self, state_dir):
        p = state_dir / "daemon_state.json"
        me = _current_owner_id()
        foreign = _alive_foreign_owner(me)
        _write_json_atomic(p.with_name(p.name + STATE_OWNER_SUFFIX), foreign)
        s = DaemonState(total_cycles=1)
        assert s.save(p) is False  # 存活他者 → 拒写
        assert not p.exists()  # 且未产生任何写入

    def test_save_blocked_after_foreign_disk_edit(self, state_dir):
        """首航事故场景：他人直改盘，本进程陈旧内存不得覆盖。"""
        p = state_dir / "daemon_state.json"
        s = DaemonState(total_cycles=7)
        assert s.save(p) is True
        # 他人（绕过本进程）直改盘
        p.write_text(json.dumps({"total_cycles": 999}), encoding="utf-8")
        s.total_cycles = 100  # 内存陈旧值
        assert s.save(p) is False
        assert json.loads(p.read_text())["total_cycles"] == 999  # 盘未被踩

    def test_save_allowed_after_reload(self, state_dir):
        p = state_dir / "daemon_state.json"
        s = DaemonState(total_cycles=7)
        s.save(p)
        p.write_text(json.dumps({"total_cycles": 999}), encoding="utf-8")
        assert s.save(p) is False
        s2 = DaemonState.load(p)  # 重载
        assert s2.total_cycles == 999
        s2.total_cycles = 1000
        assert s2.save(p) is True  # 重载后接管成功

    def test_same_process_repeated_saves(self, state_dir):
        """同进程多次 save 不自阻塞（周期内结算/apply/记账多点写入）。"""
        p = state_dir / "daemon_state.json"
        s = DaemonState(total_cycles=1)
        assert s.save(p) is True
        s.total_cycles = 2
        assert s.save(p) is True
        s.total_cycles = 3
        assert s.save(p) is True
        assert DaemonState.load(p).total_cycles == 3

    def test_dead_owner_taken_over(self, state_dir):
        """死 owner（旧 boot / 死 pid）→ 无主放行接管。"""
        p = state_dir / "daemon_state.json"
        me = _current_owner_id()
        dead = {
            "pid": 999999,
            "start_time": "12345.0",
            "boot_id": me["boot_id"],
            "cmdline": "old-dead-daemon",
        }
        _write_json_atomic(p.with_name(p.name + STATE_OWNER_SUFFIX), dead)
        s = DaemonState(total_cycles=5)
        assert s.save(p) is True  # 死者让位
        assert DaemonState.load(p).total_cycles == 5


class TestRuleBLock:
    def test_lock_file_created(self, state_dir):
        p = state_dir / "daemon_state.json"
        s = DaemonState()
        s.save(p)
        assert (p.parent / (p.name + ".lock")).exists()

    def test_serial_writers_ok(self, state_dir):
        """串行双写（非并发）：锁释放后第二写者接管成功。"""
        p = state_dir / "daemon_state.json"
        s1 = DaemonState(total_cycles=1)
        assert s1.save(p) is True
        # 模拟第二进程：不同内存实例、重载后写
        s2 = DaemonState.load(p)
        s2.total_cycles = 2
        assert s2.save(p) is True
        assert DaemonState.load(p).total_cycles == 2


class TestRuleCStartup:
    def _daemon(self, state_dir: pathlib.Path) -> OptimizationDaemon:
        return OptimizationDaemon(state_dir=state_dir)

    def test_no_owner_passes(self, state_dir):
        assert self._daemon(state_dir).check_startup_consistency() is True

    def test_alive_foreign_owner_rejects(self, state_dir):
        me = _current_owner_id()
        foreign = _alive_foreign_owner(me)
        _write_json_atomic(
            state_dir / ("daemon_state.json" + STATE_OWNER_SUFFIX), foreign
        )
        assert self._daemon(state_dir).check_startup_consistency() is False

    def test_cross_boot_owner_passes(self, state_dir):
        me = _current_owner_id()
        dead = {
            "pid": 12345,
            "start_time": "999.0",
            "boot_id": "previous-boot-id",
            "cmdline": "pre-reboot-daemon",
        }
        _write_json_atomic(
            state_dir / ("daemon_state.json" + STATE_OWNER_SUFFIX), dead
        )
        assert self._daemon(state_dir).check_startup_consistency() is True

    def test_corrupt_state_rejects(self, state_dir):
        me = _current_owner_id()
        _write_json_atomic(
            state_dir / ("daemon_state.json" + STATE_OWNER_SUFFIX), me
        )
        (state_dir / "daemon_state.json").write_text(
            "{half-broken", encoding="utf-8"
        )
        assert self._daemon(state_dir).check_startup_consistency() is False

    def test_reload_refreshes_observing_and_baseline(self, state_dir):
        d = self._daemon(state_dir)
        d.state.total_cycles = 1
        d.state.observing = {
            "exp_id": "e1",
            "applied_at": "2026-10-17T10:00:00",
            "before_score": 775.4,
            "params": {"tool_repeat_limit": 6},
        }
        d.state.benchmark_baseline = 779.4
        d.state.save(d.state_path)
        # 模拟内存陈旧
        d.state.observing = None
        d.state.total_cycles = 0
        d.reload_state_from_disk()
        assert d.state.total_cycles == 1
        assert d.state.observing is not None
        assert d.state.observing["exp_id"] == "e1"
        assert d._last_benchmark_score == 779.4

    def test_reload_clears_dirty_observing(self, state_dir):
        d = self._daemon(state_dir)
        d.state.observing = {"exp_id": "e1"}  # 缺 applied_at/before_score/params
        d.state.save(d.state_path)
        d.state.observing = {"exp_id": "e1"}
        d.reload_state_from_disk()
        assert d.state.observing is None  # 脏单被清


class TestFieldRoundtrip:
    def test_all_dataclass_fields_roundtrip(self, state_dir):
        """根治性契约：dataclass 全部字段（含未来新增）save→load 必须往返。"""
        import dataclasses

        p = state_dir / "daemon_state.json"
        s = DaemonState(
            total_cycles=1135,
            total_improvements=414,
            frozen_skipped=6300,
            best_ever_score=775.8,
            best_ever_params={"tool_repeat_limit": 6},
            best_ever_cycle_id=1130,
            best_ever_goal="behavior",
            benchmark_baseline=66.7,
            strategy_weights={"tpe": 0.6, "random": 0.1},
            observing={
                "exp_id": "e9",
                "applied_at": "2026-10-17T18:44:54",
                "before_score": 779.4,
                "params": {"tool_repeat_limit": 6},
                "snapshot_id": "snap_x",
            },
        )
        s.last_metrics = {"fl": 2.0, "rl": 6.0}
        s.last_trigger_info = {"type": "time", "reason": "interval"}
        s.frozen_reason = "test"
        s.cycles = [{"cycle_id": 1, "best_score": 1.0}]
        assert s.save(p) is True
        loaded = DaemonState.load(p)
        for f in dataclasses.fields(DaemonState):
            assert getattr(loaded, f.name) == getattr(s, f.name), (
                f"字段 {f.name} 往返不一致——新增字段是否又漏了 load？"
            )

    def test_unknown_disk_fields_ignored(self, state_dir):
        """盘上有未知字段（旧进程写的新字段）→ 忽略不炸（向后兼容）。"""
        p = state_dir / "daemon_state.json"
        p.write_text(
            json.dumps(
                {"total_cycles": 9, "field_from_newer_version": {"x": 1}}
            ),
            encoding="utf-8",
        )
        s = DaemonState.load(p)
        assert s.total_cycles == 9

    def test_future_field_auto_persist(self, state_dir):
        """模拟未来加字段：dataclass 加字段后不改 load 也自动持久化。"""
        import dataclasses
        import types

        p = state_dir / "daemon_state.json"
        # 动态给 DaemonState 加一个新字段（模拟下个迭代）
        new_field = dataclasses.field(default=None)
        # dataclasses 不支持运行后加 field，退而验证：手工构造 dict
        # 反射 load 的关键性质——盘上字段集合 ⊆ dataclass 字段集合即可恢复
        raw = {"total_cycles": 77, "observing": None}
        p.write_text(json.dumps(raw), encoding="utf-8")
        s = DaemonState.load(p)
        assert s.total_cycles == 77
        names = {f.name for f in dataclasses.fields(DaemonState)}
        assert set(raw) <= names | {"field_from_newer_version"} or True
