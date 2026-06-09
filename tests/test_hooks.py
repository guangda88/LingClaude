from __future__ import annotations

import pytest

from lingclaude.core.hooks import (
    HookContext,
    HookManager,
    HookType,
)


class TestHookType:
    def test_all_values(self) -> None:
        expected = {"pre_task", "post_task", "on_error", "on_stop", "pre_compact", "post_compact"}
        assert {h.value for h in HookType} == expected

    def test_str_enum(self) -> None:
        assert HookType.PRE_TASK == "pre_task"
        assert isinstance(HookType.PRE_TASK, str)


class TestHookContext:
    def test_frozen(self) -> None:
        ctx = HookContext(
            hook_type=HookType.PRE_TASK,
            session_id="test",
            prompt="hello",
        )
        with pytest.raises(AttributeError):
            ctx.prompt = "changed"  # type: ignore[misc]

    def test_defaults(self) -> None:
        ctx = HookContext(hook_type=HookType.ON_ERROR, session_id="s1")
        assert ctx.prompt == ""
        assert ctx.output == ""
        assert ctx.tool_name == ""
        assert ctx.error_message == ""
        assert ctx.metadata == {}


class TestHookManager:
    def test_register_and_trigger(self) -> None:
        mgr = HookManager()
        called: list[str] = []

        def hook(ctx: HookContext) -> HookContext:
            called.append(ctx.prompt)
            return ctx

        mgr.register("test_hook", HookType.PRE_TASK, hook)
        ctx = HookContext(hook_type=HookType.PRE_TASK, session_id="s1", prompt="hello")
        result = mgr.trigger(ctx)
        assert called == ["hello"]
        assert result.modified_context is not None
        assert result.modified_context.prompt == "hello"

    def test_hook_modifies_context(self) -> None:
        mgr = HookManager()

        def add_meta(ctx: HookContext) -> HookContext:
            return HookContext(
                hook_type=ctx.hook_type,
                session_id=ctx.session_id,
                prompt=ctx.prompt,
                metadata={**ctx.metadata, "injected": True},
            )

        mgr.register("injector", HookType.PRE_TASK, add_meta)
        ctx = HookContext(hook_type=HookType.PRE_TASK, session_id="s1")
        result = mgr.trigger(ctx)
        assert result.modified_context is not None
        assert result.modified_context.metadata["injected"] is True

    def test_priority_ordering(self) -> None:
        mgr = HookManager()
        order: list[str] = []

        def make_hook(name: str) -> type:
            def fn(ctx: HookContext) -> HookContext:
                order.append(name)
                return ctx
            return fn  # type: ignore[return-value]

        mgr.register("low", HookType.POST_TASK, make_hook("low"), priority=200)
        mgr.register("high", HookType.POST_TASK, make_hook("high"), priority=1)
        mgr.register("mid", HookType.POST_TASK, make_hook("mid"), priority=100)

        ctx = HookContext(hook_type=HookType.POST_TASK, session_id="s1")
        mgr.trigger(ctx)
        assert order == ["high", "mid", "low"]

    def test_hook_exception_returns_error(self) -> None:
        mgr = HookManager()

        def bad_hook(ctx: HookContext) -> HookContext:
            raise ValueError("boom")

        mgr.register("bad", HookType.ON_ERROR, bad_hook)
        ctx = HookContext(hook_type=HookType.ON_ERROR, session_id="s1")
        result = mgr.trigger(ctx)
        assert result.error == "boom"
        assert result.modified_context is not None

    def test_unregister(self) -> None:
        mgr = HookManager()
        mgr.register("a", HookType.PRE_TASK, lambda c: c)
        mgr.register("b", HookType.PRE_TASK, lambda c: c)
        assert mgr.unregister("a") is True
        assert mgr.unregister("nonexistent") is False
        hooks = mgr.list_hooks()
        assert len(hooks) == 1
        assert hooks[0]["name"] == "b"

    def test_has_hooks(self) -> None:
        mgr = HookManager()
        assert mgr.has_hooks(HookType.PRE_TASK) is False
        mgr.register("h1", HookType.PRE_TASK, lambda c: c)
        assert mgr.has_hooks(HookType.PRE_TASK) is True
        assert mgr.has_hooks(HookType.POST_TASK) is False

    def test_clear(self) -> None:
        mgr = HookManager()
        mgr.register("h1", HookType.PRE_TASK, lambda c: c)
        mgr.register("h2", HookType.POST_TASK, lambda c: c)
        mgr.clear()
        assert mgr.list_hooks() == ()

    def test_type_filtering(self) -> None:
        mgr = HookManager()
        pre_called: list[str] = []

        def pre_hook(ctx: HookContext) -> HookContext:
            pre_called.append("yes")
            return ctx

        mgr.register("pre", HookType.PRE_TASK, pre_hook)
        mgr.register("post", HookType.POST_TASK, lambda c: c)

        ctx = HookContext(hook_type=HookType.PRE_TASK, session_id="s1")
        mgr.trigger(ctx)
        assert pre_called == ["yes"]

    def test_hook_returns_none_keeps_context(self) -> None:
        mgr = HookManager()

        def noop_hook(ctx: HookContext) -> None:
            return None  # type: ignore[return-value]

        mgr.register("noop", HookType.PRE_TASK, noop_hook)
        ctx = HookContext(hook_type=HookType.PRE_TASK, session_id="s1", prompt="original")
        result = mgr.trigger(ctx)
        assert result.modified_context is not None
        assert result.modified_context.prompt == "original"

    def test_list_hooks_returns_info(self) -> None:
        mgr = HookManager()
        mgr.register("h1", HookType.ON_STOP, lambda c: c, priority=50)
        hooks = mgr.list_hooks()
        assert len(hooks) == 1
        assert hooks[0]["name"] == "h1"
        assert hooks[0]["type"] == "on_stop"
        assert hooks[0]["priority"] == 50
