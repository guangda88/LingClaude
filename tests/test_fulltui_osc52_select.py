"""OSC52 拖选复制测试（2026-09-27）。

覆盖：
- 三态状态机（DOWN→LEFT+MOVE→UP）
- 行式取词（单行/多行/反向拖选/越界静默 clamp）
- OSC52 序列字节格式（\\x1b]52;c;<base64>\\x1b\\）
- 原地点击不复制（无拖动）
- 悬停 MOVE（button=NONE）不更新选区
- 写入失败 → 静默（不反噬 UI，无状态栏劫持）
- 工具栏永不因复制反馈被劫持（2026-09-27 用户反馈回归锁定）
- 回调异常安全
"""

from __future__ import annotations

import base64
import os
from typing import Any

import pytest

from lingclaude.cli.full_tui import FullTuiSession

pt = pytest.importorskip("prompt_toolkit")
from prompt_toolkit.data_structures import Point  # noqa: E402
from prompt_toolkit.mouse_events import (  # noqa: E402
    MouseButton,
    MouseEventType,
    MouseEvent,
)


def _make_session(tmp_path: Any) -> FullTuiSession:
    return FullTuiSession(history_file=str(tmp_path / "h"))


def _ev(et: Any, x: int, y: int, button: Any = MouseButton.LEFT) -> MouseEvent:
    return MouseEvent(
        position=Point(x=x, y=y), event_type=et, button=button, modifiers=frozenset()
    )


def _set_out_text(s: FullTuiSession, text: str) -> None:
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.document import Document

    s._out_buffer.set_document(Document(text, 0), bypass_readonly=True)


def _capture_fd(s: FullTuiSession):
    """返回 (读端, 写端)：写端挂到 _osc52_fd，读端非阻塞。"""
    r, w = os.pipe()
    os.set_blocking(r, False)
    s._osc52_fd = w
    return r, w


def _drain(r: int) -> bytes:
    chunks = []
    while True:
        try:
            b = os.read(r, 65536)
        except BlockingIOError:
            break
        if not b:
            break
        chunks.append(b)
    return b"".join(chunks)


class TestThreeStateSelect:
    def test_drag_copies_multiline(self, tmp_path: Any) -> None:
        s = _make_session(tmp_path)
        _set_out_text(s, "alpha\nbeta-line-long\ngamma")
        r, w = _capture_fd(s)
        try:
            c = s._out_control
            c.mouse_handler(_ev(MouseEventType.MOUSE_DOWN, 2, 0))
            c.mouse_handler(_ev(MouseEventType.MOUSE_MOVE, 15, 1))
            c.mouse_handler(_ev(MouseEventType.MOUSE_UP, 4, 2))
        finally:
            os.close(w)
        data = _drain(r)
        os.close(r)
        # 行式语义：首行从列 2 到行尾；中间整行；末行含列 4（含）
        expected = "pha\nbeta-line-long\ngamma"
        assert data == b"\x1b]52;c;" + base64.b64encode(expected.encode()) + b"\x1b\\"

    def test_click_without_drag_no_copy(self, tmp_path: Any) -> None:
        s = _make_session(tmp_path)
        _set_out_text(s, "alpha\nbeta\ngamma")
        r, w = _capture_fd(s)
        try:
            c = s._out_control
            c.mouse_handler(_ev(MouseEventType.MOUSE_DOWN, 2, 0))
            c.mouse_handler(_ev(MouseEventType.MOUSE_UP, 2, 0))
        finally:
            os.close(w)
        assert _drain(r) == b""
        os.close(r)

    def test_reverse_drag_normalized(self, tmp_path: Any) -> None:
        s = _make_session(tmp_path)
        _set_out_text(s, "alpha\nbeta\ngamma")
        r, w = _capture_fd(s)
        try:
            c = s._out_control
            c.mouse_handler(_ev(MouseEventType.MOUSE_DOWN, 3, 2))
            c.mouse_handler(_ev(MouseEventType.MOUSE_MOVE, 1, 0))
            c.mouse_handler(_ev(MouseEventType.MOUSE_UP, 1, 0))
        finally:
            os.close(w)
        data = _drain(r)
        os.close(r)
        # 行式语义（xterm 惯例）：松手字符含入 → 末行取 [:ec+1]
        expected = "lpha\nbeta\ngamm"
        assert data == b"\x1b]52;c;" + base64.b64encode(expected.encode()) + b"\x1b\\"

    def test_hover_move_button_none_ignored(self, tmp_path: Any) -> None:
        s = _make_session(tmp_path)
        c = s._out_control
        c.mouse_handler(_ev(MouseEventType.MOUSE_DOWN, 1, 0))
        c.mouse_handler(
            _ev(MouseEventType.MOUSE_MOVE, 9, 9, button=MouseButton.NONE)
        )
        # 悬停不更新终点
        assert s._sel_end == (0, 1)

    def test_move_after_up_does_not_resurrect(self, tmp_path: Any) -> None:
        s = _make_session(tmp_path)
        _set_out_text(s, "alpha\nbeta")
        r, w = _capture_fd(s)
        try:
            c = s._out_control
            c.mouse_handler(_ev(MouseEventType.MOUSE_DOWN, 0, 0))
            c.mouse_handler(_ev(MouseEventType.MOUSE_MOVE, 3, 0))
            c.mouse_handler(_ev(MouseEventType.MOUSE_UP, 3, 0))
            # 丢 UP 后的 MOVE：不得再次触发复制
            c.mouse_handler(_ev(MouseEventType.MOUSE_MOVE, 4, 0))
        finally:
            os.close(w)
        data = _drain(r)
        os.close(r)
        # 仅一次复制（一组 OSC52 序列）
        assert data.count(b"\x1b]52;c;") == 1


