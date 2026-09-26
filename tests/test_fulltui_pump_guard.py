"""full-tui 假死根治回归测试（2026-09-26，19:20-19:45 整屏假死事故）。

覆盖四道新防线（全部纯逻辑，不依赖真实 TTY/全屏渲染）：
1. raw 看门狗：ICANON 置位 → setraw 恢复 + resync；健康 raw 态 → 零触碰
2. _reset_tty_now full-tui 守卫：活 app 脚下禁止 canonical 还原
3. _next_input 泵死亡改道：app 存活时复活泵（禁止降级直读双读者），
   复活 3 次仍死 → EOFError 结束会话
4. InputPump._run 提示符渲染异常不杀泵
"""
from __future__ import annotations

import sys
import threading  # noqa: F401 — 语义占位（Event 断言可读性）
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from lingclaude.cli import repl as repl_mod
from lingclaude.cli.full_tui import FullTuiSession
from lingclaude.cli.input_queue import InputPump, InputQueue
from lingclaude.cli.repl import _next_input, _reset_tty_now


@pytest.fixture()
def _pt_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lingclaude.cli.full_tui._HAS_PROMPT_TOOLKIT", True)


def _make_tui(tmp_path, app_alive: bool) -> FullTuiSession:
    s = FullTuiSession(history_file=str(tmp_path / "h"))
    s._ever_started = True  # noqa: SLF001 — 模拟已启动（不真开全屏）
    s._running = True  # noqa: SLF001
    s._app = MagicMock()  # noqa: SLF001
    if app_alive:
        s._app_thread = MagicMock()  # noqa: SLF001
        s._app_thread.is_alive.return_value = True
    else:
        s._app_thread = None  # noqa: SLF001
    return s


class _DeadPump:
    """模拟已死泵：dead=True，start 可注入行为。"""

    def __init__(self, on_start=None) -> None:
        self.dead = True
        self.death_reason = "测试死亡"
        self._last_beat = time.monotonic()
        self._stopped = True
        self.start_calls = 0
        self._on_start = on_start

    def is_alive(self) -> bool:
        return False

    def last_beat(self) -> float:
        return self._last_beat

    def stop(self) -> None:
        self._stopped = True

    def start(self) -> None:
        self.start_calls += 1
        if self._on_start is not None:
            self._on_start()


class _OnceInputQueue:
    """第一次 get 返回 None（触发复活轮），第二次返回真输入。"""

    def __init__(self) -> None:
        self._n = 0

    def get(self, timeout: float = 0.3):
        self._n += 1
        if self._n == 1:
            time.sleep(0.01)
            return None
        return "恢复后的输入"

    def pending(self) -> int:
        return 0


class _NeverInputQueue:
    def get(self, timeout: float = 0.3):
        time.sleep(timeout)
        return None

    def pending(self) -> int:
        return 0


class TestRawWatchdog:
    """防线 1：_check_tty_raw_drift 检测 canonical 并自愈。"""

    def _patch_termios(self, monkeypatch, lflag_with_icanon: bool):
        fake_termios = MagicMock()
        fake_termios.ICANON = 0o0000002
        lflag = 0o0000002 if lflag_with_icanon else 0
        fake_termios.tcgetattr.return_value = [0, 0, 0, lflag, b"", b"", b""]
        fake_tty = MagicMock()
        fake_stdin = MagicMock()
        fake_stdin.fileno.return_value = 0  # pytest 捕获 stdin 的 fileno 会抛异常
        monkeypatch.setitem(sys.modules, "termios", fake_termios)
        monkeypatch.setitem(sys.modules, "tty", fake_tty)
        monkeypatch.setattr(sys, "stdin", fake_stdin)
        return fake_termios, fake_tty

    def test_canonical_detected_and_restored(
        self, _pt_available, monkeypatch, tmp_path
    ) -> None:
        s = _make_tui(tmp_path, app_alive=True)
        fake_termios, fake_tty = self._patch_termios(monkeypatch, True)
        resync = MagicMock()
        monkeypatch.setattr(s, "resync", resync)
        s._check_tty_raw_drift()
        fake_termios.tcgetattr.assert_called_once()
        fake_tty.setraw.assert_called_once()
        resync.assert_called_once()

    def test_raw_healthy_no_touch(self, _pt_available, monkeypatch, tmp_path) -> None:
        s = _make_tui(tmp_path, app_alive=True)
        fake_termios, fake_tty = self._patch_termios(monkeypatch, False)
        s._check_tty_raw_drift()
        fake_tty.setraw.assert_not_called()

    def test_throttled(self, _pt_available, monkeypatch, tmp_path) -> None:
        s = _make_tui(tmp_path, app_alive=True)
        fake_termios, fake_tty = self._patch_termios(monkeypatch, True)
        s._raw_guard_last = time.monotonic()  # 刚检测过
        s._check_tty_raw_drift()
        fake_termios.tcgetattr.assert_not_called()


