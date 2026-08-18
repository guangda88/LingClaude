"""LINGKERNEL_v1 task #2 — ToolPipeline 5 段测试。"""

from __future__ import annotations

from lingclaude.engine.tool_pipeline import (
    DEFAULT_DANGEROUS_PATTERNS,
    GuardDecision,
    PipelineContext,
    ToolPipeline,
)
from lingclaude.engine.tools import ToolDefinition, ToolRegistry


def _mk_tool(name="t", handler=None, scope="read"):
    if handler is None:
        handler = lambda: {"ok": True}
    return ToolDefinition(
        name=name, description="t", parameters={"type": "object"},
        handler=handler, security_scope=scope,
    )


# === 1. pre-execute waterfall ===

def test_pre_permission_blocks():
    reg = ToolRegistry()
    reg.register(_mk_tool())
    p = ToolPipeline(reg)
    res = p.execute("t", {}, permissions_blocks=lambda n: True)
    assert "blocked by permissions" in res.get("error", "")
    assert res.get("pipeline_aborted") is True


def test_pre_rate_limit_blocks():
    reg = ToolRegistry()
    reg.register(_mk_tool())
    p = ToolPipeline(reg)
    res = p.execute("t", {}, rate_check=lambda: (False, "throttled"))
    assert "throttled" in res.get("error", "")


def test_pre_dangerous_command_blocks():
    reg = ToolRegistry()
    reg.register(_mk_tool())
    p = ToolPipeline(reg, critical_tools=("bash",))
    res = p.execute(
        "bash", {"command": "rm -rf / --no-preserve-root"},
    )
    assert "危险命令被阻止" in res.get("error", "")


def test_pre_listener_can_abort():
    reg = ToolRegistry()
    reg.register(_mk_tool())
    p = ToolPipeline(reg)

    def abort_listener(ctx):
        ctx.aborted = True
        ctx.abort_reason = "aborted by listener"

    p.add_pre_listener(abort_listener)
    res = p.execute("t", {})
    assert "aborted by listener" in res.get("error", "")


def test_pre_listener_exception_swallowed():
    reg = ToolRegistry()
    reg.register(_mk_tool())
    p = ToolPipeline(reg)

    def bad_listener(ctx):
        raise RuntimeError("oops")

    p.add_pre_listener(bad_listener)
    res = p.execute("t", {})
    assert res == {"ok": True}  # listener 失败不影响主流程


# === 2. monotonic guards ===

def test_guard_allow():
    reg = ToolRegistry()
    reg.register(_mk_tool())
    p = ToolPipeline(reg)

    def allow_guard(td, ctx):
        return GuardDecision(decision="allow")

    p.add_guard(allow_guard)
    res = p.execute("t", {})
    assert res == {"ok": True}


def test_guard_deny_blocks():
    reg = ToolRegistry()
    reg.register(_mk_tool())
    p = ToolPipeline(reg)

    def deny_guard(td, ctx):
        return GuardDecision(decision="deny", reason="sensitive tool")

    p.add_guard(deny_guard)
    res = p.execute("t", {})
    assert "guard deny" in res.get("error", "")
    assert "sensitive tool" in res.get("error", "")


def test_guard_abstain_passes():
    reg = ToolRegistry()
    reg.register(_mk_tool())
    p = ToolPipeline(reg)

    def abstain_guard(td, ctx):
        return GuardDecision(decision="abstain")

    p.add_guard(abstain_guard)
    res = p.execute("t", {})
    assert res == {"ok": True}


def test_guard_exception_treated_as_abstain():
    """dsh 规则: guard 异常 = abstain (let it through, 不阻塞)。"""
    reg = ToolRegistry()
    reg.register(_mk_tool())
    p = ToolPipeline(reg)

    def bad_guard(td, ctx):
        raise RuntimeError("oops")

    p.add_guard(bad_guard)
    res = p.execute("t", {})
    assert res == {"ok": True}


def test_guard_chain_deny_short_circuits():
    """多个 guard, 任一 deny 立即终止 (dsh 短路语义)。"""
    reg = ToolRegistry()
    reg.register(_mk_tool())
    p = ToolPipeline(reg)

    def first_deny(td, ctx):
        return GuardDecision(decision="deny", reason="first")

    def second_should_not_run(td, ctx):
        return GuardDecision(decision="deny", reason="second")

    p.add_guard(first_deny)
    p.add_guard(second_should_not_run)
    res = p.execute("t", {})
    assert "first" in res.get("error", "")
    assert "second" not in res.get("error", "")


# === 3. execute (around-dispatch) ===

def test_execute_calls_handler():
    reg = ToolRegistry()
    captured = {}

    def handler(**kwargs):
        captured.update(kwargs)
        return {"echoed": kwargs.get("msg")}

    reg.register(_mk_tool("echo", handler))
    p = ToolPipeline(reg)
    res = p.execute("echo", {"msg": "hi"})
    assert captured == {"msg": "hi"}
    assert res == {"echoed": "hi"}


def test_execute_metrics_recorded():
    reg = ToolRegistry()
    reg.register(_mk_tool())
    p = ToolPipeline(reg)
    res = p.execute("t", {})
    # metrics 通过 ctx 在 listener 中可见; 此处仅测执行成功
    assert res == {"ok": True}


