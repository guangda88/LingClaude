"""R9-2 kill_beacon 单测：快照内容 / 黑匣子生命周期 / 继承发现 / 信号快刷安全面。"""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from lingclaude.engine import kill_beacon as kb
from lingclaude.engine.kill_beacon import (
    KillBeacon,
    detect_dead_predecessor,
    format_predecessor_hint,
)


class _FakeTodo:
    def __init__(self, status: str) -> None:
        self.status = status

    def to_dict(self) -> dict:
        return {"id": "t1", "status": self.status, "content": "x"}


class _FakeEngine:
    session_id = "s" + "a" * 30
    # [user, assistant] 成对镜像（engine 双写约定）：[-2] = 最近用户输入
    _messages = ["帮我查X", "查到了：X=1", "把 W1 装上", "已完成安装"]
    _turn_count = 42

    def __init__(self) -> None:
        self._todo_store = type("S", (), {"list": staticmethod(lambda: [_FakeTodo("pending"), _FakeTodo("completed")])})()

    def get_stats(self) -> dict:
        return {"usage": {"input_tokens": 123456}}


@pytest.fixture()
def beacon_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "beacons"
    monkeypatch.setattr(kb, "_beacons_root", lambda: root)
    monkeypatch.setattr(Path, "home", Path.home)  # no-op，明确意图
    return root


@pytest.fixture()
def in_tmp_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    d = tmp_path / "proj"
    d.mkdir()
    monkeypatch.chdir(d)
    return d


def test_write_beacon_contains_task_state(beacon_root: Path, in_tmp_cwd: Path) -> None:
    b = KillBeacon(_FakeEngine(), interval=1.0)
    b.start()  # startup 快照同步落盘（start 内同步写）
    time.sleep(0.05)
    b._stop.set()  # 停周期线程，防测试期间被 periodic 覆盖
    assert b.path.exists()
    data = json.loads(b.path.read_text(encoding="utf-8"))
    assert data["session_id"] == _FakeEngine.session_id
    assert data["pid"] == os.getpid()
    assert data["turns"] == 42
    assert data["reason"] in ("startup", "periodic")  # 最新一份
    assert sum(1 for t in data["todos"] if t["status"] != "completed") == 1
    assert data["input_tokens"] == 123456
    b.stop(clean=True)


def test_clean_stop_removes_blackbox(beacon_root: Path, in_tmp_cwd: Path) -> None:
    b = KillBeacon(_FakeEngine(), interval=1.0)
    b.start()
    assert b.path.exists()
    b.stop(clean=True)
    assert not b.path.exists()  # 正常退出=黑匣子清除


def test_unclean_stop_keeps_blackbox(beacon_root: Path, in_tmp_cwd: Path) -> None:
    b = KillBeacon(_FakeEngine(), interval=1.0)
    b.write_beacon("periodic")
    b.stop(clean=False)
    assert b.path.exists()  # 异常路径不清除 → 继承发现可用


def test_detect_dead_predecessor_roundtrip(beacon_root: Path, in_tmp_cwd: Path) -> None:
    import subprocess

    victim = KillBeacon(_FakeEngine(), interval=1.0)
    victim.write_beacon("periodic")
    victim.stop(clean=False)  # 模拟被杀：留下黑匣子
    # beacon 里的 pid 是本进程（活着）会被正确排除——改写成「真死 pid」
    # （子进程 wait 后不复用，模拟前任已死）再验发现逻辑。
    p = subprocess.Popen(["true"])
    p.wait()
    assert p.poll() is not None
    data = json.loads(victim.path.read_text(encoding="utf-8"))
    data["pid"] = p.pid
    victim.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    found = detect_dead_predecessor(str(in_tmp_cwd), exclude_pid=os.getpid())
    assert found is not None
    assert found["session_id"] == _FakeEngine.session_id
    hint = format_predecessor_hint(found)
    assert "继承提示" in hint and "--continue" in hint


