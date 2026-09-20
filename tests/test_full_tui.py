"""P2 全屏 TUI 会话测试（Q4，2026-09-15）。

覆盖（不依赖真实 TTY / 全屏渲染，测纯逻辑）：
1. FullTuiSession 构造（PT 缺失时抛 RuntimeError）
2. 输出源注入 + 输出窗重绘（行数截断 MAX_OUTPUT_LINES）
3. 状态栏回调注册 + 片段渲染
4. 历史文件写入（FileHistory 兼容）
5. 输出源格式化（用户/助手前缀）
6. create_session 选择：LINGCLAUDE_TUI=2 → FullTuiSession（mock PT 可用）
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lingclaude.cli import interface
from lingclaude.cli.full_tui import (
    EOF_SENTINEL,
    MAX_OUTPUT_LINES,
    FullTuiSession,
    _StdoutProxy,
)


@pytest.fixture()
def _pt_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """mock prompt_toolkit 可用（构造不抛）。"""
    monkeypatch.setattr("lingclaude.cli.full_tui._HAS_PROMPT_TOOLKIT", True)


class TestFullTuiSession:
    def test_construct_no_pt_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("lingclaude.cli.full_tui._HAS_PROMPT_TOOLKIT", False)
        with pytest.raises(RuntimeError):
            FullTuiSession()  # type: ignore[call-arg]

    def test_construct_with_history_file(self, _pt_available: None, tmp_path: Path) -> None:
        hf = str(tmp_path / "hist")
        s = FullTuiSession(history_file=hf)
        assert s._history is not None  # noqa: SLF001
        assert isinstance(s.interrupt_event().is_set(), bool)

    def test_input_multiline_no_truncation(self, _pt_available: None, tmp_path: Path) -> None:
        """长文截断修复（2026-09-16）：全屏输入框多行模式开启。

        回归锁定：FullTuiSession 输入框 multiline=True（粘贴长文不被
        截断成第一行），且 Enter 提交 / Esc+Enter 换行 / Ctrl+C / Ctrl+D
        键位全部保留。
        """
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        # 输入框 buffer 的 multiline filter 求值为 True
        ml = s._input_area.buffer.multiline  # noqa: SLF001
        assert (ml() if callable(ml) else bool(ml)) is True

        kb = s._kb  # noqa: SLF001
        keys_desc = [str(k) for k in {b.keys: b.handler for b in kb.bindings}.keys()]
        assert any("ControlM" in k for k in keys_desc), "Enter 提交绑定缺失"
        assert any(
            "Escape" in k and "ControlM" in k for k in keys_desc
        ), "Esc+Enter 换行绑定缺失"
        assert any("c-c" in k or "ControlC" in k for k in keys_desc), "Ctrl+C 缺失"
        assert any("c-d" in k or "ControlD" in k for k in keys_desc), "Ctrl+D 缺失"

    def test_output_source_injection_and_truncation(
        self, _pt_available: None, tmp_path: Path
    ) -> None:
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        # 超过 MAX_OUTPUT_LINES 行 → 截断保留最后 MAX
        lines = [f"line-{i}" for i in range(MAX_OUTPUT_LINES + 50)]
        s.install_output_source(lambda: lines)
        s._refresh_output_area()  # noqa: SLF001
        assert s._out_buffer.text.count("\n") >= MAX_OUTPUT_LINES - 1
        assert "line-0" not in s._out_buffer.text
        assert f"line-{MAX_OUTPUT_LINES + 49}" in s._out_buffer.text

    def test_status_callback_fragments(self, _pt_available: None, tmp_path: Path) -> None:
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        s.install_bottom_toolbar(lambda: [("class:accent", "model-x"), ("", "│ /tmp")])
        frags = s._status_fragments()  # noqa: SLF001
        assert isinstance(frags, list)
        assert "".join(x[1] for x in frags) == "model-x│ /tmp"

    def test_status_callback_error_silent(self, _pt_available: None, tmp_path: Path) -> None:
        s = FullTuiSession(history_file=str(tmp_path / "h"))

        def _boom() -> list[tuple[str, str]]:
            raise RuntimeError("boom")

        s.install_bottom_toolbar(_boom)
        assert s._status_fragments() == []  # noqa: SLF001

    def test_output_source_exception_silent(self, _pt_available: None, tmp_path: Path) -> None:
        s = FullTuiSession(history_file=str(tmp_path / "h"))

        def _boom() -> list[str]:
            raise RuntimeError("boom")

        s.install_output_source(_boom)  # type: ignore[arg-type]
        s._refresh_output_area()  # noqa: SLF001
        assert s._out_buffer.text == ""

    def test_push_to_history_noop(self, _pt_available: None, tmp_path: Path) -> None:
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        s.push_to_history("hello")  # 不抛即可
        assert True


class TestCreateSessionSelection:
    """create_session 的 LINGCLAUDE_TUI=2 分支（mock PT 可用 + isatty）。"""

    def _patch_env_tty(self, monkeypatch: pytest.MonkeyPatch, tui: str) -> None:
        monkeypatch.setenv("LINGCLAUDE_TUI", tui)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(interface, "_HAS_PROMPT_TOOLKIT", True)

    def test_tui2_returns_full_tui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_env_tty(monkeypatch, "2")
        s = interface.create_session()
        assert isinstance(s, FullTuiSession)

    def test_tui0_returns_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_env_tty(monkeypatch, "0")
        s = interface.create_session()
        from lingclaude.cli.interface import FallbackSession

        assert isinstance(s, FallbackSession)

    def test_default_returns_pt_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_env_tty(monkeypatch, "")  # 未设置
        monkeypatch.delenv("LINGCLAUDE_CLI_MODE", raising=False)
        s = interface.create_session()
        from lingclaude.cli.interface import PromptToolkitSession

        assert isinstance(s, PromptToolkitSession)

    def test_plain_mode_returns_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_env_tty(monkeypatch, "")
        monkeypatch.setenv("LINGCLAUDE_CLI_MODE", "plain")
        s = interface.create_session()
        from lingclaude.cli.interface import FallbackSession

        assert isinstance(s, FallbackSession)

    def test_tui2_full_tui_fallback_on_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LINGCLAUDE_TUI=2 但 FullTuiSession 构造失败 → 降级 PromptToolkitSession。"""
        self._patch_env_tty(monkeypatch, "2")
        import lingclaude.cli.interface as iface

        class _FakeFullTui:
            def __init__(self, *a: Any, **k: Any) -> None:
                raise RuntimeError("PT missing")

        monkeypatch.setitem(sys.modules, "lingclaude.cli.full_tui", _FakeFullTui)
        s = iface.create_session()
        from lingclaude.cli.interface import PromptToolkitSession

        assert isinstance(s, PromptToolkitSession)

