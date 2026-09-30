# -*- coding: utf-8 -*-
"""_read_input 降级路径（裸 input() 隔离读）契约测试。

清偿 arch_debt/input-freeze-fallback-read-uncovered 的覆盖缺口：
降级链此前零测试——2026-09-24 输入冻结事故的降级路径无回归保护。
全 Mock，不开真终端、不碰 stdin。

2026-09-30 纠偏锚：降级路径 UnicodeDecodeError 重试必须走裸 input()
（与主读同源），不得回退 ctx.session.prompt——那是本路径明确绕开的
损坏 PT session（隔离读设计，repl.py _read_input docstring）。
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lingclaude.cli import repl


def _mk_ctx():
    session = SimpleNamespace(
        push_to_history=Mock(),
        prompt=Mock(side_effect=AssertionError("隔离读路径禁止触碰 PT session.prompt")),
    )
    return repl._ReplCtx(engine=None, status=None, session=session)


@pytest.fixture()
def patched(monkeypatch):
    """隔离 _read_input 的全部外沿：内置 input、readline 装配、看门狗。"""
    wd = Mock()
    hooks = SimpleNamespace(
        watchdog=wd,
        input=Mock(),
        add_history=Mock(),
        drain=Mock(),
        log_once=Mock(),
        patch_pt=Mock(),
        ensure_readline=Mock(),
    )
    monkeypatch.setattr("builtins.input", hooks.input)
    monkeypatch.setattr(repl, "_arm_input_watchdog", Mock(return_value=wd))
    monkeypatch.setattr(repl, "add_history_line", hooks.add_history)
    monkeypatch.setattr(repl, "_drain_stdin_buffer", hooks.drain)
    monkeypatch.setattr(repl, "_log_fallback_once", hooks.log_once)
    monkeypatch.setattr(repl, "_patch_pt_modifier_enter", hooks.patch_pt)
    monkeypatch.setattr(repl, "ensure_readline", hooks.ensure_readline)
    monkeypatch.setattr(repl, "get_output_format", Mock(return_value="plain"))
    monkeypatch.setattr(repl, "_status_prompt", lambda ctx: "P> ")
    return hooks


def _read(ctx):
    ctx.fallback_read = True
    return repl._read_input(ctx)


class TestFallbackReadContracts:
    def test_reads_via_builtin_input_and_records_history(self, patched):
        patched.input.return_value = "hello"
        ctx = _mk_ctx()
        assert _read(ctx) == "hello"
        patched.input.assert_called_once_with("P> ")
        ctx.session.push_to_history.assert_called_once_with("hello")
        patched.add_history.assert_called_once_with("hello")
        patched.log_once.assert_called_once()  # 首次降级留痕
        patched.watchdog.cancel.assert_called_once()  # 看门狗必撤

    def test_blank_line_skips_history(self, patched):
        patched.input.return_value = "   "
        assert _read(_mk_ctx()) == "   "
        patched.add_history.assert_not_called()

    def test_keyboard_interrupt_returns_empty(self, patched):
        patched.input.side_effect = KeyboardInterrupt
        assert _read(_mk_ctx()) == ""
        patched.watchdog.cancel.assert_called_once()

    def test_eoferror_propagates(self, patched):
        # EOF = 退出哨兵：降级路径不得吞掉，否则 Ctrl+D 无法退出
        patched.input.side_effect = EOFError
        with pytest.raises(EOFError):
            _read(_mk_ctx())
        patched.watchdog.cancel.assert_called_once()

    def test_decode_error_retries_builtin_input_not_broken_pt(self, patched):
        # 纠偏锚：残留字节清洗后重试必须仍是裸 input()，session.prompt 绝不触碰
        patched.input.side_effect = [UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"), "recovered"]
        assert _read(_mk_ctx()) == "recovered"
        assert patched.input.call_count == 2
        patched.drain.assert_called_once()

    def test_double_decode_error_reports_and_returns_empty(self, patched, capsys):
        patched.input.side_effect = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")
        assert _read(_mk_ctx()) == ""
        out = capsys.readouterr()
        assert "输入编码错误" in out.out
        assert patched.input.call_count == 2
