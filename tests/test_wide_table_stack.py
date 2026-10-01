"""宽表堆叠降级回归（2026-10-01 宽表错乱修复）。

背景：5 列 CJK 宽表在 plain 补白与 rich 画框两条路径下都产出超宽行，
进输出窗被 prompt_toolkit 二次软折 → 边框/竖线全错位（用户实报）。
修复：超 _render_max_width() 的表（≥2列）统一转堆叠列表，plain/rich
同口径（共用 _table_grid/_table_grid_width），落窗与指纹保持同构。
"""

from lingclaude.cli.repl_io import (
    _defuse_wide_tables,
    _disp_w,
    _expected_window_lines,
    _pad_table_block,
    _render_max_width,
    _stack_table_block,
    _table_grid_width,
    render_markdown_lines,
)

WIDE_MD = """## 排序

| # | 中期项 | 勘察发现 | 成本 | 价值 |
| --- | --- | --- | --- | --- |
| **M1** | **文件级 rewind**（atomcode/cc checkpointing） | `_commands_checkpoint.py` 已有会话级 `/rewind`（161 行 6 命令）；**文件内容快照层完全缺失**——工具执行前不拍快照，改错文件无法还原 | 中（新 `file_snapshot.py` + executor 接线 + `/undo` 命令） | **高**：LLM 改错文件是高频事故，这是唯一无兜底的缺口 |
| M2 | gate 契约化 | `gate_scripts/` 不存在，现状 `success_gate.py` 单点 | 中 | 中：与现有 tool_auth yaml 有重叠 |

收尾段。
"""

NARROW_MD = """| 列A | 列B |
|-----|-----|
| 甲  | 乙  |
"""


def _grid_from(md_table_lines):
    from lingclaude.cli.repl_io import _table_grid

    return _table_grid(md_table_lines)


class TestPadBlockStacking:
    def test_wide_table_pads_to_stack(self):
        rows = [ln for ln in WIDE_MD.split("\n") if ln.startswith("|")]
        out = _pad_table_block(rows, {"pad_char": " ", "min_col_width": 0})
        assert out[0].startswith("- "), "宽表应转堆叠"
        assert all(not ln.startswith("|") for ln in out), "不应残留表格行"
        joined = "\n".join(out)
        assert "文件级 rewind" in joined and "gate 契约化" in joined, "内容零丢失"

    def test_narrow_table_stays_padded(self):
        rows = [ln for ln in NARROW_MD.split("\n") if ln.startswith("|")]
        out = _pad_table_block(rows, {"pad_char": " ", "min_col_width": 0})
        assert out[0].startswith("| "), "窄表保持补白表格"
        assert out[0].count("|") == 3

    def test_single_col_table_not_stacked(self):
        rows = ["| 独列 |", "| --- |", "| 甲 |"]
        out = _pad_table_block(rows, {"pad_char": " ", "min_col_width": 0})
        assert out[0].startswith("| "), "单列不参与堆叠"


class TestDefuseRich:
    def test_defuse_replaces_wide_table_keeps_rest(self):
        out = _defuse_wide_tables(WIDE_MD)
        assert "| ---" not in out, "宽表源应被改写"
        assert "## 排序" in out and "收尾段。" in out, "非表格内容原样"
        assert "- **M1**" in out

    def test_defuse_keeps_narrow_table(self):
        out = _defuse_wide_tables(NARROW_MD)
        assert out == NARROW_MD, "窄表原样保留"

    def test_rich_render_no_overflow(self):
        limit = _render_max_width()
        lines, _spans = render_markdown_lines(WIDE_MD)
        # rich 输出行剥离样式后不得超渲染宽度（窗内软折不再错位）
        assert all(_disp_w(ln) <= limit + 2 for ln in lines), (
            f"行超宽: max={max(_disp_w(l) for l in lines)} limit={limit}"
        )


class TestFingerprintIsomorphism:
    def test_expected_window_lines_stacked(self):
        exp = _expected_window_lines(WIDE_MD)
        stacked = [ln for ln in exp if ln.startswith("- ")]
        assert stacked, "指纹管线应含堆叠行（与落窗同构）"
        plain_table = [ln for ln in exp if ln.startswith("| ") and "---" not in ln]
        assert not plain_table, "指纹不应残留宽表补白行"

    def test_stack_content_matches_grid(self):
        grid = _grid_from([ln for ln in WIDE_MD.split("\n") if ln.startswith("|")])
        out = _stack_table_block(grid)
        assert out[0] == "- **M1**"
        assert any("勘察发现:" in ln for ln in out), "列名应作前缀"


def test_grid_width_consistent_with_pad():
    """_table_grid_width 与 _pad_table_block 口径一致：窄表判定不误伤。"""
    rows = [ln for ln in NARROW_MD.split("\n") if ln.startswith("|")]
    grid = _grid_from(rows)
    w = _table_grid_width(grid)
    padded = _pad_table_block(rows, {"pad_char": " ", "min_col_width": 0})
    assert w <= _render_max_width(), "窄表实测宽度不超限（否则测试夹具本身宽了）"
    assert padded[0].startswith("| ")
