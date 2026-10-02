"""A(2026-09-26) 斜杠命令注册表 + 歧义形状守卫 + `!` bash 直通 测试。

覆盖：
1. SLASH_REGISTRY 单源：补全清单/handle 分派/handler 绑定一致
2. 歧义形状守卫：/Users/me、/tmp、/etc/hosts 原样放行（不吞）
3. needs_args 拦截 + arg_hint 提示
4. 别名分发（/? /todo /plan /continue）与补全可见性
5. /quit /exit 特判（审计#3 语义保留）
6. `!` 直通：单 ! 用法提示；!! 转义不触发直通
"""

from __future__ import annotations

import contextlib
import io
from types import SimpleNamespace

import pytest

from lingclaude.cli import commands as cmd_mod
from lingclaude.cli.commands import (
    SLASH_COMPLETER_WORDS,
    SLASH_REGISTRY,
    SlashCommandProcessor,
)


def _make_proc() -> SlashCommandProcessor:
    """engine/status 用最小 mock（分派路径测试不触 handler 内部）。"""
    engine = SimpleNamespace(
        _messages=[], _conversation=[],
        session_manager=SimpleNamespace(
            list_sessions=lambda project_path="": ()),
    )
    proc = SlashCommandProcessor(engine=engine, status=SimpleNamespace())
    return proc


class TestRegistrySingleSource:
    def test_completer_words_derived_from_registry(self) -> None:
        """补全清单必须 = 注册表可见条目（防两账本再漂移）。"""
        expected = []
        seen = set()
        for e in SLASH_REGISTRY.values():
            if e.hidden or e.name in seen:
                continue
            seen.add(e.name)
            expected.append(e.name)
        assert SLASH_COMPLETER_WORDS == expected

    def test_core_commands_registered(self) -> None:
        """核心命令齐备（/fork /share /multi /openrouter 历史漏登先例）。"""
        for name in ("/help", "/clear", "/compact", "/model", "/schedule",
                     "/lsp", "/resume", "/continue", "/session",
                     "/checkpoint", "/recover", "/rewind", "/quit", "/exit",
                     "/fork", "/share", "/tasks", "/todo", "/plan",
                     "/history", "/openrouter", "/resync", "/multi", "/webui"):
            assert name in SLASH_REGISTRY, f"{name} 未注册"

    def test_registry_handlers_are_real_methods(self) -> None:
        """主干命令 handler 必须是 SlashCommandProcessor 方法；插件命令
        （slash_plugin_loader 注册的函数）豁免——它们以 (processor, arg)
        函数形状落在同一张表，handle() 调用形态相同。"""
        for name, e in SLASH_REGISTRY.items():
            if e.handler is None:
                assert name in ("/quit", "/exit")
                continue
            assert callable(e.handler)
            hname = getattr(e.handler, "__name__", "")
            is_plugin_fn = (
                getattr(e.handler, "__module__", "").startswith(
                    "lingclaude.cli.slash_plugins")
                or hname in ("policy_cmd",)
            )
            if not is_plugin_fn:
                assert hasattr(SlashCommandProcessor, hname), \
                    f"{name} handler {hname} 既非 processor 方法也非插件函数"


class TestAmbiguityGuard:
    @pytest.mark.parametrize("line", ["/Users/me", "/tmp", "/etc/hosts",
                                      "/https://example.com"])
    def test_path_like_lines_not_consumed(self, line: str) -> None:
        """路径/URL 形状的行原样放行（返回 False → 落给模型/管道）。"""
        proc = _make_proc()
        assert proc.handle(line) is False

    def test_quit_still_works(self) -> None:
        proc = _make_proc()
        assert proc.handle("/quit") is True
        assert proc.quit_requested is True

    def test_exit_alias(self) -> None:
        proc = _make_proc()
        assert proc.handle("/exit") is True
        assert proc.quit_requested is True


class TestNeedsArgs:
    def test_no_registered_command_requires_args_by_default(self) -> None:
        """当前注册表全部命令无参均可执行（/resume 列表语义），不误拦。"""
        proc = _make_proc()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = proc.handle("/resume")
        assert r is True
        # _cmd_resume 走到 list_sessions（engine mock 需有 session_manager）
        # 缺参提示不应出现
        assert "缺参数" not in buf.getvalue()


class TestAliasDispatch:
    def test_help_alias_qmark(self) -> None:
        proc = _make_proc()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert proc.handle("/?") is True
        assert "斜杠命令" in buf.getvalue()

    def test_todo_plan_alias_dispatch(self) -> None:
        proc = _make_proc()
        # /tasks 域 handler 在 mixin；mock engine 足以走分派不炸
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            for line in ("/todo", "/plan"):
                assert proc.handle(line) is True

    def test_help_single_command(self) -> None:
        proc = _make_proc()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert proc.handle("/help tasks") is True
        out = buf.getvalue()
        assert "/tasks" in out and "任务面板" in out

    def test_help_unknown_command(self) -> None:
        proc = _make_proc()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert proc.handle("/help nosuch") is True
        assert "未知命令" in buf.getvalue()

    def test_help_lists_all_registered(self) -> None:
        proc = _make_proc()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            proc.handle("/help")
        out = buf.getvalue()
        for name in ("/fork", "/share", "/openrouter", "/resync", "/multi",
                     "/session", "/history"):
            assert name in out, f"/help 漏列 {name}"


