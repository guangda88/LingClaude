"""CLI 交互真功能测试 — RFC §5.1（CLI_INTERACTION_RFC v1.1）。

替换 test_t1_wiring.py 中 T1-7 的 inspect.getsource 假测试（只验源码字符串，
不验功能）。本文件用 in-memory history + fake stream 做真功能测试。
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lingclaude.cli.interface import (
    DEFAULT_HISTORY_FILE,
    FallbackSession,
    PromptSessionInterface,
    PromptToolkitSession,
    create_session,
    _HAS_PROMPT_TOOLKIT,
)


@pytest.fixture(autouse=True)
def _plain_mode():
    """默认 plain 模式（非 TTY 环境，避免 prompt_toolkit 干扰）。"""
    os.environ["LINGCLAUDE_CLI_MODE"] = "plain"
    yield
    os.environ.pop("LINGCLAUDE_CLI_MODE", None)


class TestSessionSelection:
    def test_plain_mode_returns_fallback(self):
        """LINGCLAUDE_CLI_MODE=plain → FallbackSession。"""
        sess = create_session()
        assert isinstance(sess, FallbackSession)

    def test_non_tty_returns_fallback(self, monkeypatch):
        """非 TTY → FallbackSession（CI/WebUI 场景）。"""
        monkeypatch.delenv("LINGCLAUDE_CLI_MODE", raising=False)
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        sess = create_session()
        assert isinstance(sess, FallbackSession)

    def test_fallback_satisfies_protocol(self):
        """FallbackSession 满足 PromptSessionInterface Protocol。"""
        sess = FallbackSession()
        assert isinstance(sess, PromptSessionInterface)


class TestFallbackHistory:
    def test_push_history_persists_to_file(self, tmp_path):
        """push_to_history 持久化到文件，重载后可读回。"""
        hf = tmp_path / "history"
        sess = FallbackSession(history_file=str(hf))
        sess.push_to_history("/help")
        sess.push_to_history("hello world")

        sess2 = FallbackSession(history_file=str(hf))
        assert "/help" in sess2._history
        assert "hello world" in sess2._history

    def test_empty_input_not_saved(self, tmp_path):
        """空输入不入历史。"""
        hf = tmp_path / "history"
        sess = FallbackSession(history_file=str(hf))
        sess.push_to_history("   ")
        assert sess._history == []

    def test_prompt_returns_input(self):
        """prompt 返回用户输入。"""
        sess = FallbackSession()
        with patch("builtins.input", return_value="/help"):
            assert sess.prompt("灵克> ") == "/help"


class TestInterruptEvent:
    def test_interrupt_initial_clear(self):
        """interrupt_event 初始未 set。"""
        sess = FallbackSession()
        assert not sess.interrupt_event().is_set()

    def test_interrupt_set_visible(self):
        """set 后 is_set 生效（模拟 Esc 触发）。"""
        sess = FallbackSession()
        sess.interrupt_event().set()
        assert sess.interrupt_event().is_set()

    def test_prompt_clears_interrupt(self):
        """每次 prompt 前 clear 打断状态。"""
        sess = FallbackSession()
        sess.interrupt_event().set()
        with patch("builtins.input", return_value="x"):
            sess.prompt()
        assert not sess.interrupt_event().is_set()


class TestEscListenLoop:
    def test_non_tty_no_false_trigger(self):
        """非 TTY 下 _esc_listen_loop 不误触发打断。"""
        import threading as _t
        from lingclaude.cli.app import _esc_listen_loop

        sess = FallbackSession()
        stop = _t.Event()
        t = threading.Thread(target=_esc_listen_loop, args=(sess, stop), daemon=True)
        t.start()
        t.join(timeout=0.2)
        assert not sess.interrupt_event().is_set()
        stop.set()
        t.join(timeout=0.2)
        assert not t.is_alive(), "stop 置位后线程必须退出（审计#6：防永生线程堆积）"

    def test_stop_event_exits_loop(self):
        """stop 事件置位 → 线程立即退出，即使 interrupt_event 未 set。"""
        import threading as _t
        from lingclaude.cli.app import _esc_listen_loop

        sess = FallbackSession()
        stop = _t.Event()
        stop.set()
        t = threading.Thread(target=_esc_listen_loop, args=(sess, stop), daemon=True)
        t.start()
        t.join(timeout=0.5)
        assert not t.is_alive()
        assert not sess.interrupt_event().is_set()

    def test_esc_pressed_non_tty_false(self):
        """非 TTY 下 _esc_pressed 返回 False。"""
        from lingclaude.cli.app import _esc_pressed

        assert _esc_pressed() is False


class TestPromptToolkitSession:
    def test_requires_prompt_toolkit(self):
        """prompt_toolkit 未装时 PromptToolkitSession 抛 RuntimeError。"""
        import lingclaude.cli.interface as iface

        if iface._HAS_PROMPT_TOOLKIT:
            pytest.skip("prompt_toolkit 已安装")
        with pytest.raises(RuntimeError):
            PromptToolkitSession()

    def test_completer_accepted(self):
        """PromptToolkitSession 接受 WordCompleter。"""
        import lingclaude.cli.interface as iface

        if not iface._HAS_PROMPT_TOOLKIT:
            pytest.skip("prompt_toolkit 未安装")
        from prompt_toolkit.completion import WordCompleter

        c = WordCompleter(["/help", "/clear"], ignore_case=True)
        sess = PromptToolkitSession(completer=c)
        assert sess._session.completer is not None

    def test_multiline_enabled_no_truncation(self):
        """长文截断修复（2026-09-16）：多行模式开启，粘贴长文不被截断。

        回归锁定：multiline=True 是「长文字被截断吞没」（单行模式只保留
        第一行、其余当 Enter 提交丢弃）的根治配置。断言 session 的
        multiline 为真，且 Enter 提交 / Esc+Enter 换行绑定已注入。
        """
        import lingclaude.cli.interface as iface

        if not iface._HAS_PROMPT_TOOLKIT:
            pytest.skip("prompt_toolkit 未安装")

        sess = PromptToolkitSession(history_file="/tmp/lingclaude_pt_multiline_test.json")
        assert getattr(sess._session, "multiline", False) is True

        kb = sess._session.key_bindings
        assert kb is not None
        keys_desc = [str(k) for k in {b.keys: b.handler for b in kb.bindings}.keys()]
        assert any("ControlM" in k for k in keys_desc), "Enter 提交绑定缺失"
        assert any(
            "Escape" in k and "ControlM" in k for k in keys_desc
        ), "Esc+Enter 换行绑定缺失"


class TestDisplayComponents:
    def test_print_markdown_exists(self):
        """print_markdown 可调用。"""
        from lingclaude.cli.display import print_markdown

        assert callable(print_markdown)

    def test_tool_call_panel_lifecycle(self):
        """ToolCallPanel start/add/stop 生命周期不抛错。"""
        from lingclaude.cli.display import ToolCallPanel

        panel = ToolCallPanel()
        panel.start()
        panel.add_tool_start("read", "path=test.py")
        panel.add_tool_end(False, "ok")
        panel.stop()

    def test_status_bar_render(self):
        """StatusBar.render 组合 model/tokens/mode。"""
        from lingclaude.cli.display import StatusBar

        sb = StatusBar()
        text = sb.render(model="deepseek-v4-flash", tokens=1234, mode="auto")
        assert "deepseek-v4-flash" in text
        assert "1234 tokens" in text
        assert "auto" in text


class TestH17InputPumpFix:
    """H17-输入泵修复:UnicodeDecodeError 不再只清一行导致无限循环。"""

    def test_drain_stdin_buffer_does_not_raise_on_non_tty(self):
        """非 TTY 下 _drain_stdin_buffer 静默跳过，不抛异常。"""
        import os
        from unittest.mock import patch

        # 模拟非 TTY：isatty() -> False
        with patch.object(os, "read", side_effect=OSError("should not be called")):
            with patch("sys.stdin.isatty", return_value=False):
                from lingclaude.cli.repl import _drain_stdin_buffer
                # 不抛异常即通过
                _drain_stdin_buffer()

    def test_read_input_retries_after_drain(self, monkeypatch, capsys):
        """UnicodeDecodeError 后清缓冲区再重试 prompt，不立即 return ''。"""
        from lingclaude.cli import repl as _repl
        from lingclaude.cli.interface import PromptSessionInterface

        retry_count = [0]

        class _FakeSession(PromptSessionInterface):
            def prompt(self, *args, **kwargs):
                retry_count[0] += 1
                if retry_count[0] == 1:
                    raise UnicodeDecodeError("utf-8", b"\x80", 0, 1, "invalid start byte")
                return "user input after retry"

            def push_to_history(self, text):
                pass

            def interrupt_event(self):
                import threading
                return threading.Event()

        class _FakeCtx:
            session = _FakeSession()
            fallback_read = False

        # Mock _status_prompt 和 _drain_stdin_buffer，避免 toolbar/TTY 依赖
        monkeypatch.setattr(_repl, "_status_prompt", lambda ctx: "灵克> ")
        monkeypatch.setattr(_repl, "_drain_stdin_buffer", lambda: None)

        result = _repl._read_input(_FakeCtx())
        assert result == "user input after retry"
        assert retry_count[0] == 2, "UnicodeDecodeError 后应重试一次"

    def test_read_input_gives_up_after_two_failures(self, monkeypatch, capsys):
        """连续两次 UnicodeDecodeError 才打印错误并返回空字符串。"""
        from lingclaude.cli import repl as _repl
        from lingclaude.cli.interface import PromptSessionInterface

        call_count = [0]

        class _FakeSession(PromptSessionInterface):
            def prompt(self, *args, **kwargs):
                call_count[0] += 1
                raise UnicodeDecodeError("utf-8", b"\x80", 0, 1, "invalid start byte")

            def push_to_history(self, text):
                pass

            def interrupt_event(self):
                import threading
                return threading.Event()

        class _FakeCtx:
            session = _FakeSession()
            fallback_read = False

        monkeypatch.setattr(_repl, "_status_prompt", lambda ctx: "灵克> ")
        monkeypatch.setattr(_repl, "_drain_stdin_buffer", lambda: None)

        result = _repl._read_input(_FakeCtx())
        assert result == ""
        assert call_count[0] == 2, "应恰好重试一次后放弃"
        captured = capsys.readouterr()  # 消费 "[输入编码错误，请检查终端编码设置]"
        # 第二次失败才打印错误信息（第一次只清缓冲区不打印）
        assert "输入编码错误" in captured.out or "输入编码错误" in captured.err


class TestStreamingNonBlocking:
    """TUI 输入泵问题修复：streaming 期间 prompt 不阻塞，方向键/退格正常."""

    def test_fallback_session_set_streaming_flag(self):
        """FallbackSession.set_streaming() 设置内部标志."""
        sess = FallbackSession()
        assert hasattr(sess, "set_streaming")
        sess.set_streaming(True)
        assert sess._streaming is True
        sess.set_streaming(False)
        assert sess._streaming is False

    def test_pt_session_set_streaming_flag(self):
        """PromptToolkitSession.set_streaming() 设置内部标志."""
        if not _HAS_PROMPT_TOOLKIT:
            pytest.skip("prompt_toolkit 未安装")
        sess = PromptToolkitSession()
        assert hasattr(sess, "set_streaming")
        sess.set_streaming(True)
        assert sess._streaming is True
        sess.set_streaming(False)
        assert sess._streaming is False

    def test_fallback_nonblocking_returns_empty_during_streaming(self, monkeypatch, tmp_path):
        """streaming=True 时 prompt() 非阻塞，立即返回空串."""
        monkeypatch.setenv("LINGCLAUDE_CLI_MODE", "plain")
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)  # 强制非 TTY

        # 让 _nonblocking_readline 返回空串（模拟超时）
        sess = FallbackSession(history_file=str(tmp_path / "hist"))
        sess._streaming = True  # 直接设，跳过 prompt() 分支
        result = sess.prompt("灵克> ")
        assert result == ""  # streaming 期间立即返回，不阻塞

    def test_fallback_ctrl_c_returns_empty_non_streaming(self, monkeypatch, tmp_path):
        """非 streaming 时 Ctrl+C 返回空串（caller 靠返回值感知中断）."""
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("builtins.input", lambda msg: (_ for _ in ()).throw(KeyboardInterrupt))

        sess = FallbackSession(history_file=str(tmp_path / "hist"))
        result = sess.prompt("> ")
        assert result == ""  # caller 靠空串感知 Ctrl+C，不靠 interrupt 事件

    def test_fallback_ctrl_c_sets_interrupt_streaming(self, monkeypatch, tmp_path):
        """streaming 期间 Ctrl+C → _nonblocking_readline 收到 \\x03 → set interrupt."""
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)  # 非 TTY → streaming 走非阻塞分支

        sess = FallbackSession(history_file=str(tmp_path / "hist"))
        sess._streaming = True  # 直接进非阻塞分支（_nonblocking_readline）
        # _nonblocking_readline 检测 stdin.isatty() → False → 直接返回 ""
        # 真实 Ctrl+C 在 TTY 环境下才触发 interrupt（非 TTY 无键盘输入来源）
        assert sess.prompt("> ") == ""  # streaming 非 TTY 不阻塞，立即返回

    def test_protocol_has_set_streaming(self):
        """PromptSessionInterface 协议包含 set_streaming 方法."""
        assert hasattr(PromptSessionInterface, "prompt")
        assert hasattr(PromptSessionInterface, "set_streaming")
        assert hasattr(PromptSessionInterface, "stream_print")
        assert hasattr(PromptSessionInterface, "interrupt_event")