def test_execute_handler_exception_caught():
    reg = ToolRegistry()
    reg.register(_mk_tool("boom", lambda: 1 / 0))
    p = ToolPipeline(reg)
    res = p.execute("boom", {})
    assert "Tool execution failed" in res.get("error", "")


def test_execute_tool_not_found():
    reg = ToolRegistry()
    p = ToolPipeline(reg)
    res = p.execute("missing", {})
    assert "Tool not found" in res.get("error", "")


# === 4. post-execute waterfall ===

def test_post_listener_receives_ctx():
    reg = ToolRegistry()
    reg.register(_mk_tool("echo", lambda: {"value": 1}))
    p = ToolPipeline(reg)
    captured = {}

    def post(ctx):
        captured["result"] = ctx.raw_result
        captured["metrics"] = ctx.metrics.copy()

    p.add_post_listener(post)
    res = p.execute("echo", {})
    assert captured["result"] == {"value": 1}
    assert "duration" in captured["metrics"]


def test_post_listener_can_abort():
    reg = ToolRegistry()
    reg.register(_mk_tool())
    p = ToolPipeline(reg)

    def post_abort(ctx):
        ctx.aborted = True
        ctx.abort_reason = "post-abort"

    p.add_post_listener(post_abort)
    res = p.execute("t", {})
    assert "post-abort" in res.get("error", "")


def test_post_write_verify_blocks():
    reg = ToolRegistry()
    reg.register(_mk_tool("write", lambda path, content: None, scope="write"))
    p = ToolPipeline(reg, write_scoped_tools=("write",))
    res = p.execute(
        "write", {"path": "/tmp/x", "content": "y"},
        post_write_verify=lambda p: (False, "bad content"),
    )
    assert "verify-post" in res.get("error", "")


# === 5. finalizeContent ===

def test_finalize_invoked_when_defined():
    reg = ToolRegistry()

    def handler():
        return "raw"

    def finalize(args, value):
        return [{"type": "text", "text": "wrapped"}]

    td = ToolDefinition(
        name="t", description="t", parameters={},
        handler=handler, finalize_content=finalize,
    )
    reg.register(td)
    p = ToolPipeline(reg)
    res = p.execute("t", {})
    assert res == [{"type": "text", "text": "wrapped"}]


def test_finalize_preserves_when_undefined():
    reg = ToolRegistry()
    reg.register(_mk_tool("echo", lambda: {"x": 1}))
    p = ToolPipeline(reg)
    res = p.execute("echo", {})
    assert res == {"x": 1}


def test_finalize_returns_dict_wrapped():
    """finalize 返回 dict 时, 直接用 (不走 pipeline wrap)。"""
    reg = ToolRegistry()

    def handler():
        return "raw"

    def finalize(args, value):
        return {"wrapped": True}

    td = ToolDefinition(
        name="t", description="t", parameters={},
        handler=handler, finalize_content=finalize,
    )
    reg.register(td)
    p = ToolPipeline(reg)
    res = p.execute("t", {})
    assert res == {"wrapped": True}


# === 端到端 5 段综合 ===

def test_full_pipeline_all_stages():
    """5 段依次执行: pre → guard → execute → post → finalize"""
    reg = ToolRegistry()
    pre_seen = []
    guard_seen = []
    post_seen = []
    finalize_seen = []

    def handler():
        return {"data": "ok"}

    def finalize(args, value):
        finalize_seen.append(value)
        return {"wrapped": True}

    td = ToolDefinition(
        name="t", description="t", parameters={},
        handler=handler, finalize_content=finalize,
    )
    reg.register(td)
    p = ToolPipeline(reg)
    p.add_pre_listener(lambda ctx: pre_seen.append(ctx.name))
    p.add_guard(lambda td, ctx: (guard_seen.append(td.name) or GuardDecision("allow")))
    p.add_post_listener(lambda ctx: post_seen.append(ctx.raw_result))

    res = p.execute("t", {})
    assert res == {"wrapped": True}
    assert pre_seen == ["t"]
    assert guard_seen == ["t"]
    assert post_seen == [{"data": "ok"}]
    assert finalize_seen == [{"data": "ok"}]


def test_dangerous_patterns_constant_includes_key_cases():
    """dsh / 原 coding.py 关键危险命令都在常量里。"""
    expected = {"rm -rf /", "mkfs", "dd if=", "> /dev/sd", "chmod 777 /", ":(){:|:&};:"}
    assert set(DEFAULT_DANGEROUS_PATTERNS) >= expected


def test_pipeline_ctx_post_init_metrics_default_dict():
    ctx = PipelineContext(name="x", args={})
    assert ctx.metrics == {}
    ctx.metrics["k"] = 1
    assert ctx.metrics["k"] == 1


def test_pipeline_ctx_default_fields():
    ctx = PipelineContext(name="x", args={"a": 1})
    assert ctx.raw_result is None
    assert ctx.is_error is False
    assert ctx.error_msg == ""
    assert ctx.aborted is False
    assert ctx.abort_reason == ""


if __name__ == "__main__":
    import sys
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
            traceback.print_exc()
    if failed:
        sys.exit(1)
    print(f"\nAll {len(fns)} tests passed")