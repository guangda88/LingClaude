"""_next_input 失活逃生门测试 — pump 病态卡死（prompt 无限阻塞）时
主循环不再无限空转，降级为阻塞直读（2026-09-12 事故复现与修复验证）。

验证：
- 心跳停滞 + stdin 可读（用户打字但 pump 未消费）→ 两轮确认后置 dead
- 心跳停滞 + stdin 不可读（正常空闲等输入）→ 不误判
- 降级后 _next_input 走 _read_input 阻塞直读（Ctrl+D 恢复可用）
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from lingclaude.cli.repl import _maybe_stall_escape, _next_input


class _Deadline:
    """简单超时守卫，防止测试卡死。"""

    def __init__(self, seconds: float = 5.0) -> None:
        self._t = time.monotonic() + seconds

    def expired(self) -> bool:
        return time.monotonic() > self._t


class _BlockingInputQueue:
    """模拟 InputQueue：get 阻塞（等不到输入），pending 恒 0。"""

    def __init__(self) -> None:
        self._stop = threading.Event()

    def get(self, timeout: float = 0.3) -> None:
        self._stop.wait(timeout=timeout)
        return None

    def pending(self) -> int:
        return 0


class _StuckPump:
    """模拟病态 pump：is_alive() 恒 True（线程活着），但心跳恒停滞（卡死）。"""

    def __init__(self) -> None:
        self.dead = False
        self.death_reason = ""
        self._last_beat = time.monotonic() - 30.0  # 心跳 30 秒前停滞
        self._stopped = False

    def is_alive(self) -> bool:
        return not self._stopped

    def last_beat(self) -> float:
        return self._last_beat

    def stop(self) -> None:
        """模拟 stop()：唤醒/join 成功后线程退出。"""
        self._stopped = True


class _StuckSession:
    """模拟卡死 session：interrupt_event() 可被 set。"""

    def __init__(self) -> None:
        self._interrupt = threading.Event()

    def interrupt_event(self) -> threading.Event:
        return self._interrupt


def _make_ctx(readable: bool) -> SimpleNamespace:
    return SimpleNamespace(
        input_pump=_StuckPump(),
        input_queue=_BlockingInputQueue(),
        session=_StuckSession(),
    )


@pytest.fixture
def patch_readable():
    """控制 _stdin_readable 返回值。"""
    with patch("lingclaude.cli.repl._stdin_readable", return_value=True) as m:
        yield m


class TestStallEscape:
    def test_heartbeat_stagnant_readable_marks_dead_after_confirm(self, patch_readable) -> None:  # noqa: ANN001
        """核心场景：用户打字（stdin 可读）但 pump 卡死未消费 → 两轮确认后 dead。"""
        ctx = _make_ctx(readable=True)
        # 第一轮：进入疑区，记录游标（返回 >0 的时间戳）
        t1 = _maybe_stall_escape(ctx, idle_loops=0, last_check_t=-1.0)
        assert t1 > 0
        assert not ctx.input_pump.dead  # 未确认，不置死
        # 第二轮（间隔 >1s）：确认失活 → dead + 逃生
        time.sleep(1.05)
        t2 = _maybe_stall_escape(ctx, idle_loops=1, last_check_t=t1)
        assert t2 == 0  # 逃生信号
        assert ctx.input_pump.dead
        assert "失活" in ctx.input_pump.death_reason
        # 逃生同时停掉 pump 线程（避免双读者）
        assert ctx.input_pump._stopped  # noqa: SLF001

    def test_heartbeat_stagnant_unreadable_not_marked(self) -> None:
        """正常空闲：stdin 不可读（无输入）→ 心跳停滞属正常，绝不误判。"""
        with patch("lingclaude.cli.repl._stdin_readable", return_value=False):
            ctx = _make_ctx(readable=False)
            for _ in range(5):
                t = _maybe_stall_escape(ctx, idle_loops=5, last_check_t=-1.0)
                assert t < 0  # 未进入疑区
                assert not ctx.input_pump.dead

    def test_fresh_beat_not_triggered(self) -> None:
        """心跳新鲜（pump 正常轮转）→ 即使 stdin 可读也不判死。"""
        ctx = _make_ctx(readable=True)
        ctx.input_pump._last_beat = time.monotonic()  # 心跳刚刚打过
        with patch("lingclaude.cli.repl._stdin_readable", return_value=True):
            for _ in range(5):
                t = _maybe_stall_escape(ctx, idle_loops=5, last_check_t=-1.0)
                assert t < 0
                assert not ctx.input_pump.dead

    def test_interrupt_event_set_on_escape(self, patch_readable) -> None:  # noqa: ANN001
        """逃生时 set interrupt_event（尝试唤醒 prompt_toolkit 阻塞读）。"""
        ctx = _make_ctx(readable=True)
        t1 = _maybe_stall_escape(ctx, idle_loops=0, last_check_t=-1.0)
        time.sleep(1.05)
        _maybe_stall_escape(ctx, idle_loops=1, last_check_t=t1)
        assert ctx.session.interrupt_event().is_set()


class TestNextInputFallback:
    def test_dead_pump_falls_back_to_blocking_read(self) -> None:
        """pump 置 dead 后 _next_input 永久降级为阻塞直读（_read_input）。"""
        ctx = _make_ctx(readable=True)
        ctx.pump_mode = True
        ctx.input_pump.dead = True
        # 降级后走 _read_input（session.prompt 返回 "你好"）
        ctx.session = MagicMock()
        ctx.session.prompt.return_value = "你好"
        with patch("lingclaude.cli.repl._read_input", return_value="你好") as m:
            result = _next_input(ctx)
            assert result == "你好"
            m.assert_called_once_with(ctx)

    def test_non_pump_mode_blocking_read(self) -> None:
        """非 pump 模式直接走 _read_input。"""
        ctx = _make_ctx(readable=True)
        ctx.pump_mode = False
        ctx.session = MagicMock()
        ctx.session.prompt.return_value = "hi"
        with patch("lingclaude.cli.repl._read_input", return_value="hi") as m:
            result = _next_input(ctx)
            assert result == "hi"
            m.assert_called_once_with(ctx)
