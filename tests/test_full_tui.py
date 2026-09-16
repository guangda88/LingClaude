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
        assert s._output_area.text.count("\n") >= MAX_OUTPUT_LINES - 1
        assert "line-0" not in s._output_area.text
        assert f"line-{MAX_OUTPUT_LINES + 49}" in s._output_area.text

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
        assert s._output_area.text == ""

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
        text = s._output_area.text  # noqa: SLF001
        assert "第一行" in text
        assert "第二行" in text
        assert "第三行" in text
        assert "\r" not in text
        # 40 空格清列不应产生纯空白行
        assert not any(line and not line.strip() for line in text.split("\n"))

    def test_output_area_line_cap(self, _pt_available: None, tmp_path: Path) -> None:
        s = self._make(tmp_path)
        s._append_output_lines([f"l{i}" for i in range(MAX_OUTPUT_LINES + 50)])
        lines = s._output_area.text.split("\n")  # noqa: SLF001
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
