"""真实 FullTuiSession 全链探针：真实 Application + 渲染光栅 + 原始 SGR。

目的（2026-10-03 用户第三轮反馈）：拖选越界边缘自动滚在真实进程不工作，
但分发层探针（sgr_flow_probe.py）已证明 active 时 binding 收得到事件。
本探针用真实 FullTuiSession（含 mouse_support、真实 screen/wp），
喂真实 SGR 字节序列，逐步打印内部状态，定位断点。
"""
from __future__ import annotations

import asyncio
import sys as _sys
import threading
import time

from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from lingclaude.cli.full_tui import FullTuiSession


def find_out_region(s: FullTuiSession):
    """从真实渲染光栅拿输出窗的屏幕矩形 (x, y, w, h)，拿不到返回 None。"""
    app = s._app
    if app is None or app.renderer is None:
        return None
    screen = app.renderer.last_rendered_screen
    if screen is None:
        return None
    wp = screen.visible_windows_to_write_positions.get(s._output_area)
    if wp is None:
        return None
    return (wp.xpos, wp.ypos, wp.width, wp.height)


def main() -> None:  # noqa: C901
    s = FullTuiSession(history_file="/tmp/probe-hist")
    s._set_output_lines([f"row{i:03d}" for i in range(60)])
    with create_pipe_input() as pipe:
        # start() 自建真实 stdout 输出——但 Application 的 input 从哪来？
        # PT 默认 stdin，探针无法注入。查 _build_application 未传 input，
        # 所以必须 monkey-patch create_input 让 app 读 pipe。
        import lingclaude.cli.full_tui as ft

        _orig_create_input = ft.create_input if hasattr(ft, "create_input") else None
        import prompt_toolkit.input.defaults as _idflt

        _orig = _idflt.create_input

        def _patched(*a, **kw):
            return pipe

        _idflt.create_input = _patched
        try:
            s.start()
        finally:
            _idflt.create_input = _orig
        time.sleep(1.2)

        region = find_out_region(s)
        print("out region:", region)
        if region is None:
            print("FAIL: no rendered screen/wp")
            s.close()
            return
        x0, y0, w, h = region
        cx = x0 + w // 2

        def feed(seq: bytes) -> None:
            print("  feed:", seq)
            pipe.send_bytes(seq)
            time.sleep(0.3)
            app = s._app
            screen = app.renderer.last_rendered_screen
            wp = (
                screen.visible_windows_to_write_positions.get(s._output_area)
                if screen
                else None
            )
            cur = s._out_buffer.document.cursor_position_row
            print(
                f"    active={s._sel_active} start={s._sel_start} "
                f"end={s._sel_end} cursor_row={cur} edge_dir={s._sel_edge_scroll_dir}"
            )

        # 1) DOWN 在输出窗中部
        feed(f"\x1b[<0;{cx + 1};{y0 + 2 + 1}M".encode())
        # 2) MOVE 拖到输出窗内部下半
        feed(f"\x1b[<32;{cx + 1};{y0 + h - 2 + 1}M".encode())
        # 3) MOVE 越过底边（触发边缘自动滚）
        feed(f"\x1b[<32;{cx + 1};{y0 + h + 2 + 1}M".encode())
        # 4) 停在越界位置，观察定时滚（等 1.2s ≈ 两步）
        time.sleep(1.2)
        cur = s._out_buffer.document.cursor_position_row
        print(
            f"after dwell: active={s._sel_active} cursor_row={cur} "
            f"edge_dir={s._sel_edge_scroll_dir}"
        )
        # 5) 越界 UP 落锤
        feed(f"\x1b[<0;{cx + 1};{y0 + h + 2 + 1}m".encode())
        print("sel_dragged:", s._sel_dragged, "active:", s._sel_active)

        s.close()
        time.sleep(0.3)


if __name__ == "__main__":
    main()
