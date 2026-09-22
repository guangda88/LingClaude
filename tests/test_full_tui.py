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

    def test_output_source_prefers_conversation(self, tmp_path: Path) -> None:
        """2026-09-21 输入回显配套：_conversation（带角色二元组）优先于 _messages。

        _messages 是纯字符串交替无角色信息 → 回放无法区分用户/灵克；
        _conversation 四条写路径全程 (role, text) → 回放可区分。
        """
        from lingclaude.cli.repl import _full_tui_output_source

        engine = SimpleNamespace(
            _messages=["用户问", "灵克答"],  # 旧源：无角色，应被跳过
            _conversation=[
                ("user", "用户问"),
                ("assistant", "灵克答"),
                ("system", "[L1交接刷新 @ msg#42]"),
                ("user", ""),  # 空文本跳过
            ],
        )
        lines = _full_tui_output_source(engine)()
        assert lines == [
            "🧑 用户: 用户问",
            "🤖 灵克: 灵克答",
            "[L1交接刷新 @ msg#42]",  # system 无前缀（内容自带标识）
        ]

    def test_output_source_conversation_fallback_shapes(self, tmp_path: Path) -> None:
        """_conversation 缺席 → 回退 _messages 旧逻辑（对象/dict/纯字符串）。"""
        from lingclaude.cli.repl import _full_tui_output_source

        class _Msg:
            def __init__(self, role: str, content: str) -> None:
                self.role = role
                self.content = content

        engine = SimpleNamespace(_messages=[_Msg("user", "对象消息")])
        assert _full_tui_output_source(engine)() == ["🧑 用户: 对象消息"]
        # _conversation 为空列表 → 也走 _messages 回退
        engine2 = SimpleNamespace(_conversation=[], _messages=[
            {"role": "user", "content": "dict 消息"},
        ])
        assert _full_tui_output_source(engine2)() == ["🧑 用户: dict 消息"]
        # 恢复路径产生的非二元组项：防御性降级为无角色原样
        engine3 = SimpleNamespace(_conversation=["裸字符串项"])
        assert _full_tui_output_source(engine3)() == ["裸字符串项"]


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

    def test_accept_echoes_input_to_output_area(self, _pt_available: None, tmp_path: Path) -> None:
        """2026-09-21 输入回显：_on_accept 提交的输入以 "> " 前缀进输出窗。

        此前提交后输出窗无痕，用户输入与模型回复在回放里无法区分。
        """
        s = self._make(tmp_path, started=True)

        class _Buf:
            text = "帮我看看 repl.py"

        assert s._on_accept(_Buf()) is False  # 返回 False 交给 PT reset 清 buffer
        text = s._out_buffer.text  # noqa: SLF001
        assert "> 帮我看看 repl.py" in text
        # 回显不影响提交主路径：文本仍进提交队列
        assert s.pending_submissions() == 1  # noqa: SLF001
        assert s.prompt() == "帮我看看 repl.py"

    def test_accept_empty_text_no_echo(self, _pt_available: None, tmp_path: Path) -> None:
        """空文本 accept：不回显、不入队（与旧行为一致）。"""
        s = self._make(tmp_path, started=True)

        class _Buf:
            text = ""

        assert s._on_accept(_Buf()) is False
        assert s._out_buffer.text == ""  # noqa: SLF001
        assert s.pending_submissions() == 0  # noqa: SLF001

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