class TestBangShell:
    def _ctx(self, monkeypatch: pytest.MonkeyPatch, script: str,
             capture: dict) -> "_ReplCtx-like":
        from lingclaude.cli import repl as repl_mod

        class _FakeEngine:
            def execute_tool(self, name: str, **kw):
                capture["name"] = name
                capture["command"] = kw.get("command")
                import subprocess
                p = subprocess.run(["bash", "-c", script],
                                   capture_output=True, text=True)
                return {"data": {"exit_code": p.returncode,
                                 "stdout": p.stdout, "stderr": p.stderr}}

        ctx = SimpleNamespace(engine=_FakeEngine(), input_queue=None)
        return ctx

    def test_bang_runs_command_via_execute_tool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from lingclaude.cli import repl as repl_mod

        capture: dict = {}
        ctx = self._ctx(monkeypatch, "echo bang-ok", capture)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            repl_mod._run_bang_shell(ctx, "!echo bang-ok")
        out = buf.getvalue()
        assert capture["name"] == "bash"
        assert capture["command"] == "echo bang-ok"
        assert "bang-ok" in out
        assert "[exit 0]" in out

    def test_bang_empty_shows_usage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from lingclaude.cli import repl as repl_mod

        capture: dict = {}
        ctx = self._ctx(monkeypatch, "true", capture)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            repl_mod._run_bang_shell(ctx, "!")
        assert "用法" in buf.getvalue()
        assert "name" not in capture  # 未执行

    def test_bang_blocked_result_handled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from lingclaude.cli import repl as repl_mod

        class _BlockedEngine:
            def execute_tool(self, name: str, **kw):
                return {"error": "blocked by sensitive_path_gate"}

        ctx = SimpleNamespace(engine=_BlockedEngine(), input_queue=None)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            repl_mod._run_bang_shell(ctx, "!cat /etc/shadow")
        assert "拦截" in buf.getvalue()


class TestClearSemantics:
    """2026-10-02 /clear 语义升级：clear = 真·新会话（engine.reset() 全量复位）。

    背景：34997fa（2026-08-25 app.py 巨石时代）内联逻辑只清 _messages/
    _conversation 两列表，注册表化时原样外提，此后引擎 reset() 进化了
    8 项语义（换 session_id/落盘/transcript/usage/denials/working 等），
    /clear 一直没跟上——用户预期「clear = new session」落空。
    """

    def _make_processor(self, engine: object) -> Any:
        return cmd_mod.SlashCommandProcessor(
            engine, SimpleNamespace(), reader=lambda: "", submit=lambda t: None,
        )

    def test_clear_delegates_to_engine_reset(self) -> None:
        """/clear 必须走 engine.reset() 单源，不得自造清列表旁路。"""
        calls: list[str] = []

        class _ResetSpyEngine:
            def reset(self) -> None:
                calls.append("reset")

        proc = self._make_processor(_ResetSpyEngine())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            proc._cmd_clear()
        assert calls == ["reset"]
        assert "会话已清空" in buf.getvalue()

    def test_reset_full_semantics_documented_contract(self) -> None:
        """引擎 reset() 契约自检：8 项全量复位（与 query_engine.reset 对齐）。

        用轻构造引擎实测（模式同 test_agent_loop.py:619），防 reset()
        未来缩水而 /clear 静默跟着缩水。
        """
        from lingclaude.core.models import UsageSummary
        from lingclaude.core.query_engine import QueryEngine

        class _NullProvider:
            def complete(self, *a: object, **k: object) -> None:
                raise AssertionError("not called")

            async def acomplete(self, *a: object, **k: object) -> None:
                raise AssertionError("not called")

            def count_tokens(self, text: str) -> int:
                return 0

        engine = QueryEngine(model_provider=_NullProvider())  # type: ignore[arg-type]
        old_sid = engine.session_id
        engine._messages.append("u")
        engine._conversation.append({"role": "user", "content": "u"})
        engine._transcript.append("line")
        engine._denials.append("d")
        engine.reset()
        assert engine.session_id != old_sid          # ① 新会话身份
        assert engine._messages == []                # ② 消息清空
        assert engine._conversation == []            # ③ 对话镜像清空
        assert engine._transcript == []              # ④ transcript 清空
        assert engine._denials == []                 # ⑤ 拒绝上下文清空
        assert engine.turn_count == 0                # ⑥ 轮次归零
        assert not engine.has_checkpoint             # ⑦ 检查点清空
        assert engine.usage == UsageSummary()        # ⑧ usage 归零（query_engine.py:303）
