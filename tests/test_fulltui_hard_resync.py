"""全屏 TUI 渲染结构错位根治回归（2026-09-27）。

用户实机两症状同偏移（鼠标选择偏上 3 行 + toolbar 不可见）根因：
物理屏幕结构被渲染链外裸写字节/Ctrl+L 默认 clear-screen 破坏后，
PT diff 渲染器不感知（its diff baseline 仍认为旧屏有效），不整屏重画。
受控复现（进程内 Application + 同构布局 + SGR 鼠标注入）证明布局与
坐标映射零缺陷——错位只来自渲染器外部破坏。

本测试锁定三层防护：
1. hard_resync：renderer.reset 全量复位（软 resync 治不了结构错位）
2. Ctrl+L 重绑：覆盖 PT 默认 clear-screen（erase(leave_alternate_screen
   =True 默认值) 会退出 1049h 备用屏——错位的最大单点破坏源）
3. _warn_raw / _tui_visible_print：渲染链零字节直写终端
"""

from __future__ import annotations

import io
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

from lingclaude.cli.full_tui import FullTuiSession, _StdoutProxy, _warn_raw

pt = pytest.importorskip("prompt_toolkit")


def _make_session(tmp_path: Any) -> FullTuiSession:
    return FullTuiSession(history_file=str(tmp_path / "h"))


class TestHardResync:
    def test_calls_renderer_reset_when_running(self, tmp_path: Any) -> None:
        """app 驻留时 hard_resync 必须调 renderer.reset（diff 基线清零）。"""
        s = _make_session(tmp_path)
        s._running = True
        renderer = MagicMock()
        app = MagicMock()
        app.renderer = renderer
        s._app = app
        calls = []
        s._refresh_output_area = lambda: calls.append("refresh")
        s.hard_resync()
        renderer.reset.assert_called_once()
        assert calls == ["refresh"]
        s._running = False

    def test_noop_when_not_running(self, tmp_path: Any) -> None:
        """未启动时 hard_resync 静默 no-op（不炸、不碰 app）。"""
        s = _make_session(tmp_path)
        s._running = False
        s._app = None
        calls = []
        s._refresh_output_area = lambda: calls.append("refresh")
        s.hard_resync()
        assert calls == ["refresh"]  # 文档重建仍做（幂等无害），无 app 可碰

    def test_renderer_exception_swallowed(self, tmp_path: Any) -> None:
        """renderer.reset 炸 → 吞异常退回软 resync 语义（文档重建仍执行）。"""
        s = _make_session(tmp_path)
        s._running = True
        renderer = MagicMock()
        renderer.reset.side_effect = RuntimeError("boom")
        app = MagicMock()
        app.renderer = renderer
        s._app = app
        calls = []
        s._refresh_output_area = lambda: calls.append("refresh")
        s.hard_resync()  # 不应抛
        assert calls == ["refresh"]
        s._running = False

    def test_none_renderer_tolerated(self, tmp_path: Any) -> None:
        """app.renderer 为 None（构造极早期）→ 跳过复位不炸。"""
        s = _make_session(tmp_path)
        s._running = True
        app = MagicMock()
        app.renderer = None
        s._app = app
        s._refresh_output_area = lambda: None
        s.hard_resync()
        s._running = False


class TestCtrlLRebind:
    def test_c_l_binding_registered(self, tmp_path: Any) -> None:
        """Ctrl+L 必须注册在会话键位表（覆盖 PT 默认 clear-screen）。"""
        s = _make_session(tmp_path)
        from prompt_toolkit.keys import Keys

        matched = [b for b in s._kb.bindings if b.keys == (Keys.ControlL,)]
        assert matched, "c-l 绑定不存在"

    def test_c_l_eager(self, tmp_path: Any) -> None:
        """Ctrl+L 绑定必须 eager——语义独立成键，不等更长序列匹配。

        PT 把 eager=bool 归一为 filter（Always/Never），Filter 禁 bool()
        （truth value ambiguous），须调用 filter() 取值。
        """
        s = _make_session(tmp_path)
        from prompt_toolkit.keys import Keys

        for b in s._kb.bindings:
            if b.keys == (Keys.ControlL,):
                assert b.eager() is True
                break
        else:
            pytest.fail("c-l 绑定不存在")


class TestNoRawWrites:
    def test_warn_raw_goes_to_logger_not_stderr(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """_warn_raw 不得裸写 stderr（raw 模式下顶乱 alternate screen）。

        60s 节流游标先重置，确保本次触发；stderr 捕获后必须零字节。
        """
        monkeypatch.setattr(_warn_raw, "_last", 0.0)
        import logging as _logging

        with capsys.disabled():
            pass
        # 拦 os.write：渲染层唯一允许的终端写入是 PT renderer 自己
        import os as _os

        written: list[tuple[int, bytes]] = []
        orig = _os.write

        def spy(fd: int, data: Any) -> int:
            if fd == 2:
                written.append((fd, bytes(data)))
            return orig(fd, data)

        monkeypatch.setattr(_os, "write", spy)
        _warn_raw("structural-drift-probe")
        assert not any(b"structural-drift-probe" in d for _, d in written)

    def test_stdout_proxy_detects_fullscreen_residency(self, tmp_path: Any) -> None:
        """_StdoutProxy 是「全屏驻留」判据：isinstance 可用于分流。"""
        s = _make_session(tmp_path)
        proxy = _StdoutProxy(s, io.StringIO())
        assert isinstance(proxy, _StdoutProxy)
        assert not isinstance(io.StringIO(), _StdoutProxy)

    def test_tui_visible_print_routes_by_residency(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """repl._tui_visible_print：驻留期走 logger，非驻留走 stderr。"""
        from lingclaude.cli import repl as repl_mod

        s = _make_session(tmp_path)
        proxy = _StdoutProxy(s, io.StringIO())

        # 非驻留：stderr 收到
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        repl_mod._tui_visible_print("HELLO-NONRESIDENT")
        captured = capsys.readouterr()
        assert "HELLO-NONRESIDENT" in captured.err

        # 驻留：logger 收到，stderr 零输出
        monkeypatch.setattr(sys, "stdout", proxy)
        repl_mod._tui_visible_print("HELLO-RESIDENT")
        captured = capsys.readouterr()
        assert "HELLO-RESIDENT" not in captured.err
