"""B(2026-09-26) full_tui 防爆破上限测试。

覆盖（atomcode retained.rs 借鉴的三重防御）：
1. 输入框显示行数封顶（Dimension max=INPUT_DISPLAY_MAX_LINES），内容不截断
2. 输入框 wrap_lines（长行折行显示，不撑横向）
3. 无换行流防御：_stream_line_buf 达 _STREAM_LINE_BUF_MAX 强制断行
"""

from __future__ import annotations

import pytest

from lingclaude.cli import full_tui
from lingclaude.cli.full_tui import INPUT_DISPLAY_MAX_LINES, FullTuiSession


class TestInputDisplayCap:
    def test_cap_constant(self) -> None:
        """封顶值锁定 6（与 atomcode 输入框显示上限一致）。"""
        assert INPUT_DISPLAY_MAX_LINES == 6

    def test_input_area_height_capped(self, tmp_path) -> None:
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        dim = s._input_area.window.height  # noqa: SLF001
        assert dim.max == INPUT_DISPLAY_MAX_LINES
        assert dim.min == 1

    def test_input_wrap_lines_on(self, tmp_path) -> None:
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        # PT 把 wrap_lines 包成 Condition（可调用 filter）——与 multiline 同款
        wl = s._input_area.window.wrap_lines  # noqa: SLF001
        assert (wl() if callable(wl) else bool(wl)) is True

    def test_long_content_not_truncated(self, tmp_path) -> None:
        """显示封顶 ≠ 内容截断：buffer 保留完整文本。"""
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        long_text = "\n".join(f"line-{i}" for i in range(50))
        s._input_area.buffer.text = long_text  # noqa: SLF001
        assert s._input_area.buffer.text == long_text  # noqa: SLF001
        assert s._input_area.buffer.text.count("\n") == 49  # noqa: SLF001


class TestStreamLineBufCap:
    def test_cap_constant(self) -> None:
        from lingclaude.cli import repl_io
        assert repl_io._STREAM_LINE_BUF_MAX == 65536

    def test_no_newline_stream_force_flushed(self, monkeypatch) -> None:
        """单事件超上限：强制断行输出，不留无限增长的残行缓冲。"""
        from lingclaude.cli import repl_io

        written: list[str] = []
        monkeypatch.setattr(repl_io, "_stream_write",
                            lambda s: written.append(s))
        monkeypatch.setattr(repl_io, "_OUTPUT_FORMAT", "plain")
        monkeypatch.setattr(repl_io, "_stream_line_buf", [])
        big = "x" * (repl_io._STREAM_LINE_BUF_MAX + 100)
        repl_io._handle_stream_event({"type": "text_delta", "text": big})
        # 达上限 → 立即落一行（不留在缓冲）
        assert written == [big + "\n"]
        assert repl_io._stream_line_buf == []

    def test_gradual_growth_flushed_at_cap(self, monkeypatch) -> None:
        """多事件渐进累积：累积 ≥ 上限时强制断行，内容不丢。"""
        from lingclaude.cli import repl_io

        written: list[str] = []
        monkeypatch.setattr(repl_io, "_stream_write",
                            lambda s: written.append(s))
        monkeypatch.setattr(repl_io, "_OUTPUT_FORMAT", "plain")
        monkeypatch.setattr(repl_io, "_stream_line_buf", [])
        chunk = "y" * 40000  # 无换行
        repl_io._handle_stream_event({"type": "text_delta", "text": chunk})
        assert repl_io._stream_line_buf == [chunk]  # 未达上限，留在缓冲
        repl_io._handle_stream_event({"type": "text_delta", "text": chunk})
        # 80000 ≥ 65536 → 强制断行
        assert "".join(written) == ("y" * 80000) + "\n"
        assert repl_io._stream_line_buf == []
