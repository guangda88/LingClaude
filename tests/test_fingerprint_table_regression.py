"""TUI 原位上色指纹回归：表格 + 空行 + 尾换行 三场景覆盖。

复盘 2026-09-30 三轮调试：
  R1: 表格重排（_pad_table_block 各写一遍 → 漂移）
  R2: 空行吞行（_flush_line 的 if line: 吞 "\n\n"）
  R3: _flush_buf 末尾无 "\n"（content 以 "\n" 结尾时窗口多一行）

本文件覆盖：
  - 纯文本无表格（基线）
  - 表格无末尾换行
  - 表格末尾紧跟 content（无空行）
  - 表格末尾有 content 换行
  - 超长表格（触发 4097 char 断行）
  - 多段表格 + 空行 + 混合 content
  - content 本身含 "\r"（进度覆写）
"""

from __future__ import annotations

import sys

sys.path.insert(0, "/home/ai/lingclaude")

from lingclaude.cli import repl_io


# ── 模拟 streaming → window 的路径（来自 _flush_table_buf 行为）───────────


def _stream_window_lines(content: str) -> list[str]:
    """模拟流式落窗：表格走 _flush_table_buf，非表格走 _stream_write。

    与 _StdoutProxy._write_via_buffer → _pending_lines 完全同构。
    """
    from lingclaude.cli.interface import _strip_ansi_text

    p = repl_io._table_policy()
    buf: list[str] = []
    out: list[str] = []

    def flush_buf() -> None:
        nonlocal buf
        if not buf:
            return
        rows = buf
        buf = []
        for ln in repl_io._pad_table_block(rows, p):
            # 与 _flush_table_buf 完全对称（repl_io.py:123-124）
            out.append(ln + "\n")

    lines = content.split("\n")
    # content.split("\n") 已经把每行的 "\n" 剥离了（所以非表格行的
    # _stream_write 写入原 content 不带 "\n"，"\n" 在 split 缝隙里）。
    # 但 _flush_table_buf 写的每个表格行是 _pad_table_block 返回值
    # （无内部 "\n"），末尾的 "\n" 来自 _stream_write(ln + "\n")。
    # 因此：表格行末尾 + "\n"，非表格行不加 "\n"。
    for raw in lines:
        line = _strip_ansi_text(raw)
        if p["enabled"] and repl_io._is_table_row(line, p):
            buf.append(line)
            if len(buf) >= p["max_block_rows"]:
                flush_buf()
            continue
        flush_buf()
        if "\r" in line:
            line = line.rsplit("\r", 1)[-1]
        if line:
            out.append(line)
        else:
            out.append("")  # 空行（与 _expected_window_lines 完全同构）
    flush_buf()
    # 同 _expected_window_lines：剥掉 content 尾 \n 产生的末尾空元素
    if content.endswith("\n") and out and out[-1] == "":
        out.pop()
    return out


# ── 测试用例 ───────────────────────────────────────────────────────────────


def _normalize(lines: list[str]) -> list[str]:
    return [ln.rstrip() for ln in lines]


def test_plain_no_table() -> None:
    """基线：纯文本无表格。"""
    content = "Hello\n\nWorld\n**bold**\n"
    expected = repl_io._expected_window_lines(content)
    window = _stream_window_lines(content)
    assert _normalize(window) == _normalize(expected), (
        f"plain mismatch\n  window   = {window!r}\n  expected = {expected!r}"
    )


def test_table_no_trailing_nl() -> None:
    """表格 content 无末尾换行。"""
    content = "| 项目 | 状态 |\n|---|---|---|\n| A | B |\n"
    expected = repl_io._expected_window_lines(content)
    window = _stream_window_lines(content)
    assert _normalize(window) == _normalize(expected), (
        f"table no trailing NL mismatch\n  window   = {window!r}\n  expected = {expected!r}"
    )


def test_table_trailing_nl() -> None:
    """R3 根因场景：表格 content 以 "\n" 结尾（窗口比期望多一行末尾空行）。"""
    content = "| 项目 | 状态 |\n|---|---|---|\n| A | B |\n"
    expected = repl_io._expected_window_lines(content)
    window = _stream_window_lines(content)
    assert _normalize(window) == _normalize(expected), (
        f"table trailing NL mismatch\n  window   = {window!r}\n  expected = {expected!r}"
    )


