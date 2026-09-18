"""2026-09-18 多行输入增强：Ctrl+Enter / Shift+Enter 换行。

根因（实测于 prompt_toolkit 3.0.53）：
- PT 内建 ANSI 表把 \x1b[27;5;13~（Ctrl+Enter）/\x1b[27;2;13~（Shift+Enter）
  映射为 Keys.ControlM —— 修饰键被吃掉，按了等于普通 Enter（直接提交）。
- Keys 枚举没有 "Enter" 成员；add("enter") 经 KEY_ALIASES 解析为 ControlM。

修法：_patch_pt_modifier_enter() 在会话构造前把两个序列改映射为
(Keys.Escape, Keys.ControlM) —— 精确命中 P1/P2 已有的 Esc+Enter 换行 chord。
"""

from __future__ import annotations

import pytest

pt = pytest.importorskip("prompt_toolkit")

from prompt_toolkit.input import ansi_escape_sequences as aes  # noqa: E402
from prompt_toolkit.input.vt100_parser import Vt100Parser  # noqa: E402
from prompt_toolkit.key_binding.key_bindings import _parse_key  # noqa: E402
from prompt_toolkit.keys import Keys  # noqa: E402

from lingclaude.cli.interface import _patch_pt_modifier_enter  # noqa: E402

CTRL_ENTER = "\x1b[27;5;13~"
SHIFT_ENTER = "\x1b[27;2;13~"
EXPECTED = (Keys.Escape, Keys.ControlM)


@pytest.fixture(autouse=True)
def _patched_table():
    """每个用例前确保补丁已打（模块级幂等标志复位以便覆盖幂等路径）。"""
    _patch_pt_modifier_enter()
    yield


def test_table_maps_modifier_enter_to_esc_enter_chord():
    assert aes.ANSI_SEQUENCES[CTRL_ENTER] == EXPECTED
    assert aes.ANSI_SEQUENCES[SHIFT_ENTER] == EXPECTED


def test_chord_equivalence_with_existing_binding():
    """(Escape, ControlM) 与 add("escape", "enter") 精确同键。"""
    assert _parse_key("enter") == Keys.ControlM
    assert _parse_key("escape") == Keys.Escape


def test_vt100_parser_emits_esc_enter():
    """端到端：字节流进真 Vt100Parser，吐出换行 chord 的两个按键。"""
    got: list = []
    parser = Vt100Parser(lambda kp: got.append(kp.key))
    parser.feed(CTRL_ENTER)
    parser.flush()
    assert got == [Keys.Escape, Keys.ControlM]
    got.clear()
    parser.feed(SHIFT_ENTER)
    parser.flush()
    assert got == [Keys.Escape, Keys.ControlM]


def test_patch_is_idempotent():
    import lingclaude.cli.interface as iface

    assert iface._MAPPED_SEQUENCES is True
    before = aes.ANSI_SEQUENCES[CTRL_ENTER]
    _patch_pt_modifier_enter()
    assert aes.ANSI_SEQUENCES[CTRL_ENTER] is before


def test_pt_session_construction_applies_patch(monkeypatch):
    """P1 会话构造时自动打补丁（即使标志被复位）。"""
    import lingclaude.cli.interface as iface

    monkeypatch.setattr(iface, "_MAPPED_SEQUENCES", False)
    monkeypatch.setattr(aes, "ANSI_SEQUENCES", dict(aes.ANSI_SEQUENCES))
    aes.ANSI_SEQUENCES[CTRL_ENTER] = Keys.ControlM  # 模拟出厂状态

    session = iface.PromptToolkitSession(history_file=".lingclaude/history_test_mme")
    try:
        assert aes.ANSI_SEQUENCES[CTRL_ENTER] == EXPECTED
        assert iface._MAPPED_SEQUENCES is True
    finally:
        session.close() if hasattr(session, "close") else None


def test_patch_noop_without_prompt_toolkit(monkeypatch):
    """PT 缺失时静默 no-op，不抛异常。"""
    import lingclaude.cli.interface as iface

    monkeypatch.setattr(iface, "_MAPPED_SEQUENCES", False)
    monkeypatch.setattr(iface, "_HAS_PROMPT_TOOLKIT", False)
    _patch_pt_modifier_enter()  # 不应抛
    assert iface._MAPPED_SEQUENCES is False  # 未置位（无 PT 可补）


def test_wiring_p2_full_tui_calls_patch():
    """P2 全屏 TUI 构造路径引用了单源补丁（防接线回退）。"""
    import inspect

    from lingclaude.cli import full_tui

    src = inspect.getsource(full_tui.FullTuiSession.__init__)
    assert "_patch_pt_modifier_enter" in src


def test_wiring_repl_fallback_calls_patch():
    """repl 降级直读路径防御性打补丁（防接线回退）。"""
    import inspect

    from lingclaude.cli import repl

    src = inspect.getsource(repl._read_input)
    assert "_patch_pt_modifier_enter" in src
