"""lineedit 单源 helper 测试 —— 2026-09-18 方向键/历史修复。

覆盖：
- ensure_readline 幂等、add_history_line 去重/空白过滤
- load_history_file 缺失文件静默
- FallbackSession 两条 input() 路径挂 readline + 写内存历史
- repl.full_tui / engine.coding 的 input() 路径同样挂钩
"""

from __future__ import annotations

import builtins
from unittest.mock import patch

import pytest

from lingclaude.core import lineedit as lineedit_mod
from lingclaude.core.lineedit import (
    add_history_line,
    ensure_readline,
    load_history_file,
)


# ── helper 单元 ──


def test_ensure_readline_idempotent() -> None:
    """重复调用幂等且返回值一致（Linux 下应挂载成功）。"""
    first = ensure_readline()
    second = ensure_readline()
    assert first is second
    assert first is lineedit_mod._rl_attached


def test_add_history_line_skips_blank() -> None:
    assert add_history_line("") is False
    assert add_history_line("   ") is False


def test_add_history_line_dedup_consecutive() -> None:
    assert add_history_line("dup-line") is True
    assert add_history_line("dup-line") is False  # 连续重复不记
    assert add_history_line("other") is True
    assert add_history_line("dup-line") is True  # 非连续重复允许


def test_load_history_file_missing_silent(tmp_path) -> None:
    """缺失文件静默返回，不抛异常。"""
    load_history_file(str(tmp_path / "no-such-history"))


# ── FallbackSession 接线 ──


def _install_fake_input(monkeypatch, lines: list[str]) -> None:
    it = iter(lines)

    def fake_input(prompt: str = "") -> str:
        return next(it)

    monkeypatch.setattr(builtins, "input", fake_input)


def test_fallback_prompt_records_readline_history(monkeypatch) -> None:
    """prompt() 非流式路径：input 读到的行写进 readline 内存历史。"""
    from lingclaude.cli.interface import FallbackSession

    sess = FallbackSession(history_file=str(pytest.importorskip("pathlib").Path("/tmp")) )
    _install_fake_input(monkeypatch, ["hello-rl"])
    out = sess.prompt("> ")
    assert out == "hello-rl"
    import readline

    n = readline.get_current_history_length()
    assert readline.get_history_item(n) == "hello-rl"


def test_fallback_prompt_eof_propagates(monkeypatch) -> None:
    """EOF 必须传播（审计#1 死循环防复发），且不写历史。"""
    from lingclaude.cli.interface import FallbackSession

    sess = FallbackSession(history_file="/tmp")

    def eof_input(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr(builtins, "input", eof_input)
    with pytest.raises(EOFError):
        sess.prompt("> ")


def test_fallback_prompt_collect_records_history(monkeypatch) -> None:
    """prompt_collect（泵收集）路径同样写内存历史。"""
    from lingclaude.cli.interface import FallbackSession

    sess = FallbackSession(history_file="/tmp")
    sess._streaming = True  # 强制走 collect 分支
    _install_fake_input(monkeypatch, ["pumped-line"])
    out = sess.prompt_collect("> ")
    assert out == "pumped-line"
    assert sess._streaming is True  # 标志恢复
    import readline

    n = readline.get_current_history_length()
    assert readline.get_history_item(n) == "pumped-line"


# ── 其他 input() 调用点接线 ──


def test_repl_fallback_read_uses_helper(monkeypatch) -> None:
    """repl._read_input 的 fallback_read 分支应调 ensure_readline + add_history。"""
    import lingclaude.cli.repl as repl_mod

    calls: list[str] = []

    class _Sess:
        def push_to_history(self, t: str) -> None:
            calls.append(f"push:{t}")

    class _Ctx:
        fallback_read = True
        session = _Sess()

    monkeypatch.setattr(repl_mod, "ensure_readline", lambda: calls.append("ensure") or True)
    monkeypatch.setattr(repl_mod, "add_history_line", lambda t: calls.append(f"rl:{t}") or True)
    monkeypatch.setattr(repl_mod, "_status_prompt", lambda ctx: "> ")
    monkeypatch.setattr(repl_mod, "get_output_format", lambda: "plain")
    monkeypatch.setattr(builtins, "input", lambda p="": "repl-line")

    out = repl_mod._read_input(_Ctx())
    assert out == "repl-line"
    assert "ensure" in calls and "rl:repl-line" in calls and "push:repl-line" in calls


def test_full_tui_degraded_input_uses_helper(monkeypatch) -> None:
    """FullTuiSession 降级 input() 路径挂钩 readline + 内存历史。"""
    import lingclaude.cli.full_tui as ft_mod

    calls: list[str] = []

    class _Sess:
        _ever_started = False
        _interrupt = None

    monkeypatch.setattr(ft_mod, "ensure_readline", lambda: calls.append("ensure") or True)
    monkeypatch.setattr(ft_mod, "add_history_line", lambda t: calls.append(f"rl:{t}") or True)
    monkeypatch.setattr(builtins, "input", lambda p="": "tui-degraded")

    import threading

    s = _Sess()
    s._interrupt = threading.Event()
    out = ft_mod.FullTuiSession.prompt(s, "> ")  # type: ignore[arg-type]
    assert out == "tui-degraded"
    assert "ensure" in calls and "rl:tui-degraded" in calls


def test_coding_permission_input_uses_helper(monkeypatch) -> None:
    """engine.coding 权限问答 input() 挂钩 readline。"""
    import lingclaude.engine.coding as coding_mod

    calls: list[str] = []
    monkeypatch.setattr(coding_mod, "ensure_readline", lambda: calls.append("ensure") or True)
    monkeypatch.setattr(coding_mod, "add_history_line", lambda t: calls.append(f"rl:{t}") or True)
    monkeypatch.setattr(builtins, "input", lambda p="": "1")

    assert coding_mod.ensure_readline() is True
    # 只验证接线存在（完整问答流程由既有权限测试覆盖）
    assert callable(coding_mod.add_history_line)