class TestPasteFolding:
    """长文本粘贴折叠（2026-09-21）：≥阈值行粘贴 → 占位符，提交时还原全文。

    纯逻辑测试：不依赖真实 TTY / Application，直接驱动
    _register_paste / _expand_placeholders / _fold_echo / _on_accept。
    """

    LONG = "\n".join(f"line{i}" for i in range(10))  # 10 行

    def _make(self, tmp_path: Path, started: bool = False) -> FullTuiSession:
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        if started:
            s._ever_started = True  # noqa: SLF001 — 模拟已启动（不真开全屏）
        return s

    def test_short_paste_passthrough(self, _pt_available: None, tmp_path: Path) -> None:
        """短粘贴（<阈值）原样返回，不产生占位符。"""
        s = self._make(tmp_path)
        insert, n = s._register_paste("a\nb\nc")  # noqa: SLF001
        assert insert == "a\nb\nc"
        assert n == 3
        assert s._paste_registry == {}  # noqa: SLF001

    def test_long_paste_folds_to_placeholder(self, _pt_available: None, tmp_path: Path) -> None:
        """长粘贴折叠为占位符并登记全文（编号单调递增）。"""
        s = self._make(tmp_path)
        ph, n = s._register_paste(self.LONG)  # noqa: SLF001
        assert ph == f"[文本块 #1 · 10行 · {len(self.LONG)}字符]"
        assert n == 10
        assert s._paste_registry[1] == (self.LONG, 10)  # noqa: SLF001

    def test_crlf_normalized_before_counting(self, _pt_available: None, tmp_path: Path) -> None:
        """\\r\\n 粘贴归一为 \\n 后再计数折叠（iTerm2 形态）。"""
        s = self._make(tmp_path)
        ph, n = s._register_paste("a\r\nb\r\nc\r\nd\r\ne\r\nf\r\ng")  # noqa: SLF001
        assert n == 7
        assert "\r" not in ph
        assert s._paste_registry[1][0] == "a\nb\nc\nd\ne\nf\ng"  # noqa: SLF001

    def test_expand_restores_full_text(self, _pt_available: None, tmp_path: Path) -> None:
        """提交还原：占位符 → 登记全文，前后缀保留。"""
        s = self._make(tmp_path)
        ph, _ = s._register_paste(self.LONG)  # noqa: SLF001
        expanded = s._expand_placeholders(f"前缀 {ph} 后缀")  # noqa: SLF001
        assert expanded == f"前缀 {self.LONG} 后缀"

    def test_hand_typed_lookalike_not_expanded(self, _pt_available: None, tmp_path: Path) -> None:
        """手打同形字面串（编号未登记）不被误还原。"""
        s = self._make(tmp_path)
        fake = "[文本块 #99 · 10行 · 40字符]"
        assert s._expand_placeholders(f"x {fake} y") == f"x {fake} y"  # noqa: SLF001

    def test_fold_echo_round_trip(self, _pt_available: None, tmp_path: Path) -> None:
        """回显折叠：还原后的全文重新折回占位符（与输入框所见一致）。"""
        s = self._make(tmp_path)
        ph, _ = s._register_paste(self.LONG)  # noqa: SLF001
        full = s._expand_placeholders(f"问题：{ph}")  # noqa: SLF001
        assert s._fold_echo(full) == f"问题：{ph}"  # noqa: SLF001

    def test_fold_echo_longest_first(self, _pt_available: None, tmp_path: Path) -> None:
        """超集粘贴：按长度降序替换——先短后长会把长粘贴内部截断串位。"""
        s = self._make(tmp_path)
        short = "x\ny\nz\n1\n2\n3"  # 恰 6 行（达阈值）
        long_text = short + "\nw\n4\n5\n6"  # 10 行，包含 short 为子串
        ph_s, _ = s._register_paste(short)  # noqa: SLF001
        ph_l, _ = s._register_paste(long_text)  # noqa: SLF001
        assert s._fold_echo(long_text) == ph_l  # noqa: SLF001
        assert ph_s not in s._fold_echo(long_text)  # noqa: SLF001

    def test_accept_expands_and_submits_full_text(
        self, _pt_available: None, tmp_path: Path
    ) -> None:
        """accept：占位符还原全文进提交队列；回显保持占位符形态防刷屏。"""
        s = self._make(tmp_path, started=True)
        ph, _ = s._register_paste(self.LONG)  # noqa: SLF001

        class _Buf:
            text = f"看看这段：{ph}"

        assert s._on_accept(_Buf()) is False  # noqa: SLF001
        assert s.pending_submissions() == 1
        assert s.prompt() == f"看看这段：{self.LONG}"  # 队列里是全文
        out = s._out_buffer.text  # noqa: SLF001
        assert ph in out  # 回显折叠为占位符
        assert "line9" not in out  # 粘贴全文不进输出窗


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