class TestReplWiring:
    """repl.py 的 FullTui 接线点（helper 化后可直接测纯逻辑）。"""

    def test_is_full_tui_session_true(self, _pt_available: None, tmp_path: Path) -> None:
        from lingclaude.cli.repl import _is_full_tui_session

        s = FullTuiSession(history_file=str(tmp_path / "h"))
        assert _is_full_tui_session(s) is True

    def test_is_full_tui_session_false_for_pt(self) -> None:
        from lingclaude.cli.repl import _is_full_tui_session

        # 非 FullTui（如普通对象）→ False，即使 PT 已装
        assert _is_full_tui_session(object()) is False

    def test_is_full_tui_session_false_on_import_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import builtins
        from lingclaude.cli import repl

        real_import = builtins.__import__

        def _fake_import(name: str, *a: Any, **k: Any) -> Any:
            if name == "lingclaude.cli.full_tui":
                raise ImportError("simulate missing")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        assert repl._is_full_tui_session(object()) is False  # noqa: SLF001

    def test_output_source_formats_roles(self, tmp_path: Path) -> None:
        from lingclaude.cli.repl import _full_tui_output_source

        class _Msg:
            def __init__(self, role: str, content: str) -> None:
                self.role = role
                self.content = content

        engine = SimpleNamespace(
            _messages=[
                _Msg("user", "你好"),
                _Msg("assistant", "你好！有什么可以帮你？"),
                _Msg("", "系统消息"),
                {"role": "user", "content": "dict 消息"},
                _Msg("user", ""),
            ]
        )
        lines = _full_tui_output_source(engine)()
        assert lines == [
            "🧑 用户: 你好",
            "🤖 灵克: 你好！有什么可以帮你？",
            "系统消息",
            "🧑 用户: dict 消息",
        ]

    def test_output_source_empty_messages(self, tmp_path: Path) -> None:
        from lingclaude.cli.repl import _full_tui_output_source

        assert _full_tui_output_source(SimpleNamespace(_messages=[]))() == []
        # 无 _messages 属性也安全
        assert _full_tui_output_source(SimpleNamespace())() == []