class TestOsc52Channel:
    def test_sequence_format_direct(self, tmp_path: Any) -> None:
        s = _make_session(tmp_path)
        r, w = _capture_fd(s)
        try:
            assert s._osc52_copy("hello 世界") is True
        finally:
            os.close(w)
        data = _drain(r)
        os.close(r)
        expected_b64 = base64.b64encode("hello 世界".encode("utf-8"))
        assert data == b"\x1b]52;c;" + expected_b64 + b"\x1b\\"

    def test_write_failure_silent(self, tmp_path: Any) -> None:
        s = _make_session(tmp_path)
        s._status_cb = lambda: [("class:sep", "TOOLBAR")]
        s._osc52_fd = -1  # 非法 fd → os.write 必炸
        assert s._osc52_copy("x") is False
        # 静默失败：无复制反馈劫持，工具栏原样
        assert s._status_fragments() == [("class:sep", "TOOLBAR")]

    def test_oversize_rejected(self, tmp_path: Any) -> None:
        s = _make_session(tmp_path)
        s._osc52_fd = None  # 走 /dev/tty 分支前应先被大小门拦下
        assert s._osc52_copy("a" * (9 * 1024 * 1024)) is False

    def test_toolbar_never_hijacked(self, tmp_path: Any) -> None:
        """用户反馈回归（2026-09-27）：复制反馈曾整行替换工具栏且不恢复，
        表现为 toolbar 消失只剩「✓ 已复制」。现反馈已整体移除——
        状态栏 fragments 必须无条件走 _status_cb（工具栏原样）。"""
        s = _make_session(tmp_path)
        s._status_cb = lambda: [("class:sep", "TOOLBAR")]
        # 先复制一次（模拟松手成功路径）
        r, w = _capture_fd(s)
        try:
            s._osc52_copy("hello")
        finally:
            os.close(w)
        os.close(r)
        frags = s._status_fragments()
        assert frags == [("class:sep", "TOOLBAR")]