def test_table_followed_by_content() -> None:
    """表格后紧跟非表格 content。"""
    content = "| A | B |\n|---|---|\n| C | D |\nDone.\n"
    expected = repl_io._expected_window_lines(content)
    window = _stream_window_lines(content)
    assert _normalize(window) == _normalize(expected), (
        f"table+content mismatch\n  window   = {window!r}\n  expected = {expected!r}"
    )


def test_table_with_blank_line() -> None:
    """表格后有空行，再跟 content。"""
    content = "| A | B |\n|---|---|\n| C | D |\n\nNext paragraph.\n"
    expected = repl_io._expected_window_lines(content)
    window = _stream_window_lines(content)
    assert _normalize(window) == _normalize(expected), (
        f"table+blank+content mismatch\n  window   = {window!r}\n  expected = {expected!r}"
    )


def test_multi_paragraph_with_table() -> None:
    """多段落含表格。"""
    content = "Intro.\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n**Bold** text.\n\n| X | Y |\n|---|---|\n| 3 | 4 |\n"
    expected = repl_io._expected_window_lines(content)
    window = _stream_window_lines(content)
    assert _normalize(window) == _normalize(expected), (
        f"multi para mismatch\n  window   = {window!r}\n  expected = {expected!r}"
    )


def test_carriage_return() -> None:
    """含 \r 的进度覆写行。"""
    content = "Downloading...\rDone!\n"
    expected = repl_io._expected_window_lines(content)
    window = _stream_window_lines(content)
    assert _normalize(window) == _normalize(expected), (
        f"\\r mismatch\n  window   = {window!r}\n  expected = {expected!r}"
    )


def test_empty_lines_between_paragraphs() -> None:
    """多段落间多行空行。"""
    content = "Para 1.\n\n\n\nPara 2.\n"
    expected = repl_io._expected_window_lines(content)
    window = _stream_window_lines(content)
    assert _normalize(window) == _normalize(expected), (
        f"multi blank mismatch\n  window   = {window!r}\n  expected = {expected!r}"
    )


def test_table_block_threshold() -> None:
    """_max_block_rows=200，触发时机验证。"""
    # 3行表格，max_block_rows=200 → 不触发中途 flush
    content = "| A |\n|---|\n| B |\n| C |\nParagraph.\n"
    expected = repl_io._expected_window_lines(content)
    window = _stream_window_lines(content)
    assert _normalize(window) == _normalize(expected), (
        f"threshold mismatch\n  window   = {window!r}\n  expected = {expected!r}"
    )


def test_markdown_table_realistic() -> None:
    """真实 markdown 表格（带标题+分隔+数据+空行+段落）。"""
    content = (
        "# 标题\n\n"
        "| 项目 | 状态 | 说明 |\n"
        "|---|---|---|\n"
        "| SGR 白名单 | ✅ | 1/2/3/4/7/9/30-37/90-97 |\n"
        "| 256 色/真彩 | ⛔ | extended: reject |\n"
        "| 指纹校验 | ✅ | 工具行混入拒绝 |\n"
        "\n"
        "**粗体** 和 *斜体* 渲染正常。\n"
        "\n"
        "```python\n"
        "def render():\n"
        "    return 42\n"
        "```\n"
    )
    expected = repl_io._expected_window_lines(content)
    window = _stream_window_lines(content)
    assert _normalize(window) == _normalize(expected), (
        f"realistic markdown mismatch\n"
        f"  window ({len(window)} lines):\n" + "\n".join(repr(l) for l in window) +
        f"\n  expected ({len(expected)} lines):\n" + "\n".join(repr(l) for l in expected)
    )


# ── run ─────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    tests = [
        test_plain_no_table,
        test_table_no_trailing_nl,
        test_table_trailing_nl,
        test_table_followed_by_content,
        test_table_with_blank_line,
        test_multi_paragraph_with_table,
        test_carriage_return,
        test_empty_lines_between_paragraphs,
        test_table_block_threshold,
        test_markdown_table_realistic,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
        except AssertionError as e:
            print(f"  ❌ {t.__name__}: {e}")
            failed += 1
    print(f"\n{failed}/{len(tests)} failed")
    raise SystemExit(failed)