class TestResidentFullTui:
    """常驻全屏形态（2026-09-16 v2 重写）——纯逻辑测试，不依赖真实终端。

    覆盖：提交队列 FIFO / EOF 哨兵 / Ctrl+C 软中断（空闲 vs 流式）/
    stdout 代理逐行路由 / 输出窗上限截断 / close 后 prompt 优雅 EOF /
    未启动降级 input()。
    """

    def _make(self, tmp_path: Path, started: bool = False) -> FullTuiSession:
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        if started:
            s._ever_started = True  # noqa: SLF001 — 模拟已启动（不真开全屏）
        return s

    def test_submit_fifo_and_pending(self, _pt_available: None, tmp_path: Path) -> None:
        s = self._make(tmp_path, started=True)
        s._submit("第一行")
        s._submit("第二行")
        s._submit(EOF_SENTINEL)
        assert s.pending_submissions() == 2
        assert s.prompt() == "第一行"
        assert s.prompt() == "第二行"
        assert s.pending_submissions() == 0
        with pytest.raises(EOFError):
            s.prompt()
        assert s.pending_submissions() == 0

    def test_prompt_eof_sentinel_raises(self, _pt_available: None, tmp_path: Path) -> None:
        s = self._make(tmp_path, started=True)
        s._submit(EOF_SENTINEL)
        with pytest.raises(EOFError):
            s.prompt()

    def test_idle_interrupt_cleared_and_empty(self, _pt_available: None, tmp_path: Path) -> None:
        """空闲期（streaming=False）Ctrl+C → prompt 返回 "" 且事件被清。"""
        s = self._make(tmp_path, started=True)
        s._interrupt.set()
        assert s.prompt() == ""
        assert not s._interrupt.is_set()  # noqa: SLF001

    def test_streaming_interrupt_not_consumed(self, _pt_available: None, tmp_path: Path) -> None:
        """流式期 Ctrl+C → prompt 不消费 interrupt（打断归流循环检查）。"""
        s = self._make(tmp_path, started=True)
        s.set_streaming(True)
        s._interrupt.set()
        s._submit("稍后处理")
        # prompt 返回提交内容，事件保持 set —— 流循环才能看到打断
        assert s.prompt() == "稍后处理"
        assert s._interrupt.is_set()  # noqa: SLF001
        s.set_streaming(False)
        assert s.prompt() == ""  # 空闲期消费 interrupt

    def test_stdout_proxy_routes_lines(self, _pt_available: None, tmp_path: Path) -> None:
        """print / sys.stdout.write 按行追加进输出窗。

        \r（进度式覆写）语义：丢弃当前半行 —— 中和 repl 的 " "*40+"\r"
        清列技巧（否则输出窗出现 40 空格行）。
        """
        import io as _io

        s = self._make(tmp_path)
        proxy = _StdoutProxy(s, _io.StringIO())
        sys.stdout, real = proxy, sys.stdout
        try:
            print("第一行")
            print("第二行", end="")
            proxy.write("\n")
            proxy.write(" " * 40 + "\r")  # 清列技巧：应被完全中和
            proxy.write("第三行\n")
            proxy.flush()
        finally:
            sys.stdout = real
        text = s._out_buffer.text  # noqa: SLF001
        assert "第一行" in text
        assert "第二行" in text
        assert "第三行" in text
        assert "\r" not in text
        # 40 空格清列不应产生纯空白行
        assert not any(line and not line.strip() for line in text.split("\n"))

    def test_output_area_line_cap(self, _pt_available: None, tmp_path: Path) -> None:
        s = self._make(tmp_path)
        s._append_output_lines([f"l{i}" for i in range(MAX_OUTPUT_LINES + 50)])
        lines = s._out_buffer.text.split("\n")  # noqa: SLF001
        assert len(lines) == MAX_OUTPUT_LINES
        assert lines[0] == "l50"  # 最旧的 50 行被丢弃

    def test_close_then_prompt_raises_eof(self, _pt_available: None, tmp_path: Path) -> None:
        """close 后 prompt 必须 EOFError（不得回退 input() 与终端抢读）。"""
        s = self._make(tmp_path)
        s._ever_started = True  # 模拟已启动（不真开全屏，避免 CI 无 tty）
        s.close()
        with pytest.raises(EOFError):
            s.prompt()

    def test_never_started_prompt_falls_back(self, _pt_available: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """从未启动 → 降级裸 input()（P1 逃生语义）。"""
        s = self._make(tmp_path)
        monkeypatch.setattr("builtins.input", lambda _msg="": "降级输入")
        assert s.prompt("灵克> ") == "降级输入"

    def test_start_close_lifecycle(self, _pt_available: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """start/close 生命周期：stdout 被代理接管又还原（无头环境 app.run
        可能失败，_run_app 异常路径也要保证 stdout 还原）。"""
        s = self._make(tmp_path)
        real_stdout = sys.stdout
        try:
            s.start()
        except Exception:  # noqa: BLE001 — 无头环境 app 构造/线程失败不视为断言失败
            sys.stdout = real_stdout
            pytest.skip("无头环境无法启动全屏 Application")
        # app 线程在无头环境可能立即死亡并还原 stdout —— 两种状态皆合法
        assert sys.stdout is s._stdout_proxy or s._stdout_proxy is None  # noqa: SLF001
        s.close()
        assert sys.stdout is real_stdout  # 收尾不变量：stdout 必还原
        assert s._stdout_proxy is None  # noqa: SLF001

    def test_set_streaming_flag(self, _pt_available: None, tmp_path: Path) -> None:
        s = self._make(tmp_path)
        assert s._streaming is False  # noqa: SLF001
        s.set_streaming(True)
        assert s._streaming is True  # noqa: SLF001

    # ── 输出历史滚动（2026-09-19）──

    def test_scroll_lines_moves_cursor_and_mode(self, _pt_available: None, tmp_path: Path) -> None:
        """Shift+↑↓（_scroll_out_lines）：光标行移动 + 跟随/回看模式切换。"""
        s = self._make(tmp_path)
        s._set_output_lines([f"row-{i}" for i in range(20)])  # noqa: SLF001
        assert s._follow_output is True  # noqa: SLF001
        def _row() -> int:
            return s._out_buffer.document.cursor_position_row  # noqa: SLF001

        s._scroll_out_lines(-5)  # noqa: SLF001
        assert _row() == 14
        assert s._follow_output is False  # noqa: SLF001 回看模式
        s._scroll_out_lines(-100)  # noqa: SLF001 边界钳制到 0
        assert _row() == 0
        s._scroll_out_lines(+3)  # noqa: SLF001
        assert _row() == 3
        s._scroll_out_lines(+100)  # noqa: SLF001 滚到底=回到文末→跟随恢复
        assert _row() == 19
        assert s._follow_output is True  # noqa: SLF001
        # 空缓冲滚动不抛异常
        s._set_output_lines([])  # noqa: SLF001
        s._scroll_out_lines(-1)  # noqa: SLF001

    def test_append_output_follow_mode_pins_to_end(self, _pt_available: None, tmp_path: Path) -> None:
        """跟随模式下追加输出：光标钉文末；回看模式下光标行保持不变。"""
        s = self._make(tmp_path)
        s._set_output_lines([f"row-{i}" for i in range(30)])  # noqa: SLF001
        s._scroll_out_lines(-10)  # noqa: SLF001 → 回看，光标行 19
        s._append_output_lines(["new-1", "new-2"])  # noqa: SLF001
        assert s._out_buffer.document.cursor_position_row == 19, (  # noqa: SLF001
            "回看中光标行不得被新输出拽走"
        )
        assert "new-2" in s._out_buffer.text  # noqa: SLF001 内容照常追加
        assert s._follow_output is False  # noqa: SLF001
        # 恢复跟随后再追加 → 光标回文末
        s._scroll_out_lines(+100)  # noqa: SLF001
        s._append_output_lines(["new-3"])  # noqa: SLF001
        assert s._out_buffer.document.cursor_position_row == 32  # noqa: SLF001
        assert s._follow_output is True  # noqa: SLF001

    def test_append_output_trims_with_cursor_anchor_shift(self, _pt_available: None, tmp_path: Path) -> None:
        """回看中首部行被裁：光标锚点随裁剪量平移，视觉位置不漂移。"""
        s = self._make(tmp_path)
        s._set_output_lines([f"row-{i}" for i in range(MAX_OUTPUT_LINES)])  # noqa: SLF001
        s._scroll_out_lines(-(MAX_OUTPUT_LINES - 10))  # noqa: SLF001 光标行 9
        s._append_output_lines(["tail-1", "tail-2", "tail-3"])  # noqa: SLF001 → 裁 3 行
        assert s._out_buffer.document.cursor_position_row == 6  # noqa: SLF001 9-3
        assert s._follow_output is False  # noqa: SLF001

    def test_scroll_out_pages_uses_window_height(self, _pt_available: None, tmp_path: Path) -> None:
        """PageUp/PageDown：按渲染信息页高移动光标行；无渲染信息走兜底。"""
        s = self._make(tmp_path)
        s._set_output_lines([f"row-{i}" for i in range(100)])  # noqa: SLF001
        # 无 render_info（未首帧）→ 兜底页高 10 → 光标行 100-1-9=90
        s._scroll_out_pages(-1)  # noqa: SLF001
        assert s._out_buffer.document.cursor_position_row == 90  # noqa: SLF001  100-1-9
        # mock render_info 页高 5 → 移动 4 行
        s._scroll_out_lines(+2)  # noqa: SLF001 → 92
        info = SimpleNamespace(window_height=5)
        s._output_area.render_info = info  # noqa: SLF001
        s._scroll_out_pages(1)  # noqa: SLF001
        assert s._out_buffer.document.cursor_position_row == 96  # noqa: SLF001

    def test_scroll_keybindings_registered(self, _pt_available: None, tmp_path: Path) -> None:
        """滚动键位注册齐全：PageUp/PageDown/Shift+↑↓/Ctrl+Home/End。"""
        s = self._make(tmp_path)
        kb = s._kb  # noqa: SLF001
        registered = {b.keys for b in kb.bindings}
        from prompt_toolkit.keys import Keys

        for want in (
            (Keys.PageUp,),
            (Keys.PageDown,),
            (Keys.ShiftUp,),
            (Keys.ShiftDown,),
            (Keys.ControlHome,),
            (Keys.ControlEnd,),
            (Keys.ControlM,),
            (Keys.Escape, Keys.ControlM),
            (Keys.ControlC,),
            (Keys.ControlD,),
        ):
            assert want in registered, f"键位缺失: {want}"

    def test_output_control_is_custom_scroll_control(self, _pt_available: None, tmp_path: Path) -> None:
        """输出窗控件为 _OutputScrollControl（滚轮拦截生效的前提）。"""
        from lingclaude.cli.full_tui import _OutputScrollControl

        s = self._make(tmp_path)
        assert isinstance(s._out_control, _OutputScrollControl)  # noqa: SLF001
        assert s._output_area.content is s._out_control  # noqa: SLF001

    def test_wheel_callback_scrolls(self, _pt_available: None, tmp_path: Path) -> None:
        """滚轮回调路径：on_wheel(±1) 等价 _scroll_out_lines(±1)。"""
        s = self._make(tmp_path)
        s._set_output_lines([f"row-{i}" for i in range(30)])  # noqa: SLF001
        s._on_out_wheel(-3)  # noqa: SLF001
        assert s._out_buffer.document.cursor_position_row == 26  # noqa: SLF001
        assert s._follow_output is False  # noqa: SLF001
        s._on_out_wheel(+3)  # noqa: SLF001
        assert s._follow_output is True  # noqa: SLF001


class TestTuiOptimizationP0:
    """P0-2 清洗状态机（2026-09-20，方案 docs/cli/TUI_OPTIMIZATION_PLAN_20260920.md §四）。"""

    def test_strip_marker_split_chunks(self) -> None:
        # 方案验收: b"\x1b[200~ab" + b"cd\x1b[201~" 两片 → 提交文本 abcd
        from lingclaude.cli.interface import _fallback_strip_ansi

        a1, _h1, ip1 = _fallback_strip_ansi(b"\x1b[200~ab", False)
        a2, _h2, ip2 = _fallback_strip_ansi(b"cd\x1b[201~", ip1)
        assert (a1 + a2).decode() == "abcd"
        assert ip1 and not ip2  # 开/闭各翻转一次

    def test_strip_marker_bytes_split(self) -> None:
        # 方案验收: b"\x1b[200" + b"~ab" 标记本体拆片
        from lingclaude.cli.interface import _fallback_strip_ansi

        a1, h1, ip1 = _fallback_strip_ansi(b"\x1b[200", False)
        a2, h2, ip2 = _fallback_strip_ansi(h1 + b"~ab", ip1)
        assert (a1 + a2).decode() == "ab"
        assert ip2  # 翻转后处于粘贴态（段内 \n 为正文）
        assert not h2

    def test_strip_marker_mid_chunk(self) -> None:
        # 标记夹在正文中（旧 startswith 只认分片首位的漏剥场景）
        from lingclaude.cli.interface import _fallback_strip_ansi

        a1, _h, _ip = _fallback_strip_ansi(b"xx\x1b[200~yy\x1b[201~zz", False)
        assert a1.decode() == "xxyyzz"

    def test_strip_cpr_and_ss3_swallowed(self) -> None:
        # CPR 应答 \x1b[r;cR 与 SS3 方向键 \x1bOA 整体吞，不留残字节
        from lingclaude.cli.interface import _fallback_strip_ansi

        a1, h1, _ = _fallback_strip_ansi(b"he\x1b[27;1Rllo\x1bOA", False)
        assert a1 == b"hello" and h1 == b""

    def test_strip_incomplete_tail_hold(self) -> None:
        # 尾部不完整 CSI 扣下与下一分片拼接
        from lingclaude.cli.interface import _fallback_strip_ansi

        a1, h1, _ = _fallback_strip_ansi(b"ok\x1b[", False)
        a2, h2, _ = _fallback_strip_ansi(h1 + b"2qtail", False)
        assert (a1 + a2).decode() == "oktail" and not h2

    def test_ss3_split_across_chunks(self) -> None:
        # SS3 前缀 \x1bO 拆片（回归: 前缀表缺 \x1bO 时 'A' 漏成正文）
        from lingclaude.cli.interface import _fallback_strip_ansi

        a1, h1, _ = _fallback_strip_ansi(b"q\x1bO", False)
        a2, h2, _ = _fallback_strip_ansi(h1 + b"Az", False)
        assert (a1 + a2).decode() == "qz" and not h2

    def test_strip_utf8_multibyte_safe(self) -> None:
        # 多字节字符不落 0x40-0x7E，清洗不误伤正文
        from lingclaude.cli.interface import _fallback_strip_ansi

        a1, _h, _ = _fallback_strip_ansi("你\x1b[2q好".encode(), False)
        assert a1.decode("utf-8", errors="replace") == "你好"

    def test_sanitize_submitted_strips_markers(self) -> None:
        from lingclaude.cli.interface import _sanitize_submitted

        assert _sanitize_submitted("a\x1b[200~b\x1b[201~c") == "abc"
        assert _sanitize_submitted("正常文本") == "正常文本"


class TestTuiOptimizationP1:
    """P1-1/P1-2 序列映射 + P1-3 输出层清洗。"""

    def test_csi_u_enter_mapped(self) -> None:
        # kitty 残留模式 Enter（\x1b[27u）→ ControlM 提交（绝不能 Ignore）
        interface._patch_pt_modifier_enter()
        from prompt_toolkit.input import ansi_escape_sequences as aes
        from prompt_toolkit.keys import Keys

        assert aes.ANSI_SEQUENCES["\x1b[27u"] == Keys.ControlM
        assert aes.ANSI_SEQUENCES["\x1b[27;5u"] == (Keys.Escape, Keys.ControlM)

    def test_defensive_sequences_ignored(self) -> None:
        # P1-2: 焦点/DECSCUSR/DECRQM 应答 → Keys.Ignore 不进 buffer
        interface._patch_pt_modifier_enter()
        from prompt_toolkit.input import ansi_escape_sequences as aes
        from prompt_toolkit.keys import Keys

        assert aes.ANSI_SEQUENCES["\x1b[O"] == Keys.Ignore
        assert aes.ANSI_SEQUENCES["\x1b[2 q"] == Keys.Ignore
        assert aes.ANSI_SEQUENCES["\x1b[?2026;1$y"] == Keys.Ignore

    def test_stdout_proxy_strips_escape(self, _pt_available: None, tmp_path: Path) -> None:
        # P1-3: 残留 CSI 不进输出窗；不完整尾部扣住 flush 时丢弃
        got: list[str] = []
        owner = SimpleNamespace(_write_via_buffer=lambda s: got.append(s))
        proxy = _StdoutProxy(owner, sys.stdout)
        proxy.write("he\x1b[27;1Rllo\n")
        assert "".join(got) == "hello\n"
        proxy.write("tail\x1b[2")  # 不完整尾部
        proxy.flush()               # flush 丢弃扣住残骸
        assert "".join(got) == "hello\ntail\n"


class TestTuiOptimizationP2:
    """P2-1 /history + P2-2 行上限扩容。"""

    def test_history_command_consumed(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        from lingclaude.cli.commands import SlashCommandProcessor

        class _FakeMgr:
            save_dir = tmp_path

            def __init__(self) -> None:
                pass

            def list_sessions(self, project_path: str = ""):
                return ()

        monkeypatch.setattr("lingclaude.core.session.SessionManager", _FakeMgr)
        proc = SlashCommandProcessor(engine=SimpleNamespace(), status=SimpleNamespace())
        assert proc.handle("/history") is True
        assert "无会话记录" in capsys.readouterr().out

    def test_max_output_lines_expanded(self) -> None:
        # P2-2: 800 → 5000（长会话不再静默裁剪）
        assert MAX_OUTPUT_LINES == 5000