def test_detect_ignores_live_pid_and_stale(beacon_root: Path, in_tmp_cwd: Path) -> None:
    # 活进程不算前任
    b = KillBeacon(_FakeEngine(), interval=1.0)
    b.write_beacon("periodic")
    assert detect_dead_predecessor(str(in_tmp_cwd), exclude_pid=os.getpid()) is None
    b.stop(clean=False)
    # 陈旧 beacon 不提示
    old = time.time() - (kb.STALE_HOURS + 5) * 3600
    data = json.loads(b.path.read_text(encoding="utf-8"))
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(old))
    b.path.write_text(json.dumps(data), encoding="utf-8")
    assert detect_dead_predecessor(str(in_tmp_cwd), exclude_pid=os.getpid()) is None


def test_signal_flush_writes_payload(beacon_root: Path, in_tmp_cwd: Path) -> None:
    b = KillBeacon(_FakeEngine(), interval=1.0)
    b.write_beacon("periodic")
    b.prepare_signal_flush()
    assert b._sig_fd is not None
    # 模拟信号语境：直接调 handler 专用方法（只做 ftruncate+pwrite）
    b.signal_flush()
    assert b.path.exists()
    assert json.loads(b.path.read_text(encoding="utf-8"))["reason"] == "periodic"
    b.stop(clean=True)


def test_periodic_loop_updates(beacon_root: Path, in_tmp_cwd: Path) -> None:
    b = KillBeacon(_FakeEngine(), interval=1.0)  # interval 有 1.0s 下限钳制（防呆）
    b.start()
    deadline = time.monotonic() + 5.0
    reason = "startup"
    while time.monotonic() < deadline:
        time.sleep(0.3)
        reason = json.loads(b.path.read_text(encoding="utf-8"))["reason"]
        if reason == "periodic":  # 第一次周期覆盖即达标（≤2s 预期）
            break
    data = json.loads(b.path.read_text(encoding="utf-8"))
    assert reason == "periodic", f"周期写未在 5s 内发生: {reason}"
    assert data["last_user_input"] == "把 W1 装上"  # 倒数第 2 条=用户输入（验采集逻辑）
    b.stop(clean=True)


def test_signal_handler_chain_preserved(beacon_root: Path, in_tmp_cwd: Path) -> None:
    """install 后原 SIGTERM handler 被调用（链式）；恢复原状防污染其他测试。"""
    calls: list[int] = []
    prev = signal.getsignal(signal.SIGTERM)
    try:
        signal.signal(signal.SIGTERM, lambda s, f: calls.append(s))
        eng = _FakeEngine()
        b = kb.install_kill_beacon(eng, project_path=str(in_tmp_cwd))
        assert b is not None
        assert getattr(eng, "_kill_beacon", None) is b  # 绑定给 repl 退出路径
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.2)
        assert calls, "原 handler 未被链式调用"
    finally:
        if b is not None:
            b.stop(clean=True)
        signal.signal(signal.SIGTERM, prev)


def test_env_disable_bypasses(beacon_root: Path, in_tmp_cwd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINGCLAUDE_KILL_BEACON", "1")
    assert kb.install_kill_beacon(_FakeEngine(), project_path=str(in_tmp_cwd)) is None


def test_engine_without_session_id_rejected(beacon_root: Path, in_tmp_cwd: Path) -> None:
    class _NoSid:
        pass

    assert kb.install_kill_beacon(_NoSid(), project_path=str(in_tmp_cwd)) is None


def test_write_beacon_rejects_empty_session_id(beacon_root: Path, in_tmp_cwd: Path) -> None:
    # E2E 教训 2026-10-10（unknown.json）：mock engine 无 session_id 时
    # 经 write_beacon 直调也会落盘，产生无身份垃圾 beacon，被继承检测误报。
    class _NoSidEngine:
        session_id = ""  # 空 = 无身份
        _messages: list[str] = []

        def get_stats(self) -> dict:
            return {}

    b = kb.KillBeacon(_NoSidEngine(), project_path=str(in_tmp_cwd))
    assert b.write_beacon(reason="handover") is False
    assert not list(beacon_root.rglob("*.json")), "空 session_id 不得产生任何 beacon 文件"


def test_corrupt_beacon_file_tolerated(beacon_root: Path, in_tmp_cwd: Path) -> None:
    root = beacon_root / kb._project_key(str(in_tmp_cwd))
    root.mkdir(parents=True)
    (root / "garbage.json").write_text("not json", encoding="utf-8")
    assert detect_dead_predecessor(str(in_tmp_cwd), exclude_pid=os.getpid()) is None