class TestSafety:
    def test_callback_exception_swallowed(self, tmp_path: Any) -> None:
        s = _make_session(tmp_path)
        # position 缺 y/x 属性 → 内部 AttributeError 必须被吞
        s._on_select_event("down", object())
        assert s._sel_start is None
        assert s._sel_active is False

    def test_extract_out_of_range_silent(self, tmp_path: Any) -> None:
        s = _make_session(tmp_path)
        _set_out_text(s, "ab")
        s._sel_start = (0, 0)
        s._sel_end = (50, 50)  # 行越界（输出被裁剪竞态等）
        # 宁可不复制，不错误复制（坐标异常 → 空选区）
        assert s._extract_selected_text() == ""

    def test_status_fragment_never_shows_copy_feedback(self, tmp_path: Any) -> None:
        """复制反馈已整体移除：即使手工塞入旧字段也不该劫持工具栏。
        （_sel_status 属性已不存在，此处锁定 fragments 直通 _status_cb。）"""
        s = _make_session(tmp_path)
        s._status_cb = lambda: [("class:sep", "TOOLBAR")]
        frags = s._status_fragments()
        assert frags == [("class:sep", "TOOLBAR")]
        assert not any("已复制" in frag[1] for frag in frags)


class TestSelectionHighlight:
    """拖选反色高亮 processor（视觉反馈层）行为锁定。"""

    def _proc(self, s: FullTuiSession) -> Any:
        procs = s._out_control.input_processors
        assert procs, "输出窗必须挂 _SelectionHighlightProcessor"
        return procs[0]

    def _apply(self, s: FullTuiSession, lineno: int, text: str) -> Any:
        from prompt_toolkit.layout.processors import TransformationInput

        ti = TransformationInput(
            buffer_control=s._out_control,
            document=None,
            lineno=lineno,
            source_to_display=lambda x: x,
            fragments=[("", text)],
            width=80,
            height=24,
        )
        return self._proc(s).apply_transformation(ti)

    @staticmethod
    def _text(frags: Any) -> str:
        return "".join(t for _, t, *_ in frags)

    @staticmethod
    def _reversed_text(frags: Any) -> str:
        return "".join(t for st, t, *_ in frags if "reverse" in st)

    def test_single_line_partial(self, tmp_path: Any) -> None:
        s = _make_session(tmp_path)
        s._sel_start, s._sel_end = (0, 2), (0, 5)
        out = self._apply(s, 0, "hello world").fragments
        assert self._text(out) == "hello world"
        assert self._reversed_text(out) == "llo "  # [2,5+1) → 列2..5（含），列5是空格

    def test_multiline_ranges(self, tmp_path: Any) -> None:
        s = _make_session(tmp_path)
        s._sel_start, s._sel_end = (0, 3), (2, 4)
        l0 = self._apply(s, 0, "abcdef").fragments
        l1 = self._apply(s, 1, "xyz").fragments
        l2 = self._apply(s, 2, "abcdef").fragments
        l3 = self._apply(s, 3, "abc").fragments  # 选区外
        assert self._reversed_text(l0) == "def"
        assert self._reversed_text(l1) == "xyz"
        assert self._reversed_text(l2) == "abcde"  # [0, 4+1)
        assert self._reversed_text(l3) == ""

    def test_no_selection_and_click_untouched(self, tmp_path: Any) -> None:
        s = _make_session(tmp_path)
        assert self._reversed_text(self._apply(s, 0, "abc").fragments) == ""
        s._sel_start = s._sel_end = (0, 1)  # 原地点击
        assert self._reversed_text(self._apply(s, 0, "abc").fragments) == ""

    def test_reverse_drag_normalized(self, tmp_path: Any) -> None:
        s = _make_session(tmp_path)
        s._sel_start, s._sel_end = (0, 5), (0, 2)  # 反向
        assert self._reversed_text(self._apply(s, 0, "abcdef").fragments) == "cdef"

    def test_out_of_range_row_untouched(self, tmp_path: Any) -> None:
        s = _make_session(tmp_path)
        s._sel_start, s._sel_end = (0, 2), (0, 5)
        assert self._reversed_text(self._apply(s, 9, "abcdef").fragments) == ""


