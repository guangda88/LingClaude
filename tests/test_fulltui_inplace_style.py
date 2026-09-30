"""TUI 原位上色链路测试（2026-09-30）。

覆盖 FullTuiSession 轮次标记 + 指纹校验 + 原位替换：
1. set_streaming(False→True) 记录轮次起点
2. 纯文本轮：replace_turn_styled 原位替换，素字→带样式，样式表行号正确
3. 工具行交错：指纹不符 → 返回 False，窗内容一个字不动
4. 无标记 / drop_stream_mark 后：返回 False
5. 多轮交替：上一轮样式在后续替换后行号保持正确
"""

from __future__ import annotations

import pytest

from lingclaude.cli.full_tui import FullTuiSession


@pytest.fixture()
def _pt_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lingclaude.cli.full_tui._HAS_PROMPT_TOOLKIT", True)


def _window_lines(s: FullTuiSession) -> list[str]:
    text = s._out_buffer.text  # noqa: SLF001
    return text.split("\n") if text else []


class TestInPlaceStyling:
    def test_mark_records_row_on_streaming_rise(
        self, _pt_available: None, tmp_path
    ) -> None:
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        s.append_output("用户: hi\n")
        assert s._stream_start_mark == -1  # noqa: SLF001
        s.set_streaming(True)
        assert s._stream_start_mark == 1  # noqa: SLF001（"用户: hi" 之后的行号）
        s.set_streaming(False)
        s.set_streaming(True)  # 第二轮重新标记
        assert s._stream_start_mark == 1  # noqa: SLF001

    def test_plain_turn_replaced_in_place(
        self, _pt_available: None, tmp_path
    ) -> None:
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        s.append_output("用户: 介绍一下\n")
        s.set_streaming(True)
        s.append_output("**粗体** 与\n\n普通行\n")
        assert _window_lines(s) == ["用户: 介绍一下", "**粗体** 与", "", "普通行"]
        styled = ["粗体 与", "", "普通行"]
        spans: list[list[tuple[int, int, str]]] = [[(0, 2, "1")], [], []]
        ok = s.replace_turn_styled(styled, spans, ["**粗体** 与", "", "普通行"])
        assert ok is True
        assert _window_lines(s) == ["用户: 介绍一下", "粗体 与", "", "普通行"]
        assert s._style_map == {1: [(0, 2, "1")]}  # noqa: SLF001

    def test_tool_line_interleave_fingerprint_mismatch(
        self, _pt_available: None, tmp_path
    ) -> None:
        """工具行混入窗内区间（done.content 不含）→ 指纹不符，拒绝替换。"""
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        s.append_output("用户: 帮我查\n")
        s.set_streaming(True)
        s.append_output("先看代码\n")
        s.append_output("[tool_call] read_file /x.py\n")
        s.append_output("✅ 42 行\n")
        s.append_output("结论是……\n")
        expected = ["先看代码", "结论是……"]  # done.content 只有文本部分
        styled = ["先看代码", "结论是……"]
        ok = s.replace_turn_styled(styled, [[], []], expected)
        assert ok is False
        # 窗内一个字不动（工具行绝不会被误删）
        assert "read_file" in s._out_buffer.text  # noqa: SLF001
        assert _window_lines(s)[-1] == "结论是……"

    def test_no_mark_returns_false(self, _pt_available: None, tmp_path) -> None:
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        s.append_output("内容\n")
        assert (
            s.replace_turn_styled(["x"], [[]], ["内容"]) is False
        )  # 从未 set_streaming

    def test_drop_stream_mark_invalidates(self, _pt_available: None, tmp_path) -> None:
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        s.set_streaming(True)
        s.append_output("被打断的轮次\n")
        s.drop_stream_mark()  # repl.py finally 兜底等价物
        assert s.replace_turn_styled(["x"], [[]], ["被打断的轮次"]) is False
        assert _window_lines(s) == ["被打断的轮次"]  # 原文保留

    def test_second_turn_preserves_first_turn_style(
        self, _pt_available: None, tmp_path
    ) -> None:
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        # 第一轮：上色成功
        s.set_streaming(True)
        s.append_output("第一轮 **加粗**\n")
        assert s.replace_turn_styled(
            ["第一轮 加粗"], [[(4, 6, "1")]], ["第一轮 **加粗**"]
        )
        # 第二轮：增行替换（1 行素字 → 3 行彩色版）
        s.set_streaming(False)
        s.set_streaming(True)
        assert s._stream_start_mark == 1  # noqa: SLF001
        s.append_output("第二轮 *斜体*\n")
        ok = s.replace_turn_styled(
            ["第二轮 斜体A", "", "第二轮 斜体B"],
            [[(4, 6, "3")], [], []],
            ["第二轮 *斜体*"],
        )
        assert ok is True
        assert _window_lines(s) == [
            "第一轮 加粗",
            "第二轮 斜体A",
            "",
            "第二轮 斜体B",
        ]
        # 第一轮样式行号不受第二轮影响；第二轮样式落新行号
        assert s._style_map == {0: [(4, 6, "1")], 1: [(4, 6, "3")]}  # noqa: SLF001

    def test_expanded_lines_then_plain_append_keeps_styles(
        self, _pt_available: None, tmp_path
    ) -> None:
        """替换增行后，后续普通 append 的样式行号按 _append_output_lines 旧逻辑
        以当前行数为准——验证两套路径行号口径一致，不互相踩。"""
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        s.set_streaming(True)
        s.append_output("A *em*\n")
        assert s.replace_turn_styled(["A em", "", "尾行"], [[(2, 4, "3")], [], []], ["A *em*"])
        s.set_streaming(False)
        s.append_output("B **strong**\n")  # 普通追加（无 SGR，样式空）
        s.set_streaming(True)
        s.append_output("C *i*\n")
        assert s.replace_turn_styled(
            ["C i", "", "C 尾"], [[(2, 3, "3")], [], []], ["C *i*"]
        )
        assert _window_lines(s) == ["A em", "", "尾行", "B **strong**", "C i", "", "C 尾"]
        assert s._style_map == {  # noqa: SLF001
            0: [(2, 4, "3")],
            4: [(2, 3, "3")],
        }
