"""InputPump / InputQueue 组件测试 — H17 真缺口补全（阶段0）。

验证：
- InputQueue FIFO 顺序、EOF 哨兵、drain 过滤、pending 计数
- InputPump start/stop join 单读者防重入
- InputPump 异常死亡标记 dead 传播
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock


from lingclaude.cli.input_queue import (
    EOF_SENTINEL,
    InputPump,
    InputQueue,
    is_slash_command,
)


class TestInputQueue:
    def test_fifo_order(self) -> None:
        q = InputQueue()
        q.put("first")
        q.put("second")
        q.put("/help")
        assert q.get() == "first"
        assert q.get() == "second"
        assert q.get() == "/help"
        assert q.get(timeout=0.01) is None

    def test_eof_sentinel(self) -> None:
        q = InputQueue()
        q.put_eof()
        item = q.get()
        assert item == EOF_SENTINEL
        assert InputQueue.is_eof(item)

    def test_drain_filters_eof(self) -> None:
        q = InputQueue()
        q.put("hello")
        q.put_eof()
        q.put("world")
        dropped = q.drain()
        assert dropped == ["hello", "world"]
        assert q.pending() == 0

    def test_pending(self) -> None:
        q = InputQueue()
        assert q.pending() == 0
        q.put("a")
        assert q.pending() == 1
        q.get()
        assert q.pending() == 0

    def test_is_slash_command(self) -> None:
        assert is_slash_command("/help")
        assert is_slash_command("/quit extra")
        assert not is_slash_command("//escaped")
        assert not is_slash_command("hello")
        assert not is_slash_command("")


class _FakeSession:
    """可控 fake session：prompt 返回预设行列表，或阻塞直到 stop。"""

    def __init__(self, lines: list[str] | None = None, block: bool = False) -> None:
        self._lines = lines or []
        self._idx = 0
        self._block = block
        self._interrupt = threading.Event()

    def prompt(self, message: str = "") -> str:
        if self._block:
            self._interrupt.wait(timeout=5)
            raise EOFError
        if self._idx < len(self._lines):
            line = self._lines[self._idx]
            self._idx += 1
            return line
        raise EOFError

    def interrupt_event(self) -> threading.Event:
        return self._interrupt


class TestInputPump:
    def test_pump_feeds_queue(self) -> None:
        q = InputQueue()
        session = _FakeSession(lines=["hello", "world"])
        pump = InputPump(session, q)
        pump.start()
        # 等待 pump 消费完
        for _ in range(50):
            if q.pending() >= 2:
                break
            time.sleep(0.02)
        assert q.get() == "hello"
        assert q.get() == "world"
        pump.stop()

    def test_pump_eof_signals_queue(self) -> None:
        q = InputQueue()
        session = _FakeSession(lines=[])
        pump = InputPump(session, q)
        pump.start()
        item = q.get(timeout=3)
        assert InputQueue.is_eof(item)
        pump.stop()

    def test_stop_joins_thread(self) -> None:
        """stop() 后线程在 timeout 内退出（P0-join fix）。"""
        q = InputQueue()
        session = _FakeSession(block=True)
        pump = InputPump(session, q)
        pump.start()
        assert pump.is_alive()
        pump.stop()
        # join(timeout=2) 后线程应退出
        assert not pump.is_alive()

    def test_start_when_old_thread_alive_marks_dead(self) -> None:
        """旧线程卡在 prompt 时 start() 不开新线程，标记 dead（P0 单读者守卫）。"""
        q = InputQueue()
        session = _FakeSession(block=True)
        pump = InputPump(session, q)
        pump.start()
        assert pump.is_alive()
        # 不调 stop()，直接 start() — 旧线程还活着，新 start() 应标记 dead
        pump.start()
        assert pump.dead
        pump.stop()

    def test_pump_filters_blank_lines(self) -> None:
        q = InputQueue()
        session = _FakeSession(lines=["", "  ", "real"])
        pump = InputPump(session, q)
        pump.start()
        for _ in range(50):
            if q.pending() >= 1:
                break
            time.sleep(0.02)
        assert q.get() == "real"
        pump.stop()

    def test_pump_exception_marks_dead(self) -> None:
        q = InputQueue()
        session = MagicMock()
        session.prompt.side_effect = RuntimeError("terminal broken")
        pump = InputPump(session, q)
        pump.start()
        for _ in range(50):
            if pump.dead:
                break
            time.sleep(0.02)
        assert pump.dead
        pump.stop()

    def test_pump_heartbeat_advances_on_prompt_return(self) -> None:
        """心跳语义：prompt 正常返回即打拍（含空行）—— 线程在轮转的硬证据。"""
        q = InputQueue()
        session = _FakeSession(lines=["", "  ", "real"])
        pump = InputPump(session, q)
        pump.start()
        for _ in range(50):
            if q.pending() >= 1:
                break
            time.sleep(0.02)
        assert q.get() == "real"
        # prompt 成功返回过（含空行过滤前）→ 心跳应显著晚于启动时刻
        assert pump.last_beat() >= pump._start_t - 1e-9  # noqa: SLF001
        pump.stop()

    def test_heartbeat_stagnates_while_blocked(self) -> None:
        """心跳停滞语义：prompt 阻塞等输入期间心跳不推进 —— 主循环不得单凭心跳判死。"""
        q = InputQueue()
        session = _FakeSession(block=True)
        pump = InputPump(session, q)
        pump.start()
        b0 = pump.last_beat()
        time.sleep(0.15)
        assert pump.is_alive()          # 活着
        assert pump.last_beat() == b0   # 但心跳停滞（阻塞中）
        pump.stop()