class TestScrollDuringSelect:
    """拖选进行中滚屏联动（2026-10-03）。

    修复「拖选复制时无法滚动屏幕，导致不能选全」：拖选中滚轮/翻页，
    选区终点行随视口平移（起点锚定）——对齐终端原生「拖选+滚轮=延伸选区」
    惯例；_sel_dragged 置真保证 UP 落锤取词。
    """

    def test_wheel_extends_selection_downward(self, tmp_path: Any) -> None:
        s = _make_session(tmp_path)
        _set_out_text(s, "\n".join(f"line{i}" for i in range(20)))
        s._on_select_event("down", Point(x=3, y=2))
        s._scroll_out_lines(3)
        assert s._sel_end[0] == 5  # 终点随滚动 +3
        assert s._sel_start == (2, 3)  # 起点锚定
        assert s._sel_dragged is True  # UP 将落锤取词

    def test_wheel_up_shrinks_end_clamped(self, tmp_path: Any) -> None:
        s = _make_session(tmp_path)
        _set_out_text(s, "\n".join(f"line{i}" for i in range(20)))
        s._on_select_event("down", Point(x=0, y=4))
        s._on_select_event("move", Point(x=5, y=8))
        s._scroll_out_lines(-2)
        assert s._sel_end[0] == 6  # 8-2

    def test_end_clamped_to_last_line(self, tmp_path: Any) -> None:
        s = _make_session(tmp_path)
        _set_out_text(s, "\n".join(f"line{i}" for i in range(20)))
        s._on_select_event("down", Point(x=0, y=5))
        s._scroll_out_lines(1000)
        assert s._sel_end[0] == 19  # 钳制到末行

    def test_scroll_without_selection_untouched(self, tmp_path: Any) -> None:
        s = _make_session(tmp_path)
        _set_out_text(s, "\n".join(f"line{i}" for i in range(20)))
        s._scroll_out_lines(3)  # 无拖选：纯滚动
        assert s._sel_start is None and s._sel_end is None

    def test_follow_mode_restored_on_full_scroll(self, tmp_path: Any) -> None:
        """滚回文末应恢复跟随（既有语义），拖选中亦然。"""
        s = _make_session(tmp_path)
        _set_out_text(s, "\n".join(f"line{i}" for i in range(20)))
        s._on_select_event("down", Point(x=0, y=2))
        s._scroll_out_lines(-5)
        assert s._follow_output is False
        s._scroll_out_lines(100)  # 到底
        assert s._follow_output is True

    def test_wheel_during_select_then_copy_captures_scrolled_content(
        self, tmp_path: Any
    ) -> None:
        """端到端：DOWN→滚轮 3 行→UP，取词应覆盖滚过的行（选全）。"""
        s = _make_session(tmp_path)
        lines = [f"row{i}" for i in range(20)]
        _set_out_text(s, "\n".join(lines))
        r, w = _capture_fd(s)
        try:
            s._on_select_event("down", Point(x=0, y=2))
            s._scroll_out_lines(3)
            s._on_select_event("up", Point(x=0, y=2))  # 松手格(2,0) 不得拽回选区
            raw = _drain(r)
            assert raw.startswith(b"\x1b]52;c;"), "OSC52 序列头"
            payload = raw[len(b"\x1b]52;c;"):-2]  # 剥头（ESC]52;c;）与尾（ESC\）
            data = base64.b64decode(payload).decode()
            got = data.split("\n")
            assert got[0] == "row2" and got[1] == "row3" and got[2] == "row4"
            # 末行 = row5 首字符：滚轮延伸的终点行 5 被保留（UP 松手格 (2,0)
            # 小于延伸终点 (5,0)，不得覆盖——否则选区被拽回、选不全）
            assert got[-1] == "r" and len(got) == 4
        finally:
            os.close(r)
            os.close(w)