class TestResetTtyGuard:
    """防线 2：_reset_tty_now 在 full-tui 活 app 下拒绝执行。"""

    def test_full_tui_running_skips(self, _pt_available, monkeypatch, tmp_path) -> None:
        fake_termios = MagicMock()
        monkeypatch.setattr(repl_mod, "termios", fake_termios)
        s = _make_tui(tmp_path, app_alive=True)
        ctx = SimpleNamespace(session=s, saved_termios=[[0], [0], [0], [0]])
        _reset_tty_now(ctx)
        fake_termios.tcsetattr.assert_not_called()

    def test_full_tui_dead_app_skips(self, _pt_available, monkeypatch, tmp_path) -> None:
        fake_termios = MagicMock()
        monkeypatch.setattr(repl_mod, "termios", fake_termios)
        s = _make_tui(tmp_path, app_alive=False)
        ctx = SimpleNamespace(session=s, saved_termios=[[0], [0], [0], [0]])
        _reset_tty_now(ctx)
        fake_termios.tcsetattr.assert_not_called()

    def test_plain_session_still_resets(self, _pt_available, monkeypatch) -> None:
        fake_termios = MagicMock()
        monkeypatch.setattr(repl_mod, "termios", fake_termios)
        fake_stdin = MagicMock()
        fake_stdin.fileno.return_value = 0  # pytest 捕获 stdin 的 fileno 会抛异常
        monkeypatch.setattr(sys, "stdin", fake_stdin)
        saved = [[0], [0], [0], [0]]
        ctx = SimpleNamespace(session=SimpleNamespace(), saved_termios=saved)
        _reset_tty_now(ctx)
        fake_termios.tcsetattr.assert_called_once()
        assert fake_termios.tcsetattr.call_args.args[1] == fake_termios.TCSADRAIN


class TestNextInputFullTuiRevive:
    """防线 3：full-tui 泵死亡 → 复活而非降级直读。"""

    def _ctx(self, pump, tui, queue) -> SimpleNamespace:
        return SimpleNamespace(
            input_pump=pump,
            input_queue=queue,
            session=tui,
            pump_mode=True,
            fallback_read=False,
        )

    def test_revive_when_app_alive(self, _pt_available, tmp_path) -> None:
        s = _make_tui(tmp_path, app_alive=True)
        pump = _DeadPump()
        ctx = self._ctx(pump, s, _OnceInputQueue())
        assert _next_input(ctx) == "恢复后的输入"
        assert pump.start_calls == 1
        assert pump.dead is False
        assert ctx.fallback_read is False

    def test_give_up_after_three_revives(self, _pt_available, tmp_path) -> None:
        s = _make_tui(tmp_path, app_alive=True)
        # 每次 start 后立即再死 → 复活循环直至耗尽 3 次
        pump = _DeadPump(on_start=None)

        def _restart_then_die() -> None:
            pump.dead = False  # 模拟 start 成功
            pump.dead = True  # 随即再死

        pump._on_start = _restart_then_die
        ctx = self._ctx(pump, s, _NeverInputQueue())
        with pytest.raises(EOFError):
            _next_input(ctx)
        assert pump.start_calls == 3

    def test_fallthrough_when_app_dead(self, _pt_available, monkeypatch, tmp_path) -> None:
        # app 已死 → 不复活，走原有降级路径（_read_input）；此处仅验证
        # 未触发复活（start 未被调用），降级细节由 test_cli_stall_escape 覆盖。
        s = _make_tui(tmp_path, app_alive=False)
        pump = _DeadPump()
        ctx = self._ctx(pump, s, _NeverInputQueue())
        monkeypatch.setattr(
            "lingclaude.cli.repl._read_input",
            lambda c: (_ for _ in ()).throw(KeyboardInterrupt),
        )
        with pytest.raises(KeyboardInterrupt):
            _next_input(ctx)
        assert pump.start_calls == 0


class TestPumpPromptTextGuard:
    """防线 4：_prompt_text 渲染异常不杀泵、不丢输入。"""

    def test_prompt_text_exception_falls_back(self, capsys) -> None:
        class _Boom:
            def __call__(self) -> str:
                raise RuntimeError("渲染爆炸")

        class _Session:
            def prompt(self, msg: str) -> str:
                assert msg == ""  # 降级空提示符
                pump._stop.set()
                return "用户输入"

        pump = InputPump(_Session(), InputQueue(), prompt_text=_Boom())
        pump._run()  # 同步执行一轮
        out = capsys.readouterr().err
        assert "提示符渲染失败" in out
        assert pump.dead is False
        assert pump._q.get(timeout=0.5) == "用户输入"
