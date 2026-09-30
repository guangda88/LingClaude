"""2026-09-29 CJK 列对齐修复回归：SGR 样式 span 显示列坐标系。

根因：_extract_sgr_styles 按 codepoint 计列，而 PT 排版按 wcwidth（全角=2
列）排 fragment —— 含中文全角行上，SGR 样式段从首个全角字符起整体左移
（codepoint<显示列），带粗体/颜色的 rich 表格列错位。

修复：span 列号升格为**显示列**（全角+2），_style_range/_reverse_range
fragment 宽度同用 _line_disp_width。纯 ASCII 行 显示列==codepoint，零回归。
"""
from __future__ import annotations

import pytest

from lingclaude.cli.interface import _extract_sgr_styles


class TestExtractSgrStylesCjkColumns:
    """span 列号 = 显示列（全角=2）。"""

    def test_cjk_bold_span_is_display_cols(self):
        # 中文名称(4 codepoint, 8 显示列) 粗体 → span 端点须为 8 而非 4
        text, spans = _extract_sgr_styles("\x1b[1m中文名称\x1b[0m 备注")
        assert text == "中文名称 备注"
        assert len(spans) == 1
        lo, hi, st = spans[0]
        assert st == "bold"
        assert (lo, hi) == (0, 8), f"全角粗体段应 0-8 显示列, got {(lo, hi)}"

    def test_mixed_cjk_trailing_span_start(self):
        # 粗体 CJK 段之后，续段起点 = 前段显示列（非 codepoint）
        text, spans = _extract_sgr_styles(
            "\x1b[1m中文\x1b[0m\x1b[4m尾部\x1b[0m"
        )
        assert text == "中文尾部"
        assert spans[0] == (0, 4, "bold")
        assert spans[1] == (4, 8, "underline")

    def test_ascii_zero_regression(self):
        # 纯 ASCII：显示列==codepoint，行为与旧实现一致
        text, spans = _extract_sgr_styles("\x1b[1mabc\x1b[0mdef")
        assert text == "abcdef"
        assert spans == [(0, 3, "bold")]

    def test_non_sgr_csi_not_breaking_columns(self):
        # 非 SGR CSI 整体吞，不占列
        text, spans = _extract_sgr_styles("\x1b[?25l\x1b[1m中\x1b[0m文")
        assert text == "中文"
        assert spans == [(0, 2, "bold")]


@pytest.fixture()
def _pt_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """mock prompt_toolkit 可用（_style_range/_reverse_range 在 PT 分支内）。"""
    monkeypatch.setattr("lingclaude.cli.full_tui._HAS_PROMPT_TOOLKIT", True)


class TestStyleRangeCjkFragments:
    """_style_range/_reverse_range 按显示列切 fragment。"""

    def test_style_range_cjk_fragment(self, _pt_available: None) -> None:
        from lingclaude.cli.full_tui import _style_range

        # 行：粗体中文名称(8 显示列) + 空格 + value(5)
        text, spans = _extract_sgr_styles("\x1b[1m中文名称\x1b[0m value")
        assert spans == [(0, 8, "bold")]
        # fragment 即净化文本整体（TextArea 逐行渲染）
        frags = _style_range([("", text)], 0, 8, "bold")
        assert "".join(t for _, t in frags) == text
        assert "bold" in frags[0][0], f"首段应带 bold: {frags[0]!r}"
        # value 段（显示列 9-13）不受粗体影响
        assert not any("bold" in s for s, t in frags if t == "value")

    def test_style_range_ascii_unchanged(self, _pt_available: None) -> None:
        from lingclaude.cli.full_tui import _style_range

        frags = _style_range([("", "abcdef")], 1, 4, "italic")
        # 跨界切三段：pre='a', mid='bcd'(italic), post='ef'
        assert [t for _, t in frags] == ["a", "bcd", "ef"]
        assert "italic" in frags[1][0]
        assert "italic" not in frags[0][0] and "italic" not in frags[2][0]

    def test_reverse_range_cjk_fragment(self, _pt_available: None) -> None:
        from lingclaude.cli.full_tui import _reverse_range

        # 中文 = 4 显示列；全行反色不切段
        frags = _reverse_range([("", "中文")], 0, None)
        assert "".join(t for _, t in frags) == "中文"
        assert "reverse" in frags[0][0]
        # 跨界：显示列 [2, 6) 中 4 列行 → 整段进 mid
        frags = _reverse_range([("", "abc中")], 2, 4)
        texts = [t for _, t in frags]
        assert "".join(texts) == "abc中"
        assert "reverse" in frags[1][0]
        assert "reverse" not in frags[0][0]
