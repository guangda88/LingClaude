"""TUI 原位上色指纹同构回归（2026-09-30 表格重排失配事故）。

事故：指纹用 content.split("\\n") 裸比对，而流式落窗对表格行块做了
显示列重排补白 → 带表格轮次指纹恒失配，替换静默回退素字（全测试绿
但真实轮必翻车）。修复：_expected_window_lines 与落窗管线逐字符同构
（ANSI 清洗 / 表格重排 / \\r 与 4097 断行 / 尾换行伪影），重排核心
_pad_table_block 与 _flush_table_buf 共用同一实现。
"""

from __future__ import annotations

import sys

import pytest

import lingclaude.cli.full_tui as ft
from lingclaude.cli import repl_io
from lingclaude.cli.full_tui import FullTuiSession

TABLE_CONTENT = (
    "# 标题\n"
    "\n"
    "| 项目 | 状态 |\n"
    "|---|---|\n"
    "| SGR 白名单 | ✅ |\n"
    "\n"
    "**粗体** 结束\n"
)


def _stream_turn(s: FullTuiSession, content: str, chunk: int = 32) -> None:
    """按真实流式路径喂一轮：text_delta 分片 + done（stdout 须已接代理）。"""
    repl_io.set_output_format("text")
    s.set_streaming(True)
    for i in range(0, len(content), chunk):
        repl_io._handle_stream_event(
            {"type": "text_delta", "text": content[i : i + chunk]}
        )
    repl_io._handle_stream_event({"type": "done", "content": content})


class TestFingerprintIsomorphic:
    def test_table_turn_replaced_in_place(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """带表格的轮次：指纹经同构变换后比对 → 替换成功，** 字面量消失。"""
        monkeypatch.setattr(ft, "_HAS_PROMPT_TOOLKIT", True)
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        repl_io.set_full_tui_managed(True)
        proxy = ft._StdoutProxy(s, sys.stdout)
        monkeypatch.setattr(sys, "stdout", proxy)

        _stream_turn(s, TABLE_CONTENT)

        lines = s._out_buffer.text.split("\n")
        assert not any("**" in ln for ln in lines), "粗体字面量未替换"
        assert len(s._style_map) >= 1, "样式表为空"

    def test_expected_lines_match_manual_window_feed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """同构性硬核校验：expected(content) == 真实流式落窗素字段。"""
        monkeypatch.setattr(ft, "_HAS_PROMPT_TOOLKIT", True)
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        proxy = ft._StdoutProxy(s, sys.stdout)
        monkeypatch.setattr(sys, "stdout", proxy)

        s.set_streaming(True)
        repl_io._handle_stream_event({"type": "text_delta", "text": TABLE_CONTENT})
        s.set_streaming(False)

        text = s._out_buffer.text or ""
        # set_streaming(True) 时窗尚空 → mark=0，整窗即流式段（rstrip 归一同口径）
        window = text.split("\n")
        assert [ln.rstrip() for ln in window] == [
            ln.rstrip() for ln in repl_io._expected_window_lines(TABLE_CONTENT)
        ]

    def test_trailing_newline_no_ghost_row(self) -> None:
        """content 尾换行不产生幽灵空行（split 伪影剔除）。"""
        assert repl_io._expected_window_lines("甲\n乙\n") == ["甲", "乙"]

    def test_cr_and_oversize_isomorphic(self) -> None:
        """\\r 丢半行 + 超长行 4097 断行与代理写入语义一致。"""
        long_line = "x" * 5000
        lines = repl_io._expected_window_lines("abc\rdef\n" + long_line)
        assert lines[0] == "def"
        assert len(lines[1]) == 4097 and len(lines[2]) == 903
