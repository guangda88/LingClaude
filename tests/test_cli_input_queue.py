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



class TestPromptCollectRegression:
    """2026-09-18 重复输入事故回归 — 泵在生成期必须真读 stdin。

    根因：H17 会话级泵架构下，生成期唯一调 prompt() 的是 pump 线程；
    PT 包装层 streaming 短路（prompt 返回 ""）让泵空转不读 stdin，
    用户输入滞留终端缓冲至流结束才处理，体感「无响应需重输」。
    修复：InputPump 优先走 prompt_collect（真读，无视短路）。
    """

    def test_pump_uses_prompt_collect_when_available(self) -> None:
        """session 提供 prompt_collect 时，泵必须走它而非 prompt()。"""
        from lingclaude.cli.input_queue import InputPump as _P

        collect_calls: list[str] = []
        prompt_calls: list[str] = []

        class _ShortCircuitSession:
            """模拟 PT 包装层：streaming 期 prompt() 返回 ""（短路）。"""

            def __init__(self) -> None:
                self._interrupt = threading.Event()

            def prompt(self, message: str = "") -> str:
                prompt_calls.append(message)
                return ""  # streaming 短路

            def prompt_collect(self, message: str = "") -> str:
                collect_calls.append(message)
                return "from-collect"

            def interrupt_event(self) -> threading.Event:
                return self._interrupt

        q = InputQueue()
        pump = _P(_ShortCircuitSession(), q)
        pump.start()
        item = q.get(timeout=3)
        pump.stop()
        assert item == "from-collect", "泵应经 prompt_collect 拿到真输入"
        assert collect_calls, "泵应调用 prompt_collect"
        assert not prompt_calls, "短路 prompt() 不应被泵调用"

    def test_pump_falls_back_to_prompt_without_collect(self) -> None:
        """第三方/fake session 未实现 prompt_collect → 降级 prompt()（兼容）。"""
        q = InputQueue()
        session = _FakeSession(lines=["legacy-line"])
        assert not hasattr(session, "prompt_collect")
        pump = InputPump(session, q)
        pump.start()
        item = q.get(timeout=3)
        pump.stop()
        assert item == "legacy-line"

    def test_pump_ignores_mock_dynamic_collect(self) -> None:
        """MagicMock 的动态 prompt_collect 不算数 — 泵仍走 prompt() 旧路径。

        动态属性（非真绑定方法）未被配置 side_effect 时，本应触发的异常
        路径（test_pump_exception_marks_dead）会被 mock 吞掉 → dead 永不为
        True。isinstance(MethodType) 判定防住这类测试基建回归。
        """
        q = InputQueue()
        session = MagicMock()
        session.prompt.side_effect = EOFError
        pump = InputPump(session, q)
        pump.start()
        item = q.get(timeout=3)
        pump.stop()
        assert InputQueue.is_eof(item), "mock 未实现真收集读 → 应走 prompt() 的 EOF 路径"

    def test_prompt_collect_bypasses_streaming_short_circuit(self) -> None:
        """PT 包装层 prompt_collect 无视 _streaming 标志，真读内层 session。

        2026-09-18 二次修复：同时验证 in_thread=True 传入（避免与主线程
        Application 冲突，导致打字被吞）。
        """
        from lingclaude.cli.interface import PromptToolkitSession, _HAS_PROMPT_TOOLKIT

        if not _HAS_PROMPT_TOOLKIT:
            pytest.skip("prompt_toolkit 未安装")

        sess = PromptToolkitSession(history_file="/tmp/lc-test-history-pc3")

        class _Inner:
            def __init__(self) -> None:
                self.called_with: list[str] = []
                self.in_thread_flags: list[bool] = []

            def prompt(self, message: str = "", **kwargs: bool) -> str:
                self.called_with.append(message)
                self.in_thread_flags.append(bool(kwargs.get("in_thread", False)))
                return "inner-real-read"

        inner = _Inner()
        sess._session = inner  # noqa: SLF001 — 测试注入内层
        sess.set_streaming(True)
        # 旧 bug：此时 prompt() 短路返回 ""；prompt_collect 必须真读
        # 二次修复：in_thread=True 避免与主线程 Application 冲突
        assert sess.prompt_collect("灵克> ") == "inner-real-read"
        assert inner.called_with == ["灵克> "]
        assert inner.in_thread_flags == [True], "必须传 in_thread=True 隔离 Application"
        sess.set_streaming(False)

    def test_prompt_still_short_circuits_during_streaming(self) -> None:
        """短路本身保留（主线程防双阻塞）：streaming 期 prompt() 仍返回 ""。"""
        from lingclaude.cli.interface import PromptToolkitSession, _HAS_PROMPT_TOOLKIT

        if not _HAS_PROMPT_TOOLKIT:
            pytest.skip("prompt_toolkit 未安装")

        sess = PromptToolkitSession(history_file="/tmp/lc-test-history-pc2")
        sess.set_streaming(True)
        assert sess.prompt() == ""
        sess.set_streaming(False)
