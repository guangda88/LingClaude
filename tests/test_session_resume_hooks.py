"""SESSION_RESUME 钩子接线回归（2026-09-29）。

背景：/resume·/continue·/session switch 三条恢复路径此前只替换内存上下文，
钩子消费者（TUI 输出窗 resync 回放）收不到事件——恢复成功但界面停留旧会话。

覆盖：
1. _fire_session_resume_hooks 正常触发（SESSION_RESUME + session_id 传递）
2. /resume <id> 成功路径触发钩子
3. /session switch <id> 成功路径触发钩子（此前静默跳过的路径）
4. engine._hooks 缺席 → 静默跳过不崩
5. hooks.trigger 抛异常 → 恢复主路径不受影响
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from lingclaude.cli.commands import SlashCommandProcessor
from lingclaude.cli._commands_session import _fire_session_resume_hooks
from lingclaude.core.hooks import HookContext, HookManager, HookType

_FIRED: list[HookContext] = []


def _capture(ctx: HookContext) -> HookContext | None:
    _FIRED.append(ctx)
    return None


def _make_engine(tmp_path: Any, load_ok: bool = True) -> SimpleNamespace:
    """最小 engine 鸭子类型：会话列表 1 条 + load_session 可控 + 真 HookManager。"""
    _FIRED.clear()
    hooks = HookManager()
    hooks.register("capture", HookType.SESSION_RESUME, _capture)
    session_row = {
        "session_id": "abc12345deadbeef",
        "created_at": "2026-09-29T10:00:00",
        "summary": "测试会话",
    }
    return SimpleNamespace(
        _messages=[],
        _conversation=[],
        _hooks=hooks,
        session_manager=SimpleNamespace(
            list_sessions=lambda project_path="": [session_row]),
        _session_persister=SimpleNamespace(
            load_session=lambda tid: load_ok),
    )


class TestFireSessionResumeHooks:
    def test_fires_with_session_id(self, tmp_path: Any) -> None:
        engine = _make_engine(tmp_path)
        _fire_session_resume_hooks(engine, "abc12345deadbeef", note="n1")
        assert len(_FIRED) == 1
        assert _FIRED[0].hook_type == HookType.SESSION_RESUME
        assert _FIRED[0].session_id == "abc12345deadbeef"
        assert _FIRED[0].resumed is True

    def test_missing_hooks_silent(self, tmp_path: Any) -> None:
        engine = _make_engine(tmp_path)
        del engine._hooks  # 模拟无钩子管理器的 engine
        _fire_session_resume_hooks(engine, "abc")  # 不应抛
        assert len(_FIRED) == 0

    def test_trigger_exception_swallowed(self, tmp_path: Any) -> None:
        engine = _make_engine(tmp_path)
        broken = SimpleNamespace(trigger=lambda ctx: 1 / 0)
        engine._hooks = broken
        _fire_session_resume_hooks(engine, "abc")  # 异常被吞，恢复主路径不受影响


class TestResumeCommandsFireHook:
    def test_cmd_resume_fires_hook(self, tmp_path: Any) -> None:
        engine = _make_engine(tmp_path)
        proc = SlashCommandProcessor(engine=engine, status=SimpleNamespace())
        proc._cmd_resume("/resume", "abc1")
        assert len(_FIRED) == 1
        assert _FIRED[0].hook_type == HookType.SESSION_RESUME

    def test_cmd_continue_fires_hook(self, tmp_path: Any) -> None:
        engine = _make_engine(tmp_path)
        proc = SlashCommandProcessor(engine=engine, status=SimpleNamespace())
        proc._cmd_resume("/continue", "")
        assert len(_FIRED) == 1

    def test_cmd_session_switch_fires_hook(self, tmp_path: Any) -> None:
        engine = _make_engine(tmp_path)
        proc = SlashCommandProcessor(engine=engine, status=SimpleNamespace())
        proc._cmd_session("switch abc1")
        assert len(_FIRED) == 1
        assert _FIRED[0].hook_type == HookType.SESSION_RESUME

    def test_cmd_session_switch_failure_no_hook(self, tmp_path: Any) -> None:
        engine = _make_engine(tmp_path, load_ok=False)
        proc = SlashCommandProcessor(engine=engine, status=SimpleNamespace())
        proc._cmd_session("switch abc1")
        assert len(_FIRED) == 0