class TestTuiBurstAndMulti:
    """P0-5 粘贴爆发重组器 + P1-4 /multi 多行模式（2026-09-20）。

    根因：窗口期（生成结束→下轮 prompt 启动）终端无 bracketed paste 包裹，
    粘贴多行以裸 \\n 进内核行缓冲 → 下轮逐行解释成多轮提交（用户报告
    「输入遇到换行符即传到 LLM，多行文被截为多轮单行命令」）。
    """

    class _FakeStdin:
        """pytest 的 sys.stdin 无 fileno()（DontReadFromInput）→ 函数 except
        分支会早退。整体替换为带 fileno 的假对象，真实走完 select 探测路径。"""

        def __init__(self, tty: bool = True) -> None:
            self._tty = tty

        def isatty(self) -> bool:
            return self._tty

        def fileno(self) -> int:
            return 0

    @staticmethod
    def _icanon() -> int:
        import termios

        return termios.ICANON

    # ---- P0-5 重组器 ------------------------------------------------------

    def test_reconcile_merges_burst(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from lingclaude.cli import interface as iface

        monkeypatch.setattr(iface.sys, "stdin", self._FakeStdin(True))
        calls = {"n": 0}

        def fake_select(*_a, **_k):
            calls["n"] += 1
            return ([1], [], []) if calls["n"] <= 2 else ([], [], [])

        lines = iter(["第二行\n", "第三行\n"])
        out = iface._reconcile_burst_lines(
            "首行",
            _select=fake_select,
            _readline=lambda: next(lines),
            _tcgetattr=lambda fd: [0, 0, 0, self._icanon()],
        )
        assert out == "首行\n第二行\n第三行"

    def test_reconcile_silent_gap_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from lingclaude.cli import interface as iface

        monkeypatch.setattr(iface.sys, "stdin", self._FakeStdin(True))

        def _boom():
            raise AssertionError("静默间隔后不应继续读")

        out = iface._reconcile_burst_lines(
            "单行",
            _select=lambda *_a: ([], [], []),
            _readline=_boom,
            _tcgetattr=lambda fd: [0, 0, 0, self._icanon()],
        )
        assert out == "单行"

    def test_reconcile_raw_mode_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from lingclaude.cli import interface as iface

        monkeypatch.setattr(iface.sys, "stdin", self._FakeStdin(True))

        def _boom():
            raise AssertionError("raw 模式下禁碰（PT 事件循环自理）")

        out = iface._reconcile_burst_lines(
            "raw行",
            _select=lambda *_a: ([1], [], []),
            _readline=_boom,
            _tcgetattr=lambda fd: [0, 0, 0, 0],
        )
        assert out == "raw行"

    def test_reconcile_not_tty_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from lingclaude.cli import interface as iface

        monkeypatch.setattr(iface.sys, "stdin", self._FakeStdin(False))
        assert iface._reconcile_burst_lines("非tty") == "非tty"

    def test_reconcile_line_cap_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from lingclaude.cli import interface as iface

        monkeypatch.setattr(iface.sys, "stdin", self._FakeStdin(True))
        n = {"i": 0}

        def endless_readline():
            n["i"] += 1
            return f"L{n['i']}\n"

        out = iface._reconcile_burst_lines(
            "L0",
            _select=lambda *_a: ([1], [], []),
            _readline=endless_readline,
            _tcgetattr=lambda fd: [0, 0, 0, self._icanon()],
        )
        assert out.count("\n") + 1 == iface._BURST_MAX_LINES

    def test_reconcile_env_kill_switch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from lingclaude.cli import interface as iface

        monkeypatch.setattr(iface, "_BURST_GAP_MS", 0.0)
        monkeypatch.setattr(iface.sys, "stdin", self._FakeStdin(True))
        assert iface._reconcile_burst_lines("关闭重组") == "关闭重组"

    # ---- P1-4 /multi ------------------------------------------------------

    def test_multi_submits_joined_text(self) -> None:
        from lingclaude.cli.commands import SlashCommandProcessor

        captured: dict = {}
        proc = SlashCommandProcessor(
            SimpleNamespace(), SimpleNamespace(),
            reader=iter(["第一行", "第二行", "."]).__next__,
            submit=lambda t: captured.setdefault("text", t),
        )
        assert proc.handle("/multi") is True
        assert captured["text"] == "第一行\n第二行"

    def test_multi_eof_aborts_safely(self) -> None:
        from lingclaude.cli.commands import SlashCommandProcessor

        def _no_submit(_t):
            raise AssertionError("EOF 放弃路径不应提交")

        proc = SlashCommandProcessor(
            SimpleNamespace(), SimpleNamespace(),
            reader=iter(["line1"]).__next__,
            submit=_no_submit,
        )
        assert proc.handle("/multi") is True

    def test_multi_missing_reader_safe(self) -> None:
        from lingclaude.cli.commands import SlashCommandProcessor

        proc = SlashCommandProcessor(SimpleNamespace(), SimpleNamespace())
        assert proc.handle("/multi") is True

    def test_multi_quit_priority_untouched(self) -> None:
        from lingclaude.cli.commands import SlashCommandProcessor

        proc = SlashCommandProcessor(SimpleNamespace(), SimpleNamespace())
        assert proc.handle("/quit") is True
        assert proc.quit_requested is True

    # ---- 接线守卫 ----------------------------------------------------------

    def test_wiring_burst_hooks_and_multi_injection(self) -> None:
        """P0-5 五个入口 hook 与 P1-4 repl 注入必须真实接线（防回归删线）。"""
        root = Path(__file__).resolve().parent.parent
        iface_src = (root / "lingclaude" / "cli" / "interface.py").read_text(encoding="utf-8")
        # def 本体 + 5 个入口 hook（PT prompt/collect、Fallback streaming/阻塞、泵收集）
        assert iface_src.count("_reconcile_burst_lines(") >= 6
        repl_src = (root / "lingclaude" / "cli" / "repl.py").read_text(encoding="utf-8")
        assert "reader=lambda: _next_input(ctx)" in repl_src
        assert 'submit=lambda t: setattr(ctx, "queued_next", t)' in repl_src


class TestStripAnsiText:
    """输出侧 ANSI 清洗（_write_via_buffer 汇聚点，2026-09-20）。

    症状：模型回复内嵌 rich SGR 序列落 TextArea，0x1b 被渲染成 '?'，
    再漏出 '[1;4m' 明文噪声。快速路径须同时排除裸 C0（修复回归）。
    """

    @staticmethod
    def _f():
        from lingclaude.cli.interface import _strip_ansi_text
        return _strip_ansi_text

    def test_sgr_bold_underline(self):
        assert self._f()("\x1b[1;4m标题\x1b[0m 正文") == "标题 正文"

    def test_sgr_256color(self):
        assert self._f()("\x1b[48;5;235m \x1b[0m\x1b[38;5;81mif\x1b[0m") == " if"

    def test_truncated_csi_swallowed(self):
        assert self._f()("AB\x1b[1;4") == "AB"

    def test_ss3_swallowed(self):
        assert self._f()("x\x1bOAy") == "xy"

    def test_bare_c0_replaced(self):
        assert self._f()("a\x00b\x07c\nd\te") == "a b c\nd\te"

    def test_bare_c0_no_esc_not_fastpath(self):
        # 2026-09-20 修复回归：无 ESC 但含 C0 不得走快速路径直通
        assert self._f()("a\x00b") == "a b"

    def test_plain_passthrough(self):
        assert self._f()("plain text 中文") == "plain text 中文"

    def test_bracketed_paste_marks(self):
        assert self._f()("\x1b[200~abc\x1b[201~") == "abc"

    def test_query_sequences(self):
        assert self._f()("box\x1b[?25l\x1b[2 q\x1b[6nend") == "boxend"

    def test_empty(self):
        assert self._f()("") == ""


class TestAnsiStripSecondPath:
    """乱码修复第二/三路径回归（2026-09-20）。

    症状二：重启后历史回放（_refresh_output_area → _set_output_lines）
    把 transcript 里带 SGR 的模型回复原样灌进 TextArea，0x1b 渲染成
    '?' 再漏 '[1;4m' 明文。
    症状三：repl_io._stream_write 非托管期裸写分支不清洗。
    """

    def test_history_replay_strips_sgr(self, _pt_available: None, tmp_path: Path) -> None:
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        s._set_output_lines(["plain", "\x1b[1;4m标题\x1b[0m 正文", "\x1b[48;5;235m x\x1b[0m"])
        text = s._out_buffer.text  # noqa: SLF001
        assert "标题 正文" in text
        assert "\x1b" not in text
        assert "[1;4m" not in text

    def test_stream_write_unbridged_strips_sgr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import io as _io

        from lingclaude.cli import repl_io

        repl_io.set_stream_bridged(False)
        buf = _io.StringIO()
        monkeypatch.setattr(sys, "stdout", buf)
        repl_io._stream_write("\x1b[1;4m加粗\x1b[0m 正文\x1b[48;5;235m")
        assert buf.getvalue() == "加粗 正文"


class TestAnsiStripFourthPath:
    """乱码修复第四路径回归（2026-09-20）。

    症状：done 事件在 TTY 下调 print_markdown(content) 用 rich 重渲染
    「正式版」，display.py Console(stderr=True) 使带 SGR 的渲染结果直达
    真实终端（stderr 绕开 stdout 清洗链），全屏 TextArea 里 0x1b → '?'，
    参数 '[48;5;235m' 漏成明文。修复：全屏 TUI 托管期跳过重渲染。
    """

    def test_done_skips_markdown_when_full_tui_managed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import io as _io

        from lingclaude.cli import repl_io

        repl_io.set_full_tui_managed(True)
        repl_io.set_stream_bridged(False)
        buf = _io.StringIO()
        calls: list[str] = []

        class _SpyTty(_io.StringIO):
            def isatty(self) -> bool:
                return True

        fake = _SpyTty()
        monkeypatch.setattr(sys, "stdout", fake)
        monkeypatch.setattr(
            "lingclaude.cli.render_facade.print_markdown",
            lambda text: calls.append(text),
        )
        try:
            repl_io._handle_stream_event(
                {"type": "done", "content": "\x1b[1;4m标题\x1b[0m 正文"}
            )
        finally:
            repl_io.set_full_tui_managed(False)
        assert calls == []  # 全屏托管：rich 重渲染被跳过
        assert "标题" not in fake.getvalue()  # content 不再经任何通道落 stdout

    def test_done_renders_markdown_when_not_managed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import io as _io

        from lingclaude.cli import repl_io

        repl_io.set_full_tui_managed(False)
        repl_io.set_stream_bridged(False)
        calls: list[str] = []

        class _SpyTty(_io.StringIO):
            def isatty(self) -> bool:
                return True

        fake = _SpyTty()
        monkeypatch.setattr(sys, "stdout", fake)
        # 彩色 opt-in 模式：保留「底部追加正式版」路径（2026-09-22 双输出修复）
        monkeypatch.setenv("LINGCLAUDE_COLOR", "1")
        monkeypatch.setattr(
            "lingclaude.cli.render_facade.print_markdown",
            lambda text: calls.append(text),
        )
        repl_io._handle_stream_event({"type": "done", "content": "# 标题"})
        assert calls == ["# 标题"]  # 彩色 opt-in：正版渲染保留

    def test_done_plain_mode_skips_rerender(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """2026-09-22 双输出修复：纯文本模式下 done 不再重渲染正式版。

        背景：纯文本化后流式 delta 已写全文，「正式版」与裸文本内容完全
        相同，重渲染 = 内容输出两遍（用户实测确认）。纯文本模式下 done
        只补空行收尾；渲染函数零调用。
        """

        import io as _io

        from lingclaude.cli import repl_io

        repl_io.set_full_tui_managed(False)
        repl_io.set_stream_bridged(False)
        calls: list[str] = []

        class _SpyTty(_io.StringIO):
            def isatty(self) -> bool:
                return True

        fake = _SpyTty()
        monkeypatch.setattr(sys, "stdout", fake)
        monkeypatch.delenv("LINGCLAUDE_COLOR", raising=False)
        monkeypatch.setattr(
            "lingclaude.cli.render_facade.print_markdown",
            lambda text: calls.append(text),
        )
        repl_io._handle_stream_event({"type": "done", "content": "# 标题"})
        assert calls == []  # 纯文本：重渲染跳过
        assert "# 标题" not in fake.getvalue()  # content 不再二次输出
        assert fake.getvalue() == "\n\n"  # 只补空行收尾


class TestAtomcodeP123:
    """atomcode 三借鉴回归（2026-09-20）：inflight 快照 / 单一输出 owner / resync。"""

    # ── P1: turn_start 即落 checkpoint（round=-1）──

    def test_stream_turn_start_writes_checkpoint(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """流式 turn 开始时（首 token 前）必须已写 round=-1 checkpoint。"""
        from types import SimpleNamespace as NS
        from lingclaude.core.model_call import ModelCallMixin

        saved: list[tuple] = []
        order: list[str] = []

        class _Eng(ModelCallMixin):
            def __init__(self) -> None:
                self.session_id = "t123"
                self._messages = ["hi"]  # _build_messages 后的形态占位

            def _build_messages(self, prompt: str) -> list:
                return ["u", "s", prompt]

            def _build_openai_tools(self, query: str = "") -> list:
                return []

            def _resolve_model_config(self, prompt: str) -> tuple:
                return (None, None)

            def _save_checkpoint(self, messages, round_idx, prompt, used_tools, ti, to, tag=None):
                saved.append((round_idx, prompt, tuple(messages)))

            def _provider_stream(self, *a, **k):
                yield {"type": "finish", "usage": None}
                yield {"type": "done"}

        eng = _Eng()
        # provider 首事件即 finish(usage=None) + done 前 engine 需要 finalize 链，
        # 但本测试只关心「checkpoint 先于 provider」——provider 首次调用即抛
        # 哨兵异常截断生成器，避免拖入 finalize 全家桶 stub。
        class _Sentinel(Exception):
            pass

        def _provider_stream(*a, **k):
            order.append("provider")
            raise _Sentinel

        eng._provider = type("P", (), {"stream_complete": staticmethod(_provider_stream)})()
        orig_save = eng._save_checkpoint

        def _spy_save(*a, **k):
            order.append("checkpoint")
            orig_save(*a, **k)

        eng._save_checkpoint = _spy_save  # type: ignore[method-assign]
        try:
            list(eng.stream_call_model("你好"))
        except _Sentinel:
            pass
        assert order == ["checkpoint", "provider"], "checkpoint 必须先于 provider 调用"
        assert saved and saved[0][0] == -1 and saved[0][1] == "你好"
        assert "你好" in saved[0][2]

    def test_sync_turn_start_writes_checkpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非流式 _call_model 同语义：round=-1 checkpoint 先于 provider。"""
        from lingclaude.core.model_call import ModelCallMixin

        saved: list[tuple] = []

        class _Eng(ModelCallMixin):
            def __init__(self) -> None:
                self.session_id = "t123s"

            def _build_messages(self, prompt: str) -> list:
                return ["u", prompt]

            def _build_openai_tools(self, query: str = "") -> list:
                return []

            def _resolve_model_config(self, prompt: str) -> tuple:
                return (None, None)

            def _save_checkpoint(self, messages, round_idx, prompt, used_tools, ti, to, tag=None):
                saved.append((round_idx, prompt))

            def _log_model_request(self, prompt, messages, tools):
                saved.append(("model_request", prompt))
                return 0

            def _pre_send_check(self, seq, messages):
                return False  # fail-closed：到此即返回，验证 checkpoint 已落

            def _log_to_flywheel(self, *a, **k):
                pass

        eng = _Eng()
        eng._provider = None
        out = eng._call_model("同步路径")
        assert saved[0][0] == -1 and saved[0][1] == "同步路径"
        assert "MV-1 fail-closed" in out  # 走到了 fail-closed，说明 checkpoint 先落

    # ── P2: 单一输出 owner（display Console 选路）──

    def test_display_console_routes_stdout_when_managed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """全屏托管期 rich Console 应写 sys.stdout（代理），而非 stderr 直通。"""
        from lingclaude.cli import display, repl_io

        repl_io.set_full_tui_managed(True)
        try:
            console = display._get_console()
            assert console.file is sys.stdout
        finally:
            repl_io.set_full_tui_managed(False)

    def test_display_console_stderr_when_not_managed(self) -> None:
        """plain 模式（未托管）保持 stderr 直通——彩色正式版是设计意图。"""
        from lingclaude.cli import display, repl_io

        repl_io.set_full_tui_managed(False)
        console = display._get_console()
        import sys as _s
        assert console.file is _s.stderr

    def test_is_full_tui_managed_flag_roundtrip(self) -> None:
        from lingclaude.cli import repl_io

        repl_io.set_full_tui_managed(True)
        assert repl_io.is_full_tui_managed() is True
        repl_io.set_full_tui_managed(False)
        assert repl_io.is_full_tui_managed() is False

    # ── P3: resync 全量重绘原语 ──

    def test_resync_rebuilds_and_strips(
        self, _pt_available: None, tmp_path: Path
    ) -> None:
        """resync() 从 output_source 重建文档 + 剥 SGR + 请求重绘。"""
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        invalidated: list[bool] = []
        s.install_output_source(
            lambda: ["干净行", "\x1b[1;4m脏行\x1b[0m", "\x1b[48;5;235m底色\x1b[0m"]
        )
        s._app = None  # 未启动态：_invalidate 内部自静默
        s.resync()
        text = s._out_buffer.text
        assert "干净行" in text
        assert "\x1b" not in text, "resync 后不允许残留任何 ESC"
        assert "脏行" in text and "底色" in text
        assert not hasattr(s, "_resync_marker")  # 幂等：重复调用安全
        s.resync()  # 二次调用不抛

    def test_resync_cmd_dispatch(self, capsys: pytest.CaptureFixture) -> None:
        """/resync 斜杠命令：有 resync 的会话被调用，无 resync 的提示降级。"""
        from types import SimpleNamespace as NS

        from lingclaude.cli.commands import SlashCommandProcessor

        calls: list[bool] = []

        class _SessionWithResync:
            def resync(self) -> None:
                calls.append(True)

        proc = SlashCommandProcessor(engine=NS(), status=NS())
        proc.session = _SessionWithResync()
        assert proc.handle("/resync") is True
        assert calls == [True]
        assert "已重绘" in capsys.readouterr().out

        proc2 = SlashCommandProcessor(engine=NS(), status=NS())
        proc2.session = NS()  # 无 resync 方法
        assert proc2.handle("/resync") is True
        assert "不支持" in capsys.readouterr().out

        proc3 = SlashCommandProcessor(engine=NS(), status=NS())
        assert proc3.handle("/resync") is True  # session 未注入也安全降级
        assert "不支持" in capsys.readouterr().out

# ── B: plain 模式禁色降级口（2026-09-21 乱码战役收尾）─────────────────


class TestPlainNoColor:
    """NO_COLOR / LINGCLAUDE_PLAIN_NO_COLOR → rich 零 SGR 输出。

    背景：实测终端声明 TERM=xterm-256color 却把 ESC 渲染成字面 '?'，
    plain 模式 rich→stderr 彩色直通即乱码。禁色开关让第四路径在
    plain 模式下物理消灭（force_terminal=False → rich 判定非终端）。
    """

    def test_no_color_env_plain_console_no_sgr(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """NO_COLOR=1 → plain Console 渲染不产生任何 ESC 序列。"""
        import re

        from lingclaude.cli import display, repl_io

        repl_io.set_full_tui_managed(False)
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.delenv("LINGCLAUDE_PLAIN_NO_COLOR", raising=False)
        console = display._get_console()
        assert console.file is sys.stderr  # 直通语义不变，只是禁色
        console.print("[bold red]加粗[/bold red] [cyan]青色[/cyan]")
        err = capsys.readouterr().err
        # ESC = \x1b；禁色后不得存在任何 CSI 序列
        assert not re.search(r"\x1b\[[0-9;]*m", err), f"残留 SGR: {err!r}"
        assert "加粗" in err and "青色" in err  # 内容仍在，只去色

    def test_lingclaude_plain_no_color_alias(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """LINGCLAUDE_PLAIN_NO_COLOR=1 与 NO_COLOR 等效（行为级：零 SGR）。"""
        import re

        from lingclaude.cli import display, repl_io

        repl_io.set_full_tui_managed(False)
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("LINGCLAUDE_PLAIN_NO_COLOR", "1")
        console = display._get_console()
        # 禁色分支显式传 force_terminal=False（rich 实测不存 _no_color 属性）
        assert console._force_terminal is False
        console.print("[bold red]加粗[/bold red]")
        err = capsys.readouterr().err
        assert not re.search(r"\x1b\[[0-9;]*m", err), f"残留 SGR: {err!r}"
        assert "加粗" in err

    def test_managed_takes_precedence_over_no_color(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """托管期不受禁色开关影响——stdout 代理路径优先（P2 语义保持）。"""
        from lingclaude.cli import display, repl_io

        monkeypatch.setenv("NO_COLOR", "1")
        repl_io.set_full_tui_managed(True)
        try:
            console = display._get_console()
            assert console.file is sys.stdout
        finally:
            repl_io.set_full_tui_managed(False)

    def test_plain_console_plain_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """2026-09-22 终裁：默认纯文本（零 ESC 源头），彩色改 LINGCLAUDE_COLOR opt-in。

        旧语义（无环境变量→彩色）随「声明彩色却不消费 SGR」终端的长期乱码
        一起废弃；禁色 Console 显式 force_terminal=False + color_system=None。
        """
        from lingclaude.cli import display, repl_io

        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("LINGCLAUDE_COLOR", raising=False)
        monkeypatch.delenv("LINGCLAUDE_PLAIN_NO_COLOR", raising=False)
        repl_io.set_full_tui_managed(False)
        console = display._get_console()
        # 禁色分支显式 force_terminal=False；color_system=None 物理归零
        assert console._force_terminal is False
        assert console._color_system is None


class TestToolbarStyle:
    """2026-09-22: 状态栏样式注册修复——状态球/上下文分色/todo 标记可见性。

    根因：toolbar_fragments 与 full_tui 用 class:X fragment 选择器着色，
    但 PT 从未收到任何样式规则（Application/PromptSession 均未传 style=），
    全部 fragment 渲染为默认色。且 PT3 规则字典 key 禁带 class: 前缀
    （CLASS_NAMES_RE ^[a-z0-9.\\s_-]*$，'class:green' 当 key 直接
    AssertionError）——规则 key 必须是裸类名。
    """

    def test_style_table_registered_bare_class_names(self) -> None:
        """样式表构造成功且 fragment 查询真正着色（实证的着色路径）。"""
        from lingclaude.cli.interface import PT_TUI_STYLE

        assert PT_TUI_STYLE is not None
        green = PT_TUI_STYLE.get_attrs_for_style_str("class:green")
        assert green.color == "ansigreen"
        red = PT_TUI_STYLE.get_attrs_for_style_str("class:red")
        assert red.color == "ansired" and red.bold is True
        accent = PT_TUI_STYLE.get_attrs_for_style_str("class:accent")
        assert accent.color == "ansicyan" and accent.bold is True

    def test_full_tui_application_carries_style(self) -> None:
        """全屏 Application 构造必须携带样式表（修复点直证）。"""
        from lingclaude.cli.interface import PT_TUI_STYLE
        from lingclaude.cli.full_tui import PT_TUI_STYLE as FT_STYLE

        assert FT_STYLE is PT_TUI_STYLE
        app = FullTuiSession(history_file="/tmp/_t_style_h")._build_application(None)
        assert app.style is FT_STYLE

    def test_pt_session_carries_style(self) -> None:
        """PromptSession 构造必须携带样式表（bottom_toolbar 路径修复点）。"""
        s = interface.PromptToolkitSession(history_file="/tmp/_t_style_h2")
        # PT 包进 DynamicStyle 代理，但查询结果必须等于我们的规则
        got = s._session.app.style.get_attrs_for_style_str("class:green")
        assert got.color == "ansigreen"


class TestCutoverGeneration:
    """P2-13（Pi chord 双代热更）：蓝绿 cutover 语义测试。

    不真 run 抢终端——candidate 用 fake（非 PT Application 走 verify 抛错路径）
    或最小 stub Application；断言「失败回退旧代不动 / 成功切换 _app 替换」。
    """

    def _mk_session(self, tmp_path: Path) -> Any:
        return FullTuiSession(history_file=str(tmp_path / "hist"))

    def test_candidate_build_fail_keeps_old(self, _pt_available: None, tmp_path: Path) -> None:
        """候选构建失败 → 返回 False，旧代 self._app 零扰动。"""
        s = self._mk_session(tmp_path)
        old_app = SimpleNamespace(exit=lambda: None)
        s._app = old_app
        ok = s.cutover_generation(build_new=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert ok is False
        assert s._app is old_app  # 旧代未动

    def test_candidate_verify_fail_keeps_old(self, _pt_available: None, tmp_path: Path) -> None:
        """候选验证失败 → dispose 回退，旧代继续服务。"""
        s = self._mk_session(tmp_path)
        old_app = SimpleNamespace(exit=lambda: None)
        s._app = old_app
        candidate = SimpleNamespace(layout=None)  # 缺 layout → 缺省 verify 抛 ValueError
        ok = s.cutover_generation(build_new=lambda: candidate)
        assert ok is False
        assert s._app is old_app

    def test_candidate_verify_override_fail_keeps_old(self, _pt_available: None, tmp_path: Path) -> None:
        """自定义 verify 抛异常 → 同样回退（verify 钩子语义）。"""
        s = self._mk_session(tmp_path)
        old_app = SimpleNamespace(exit=lambda: None)
        s._app = old_app
        cand = SimpleNamespace(layout=object())
        def bad_verify(_c: Any) -> None:
            raise AssertionError("not ready")
        ok = s.cutover_generation(build_new=lambda: cand, verify=bad_verify)
        assert ok is False
        assert s._app is old_app

    def test_cutover_success_swaps_app(self, _pt_available: None, tmp_path: Path) -> None:
        """验证通过 → _app 替换为候选（冷 cutover：旧代未运行线程路径）。"""
        s = self._mk_session(tmp_path)
        s._running = False
        s._app_thread = None  # 冷切换：无旧线程
        cand = SimpleNamespace(layout=object(), exit=lambda: None)
        ok = s.cutover_generation(build_new=lambda: cand,
                                  verify=lambda c: None)  # 自定义 verify 放行
        assert ok is True
        assert s._app is cand
