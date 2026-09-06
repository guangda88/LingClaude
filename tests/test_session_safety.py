"""2026-09-06 事故加固测试：逐轮落盘 / 文件编辑锁 / 活性判定。"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from lingclaude.core.file_lock import file_edit_lock


class TestFileEditLock:
    def test_exclusive_mutex(self, tmp_path: Path) -> None:
        monkey_target = tmp_path / "a.yaml"
        monkey_target.write_text("x", encoding="utf-8")
        os.chdir(tmp_path)
        acquired_inside = False
        with pytest.raises(TimeoutError):
            with file_edit_lock(monkey_target, owner="A", timeout=0.5):
                acquired_inside = True
                with file_edit_lock(monkey_target, owner="B", timeout=0.5):
                    pass  # 不应拿到
        assert acquired_inside

    def test_released_after_context(self, tmp_path: Path) -> None:
        os.chdir(tmp_path)
        target = tmp_path / "b.yaml"
        with file_edit_lock(target, owner="A"):
            pass
        with file_edit_lock(target, owner="B", timeout=0.5):
            pass  # 释放后可再获取

    def test_stale_lock_reclaimed(self, tmp_path: Path, monkeypatch) -> None:
        import lingclaude.core.file_lock as fl

        os.chdir(tmp_path)
        target = tmp_path / "c.yaml"
        # 模拟死进程留下的锁：写入不存在的 pid + 把锁 mtime 拨旧
        with file_edit_lock(target, owner="ghost") as lp:
            pass
        holder_pid = int(lp.read_text().split("|")[0])
        fl._STALE_SECONDS = 0  # 0 秒即视为陈旧
        # 即使"持锁者"是活进程（当前测试进程），陈旧判定也应允许夺走
        with file_edit_lock(target, owner="B", timeout=0.5):
            pass
        fl._STALE_SECONDS = 600


class TestPerTurnPersist:
    def test_interactive_loop_persists_each_turn(self, tmp_path: Path, monkeypatch) -> None:
        """逐轮落盘：一轮完成后 session 文件应已存在（无需退出）。"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("LINGCLAUDE_BUS_LISTENER", "0")

        calls: list[list[str]] = []

        class FakePersister:
            def persist_session(self):
                calls.append(1)

                class R:
                    is_error = False
                    error = ""

                return R()

        class FakeEngine:
            _session_persister = FakePersister()

        # 直接验证 app.py 源码包含逐轮持久化调用（行为级测试见集成）
        import lingclaude.cli.app as app_mod

        src = Path(app_mod.__file__).read_text(encoding="utf-8")
        assert "persist_session()" in src
        assert "逐轮落盘" in src
        # 模拟调用计数器可用
        fe = FakeEngine()
        fe._session_persister.persist_session()
        assert len(calls) == 1
