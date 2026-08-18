"""LINGKERNEL_v1 task #3 — ToolDefinition 4 字段测试。

覆盖：
- 新字段 is_concurrency_safe / finalize_content / present_call / present_result / output
- ToolOutputDefinition schema + render + presentation_meta
- ToolRegistry.execute + finalize_result 兼容性
- 老调用方 (handler + security_scope only) 仍可工作
"""

from __future__ import annotations

from lingclaude.core.types import Result
from lingclaude.engine.tools import (
    ToolDefinition,
    ToolOutputDefinition,
    ToolRegistry,
)


def _ok_payload(name="t"):
    return {"result": f"hello-{name}"}


def test_minimal_compat():
    """老调用: 仅 name/description/parameters/handler/security_scope — 仍 OK。"""
    td = ToolDefinition(
        name="t1",
        description="test",
        parameters={"type": "object"},
        handler=lambda: _ok_payload(),
    )
    assert td.name == "t1"
    assert td.is_concurrency_safe is False
    assert td.output is None
    assert td.finalize_content is None
    assert td.present_call is None
    assert td.present_result is None


def test_output_definition_schema_render():
    """ToolOutputDefinition 接受 schema + render + 可选 presentation_meta。"""

    def render(args, value):
        return [{"type": "text", "text": f"value={value}"}]

    out = ToolOutputDefinition(schema={"type": "object"}, render=render)
    blocks = out.render({"a": 1}, "X")
    assert blocks == [{"type": "text", "text": "value=X"}]
    assert out.presentation_meta is None


def test_output_definition_with_presentation_meta():
    def render(args, value):
        return [{"type": "text", "text": str(value)}]

    def pm(args, value):
        return {"ui_kind": "card", "rows": len(value)}

    out = ToolOutputDefinition(schema={"type": "array"}, render=render, presentation_meta=pm)
    assert out.presentation_meta({"a": 1}, [1, 2, 3]) == {"ui_kind": "card", "rows": 3}


def test_to_dict_omits_none():
    """to_dict 不含 None 字段; 含 output_schema 当 output 非空。"""

    def render(args, value):
        return []

    td_with_output = ToolDefinition(
        name="t2",
        description="d",
        parameters={"type": "object"},
        handler=lambda: None,
        output=ToolOutputDefinition(schema={"type": "object"}, render=render),
    )
    d = td_with_output.to_dict()
    assert d["output_schema"] == {"type": "object"}

    td_min = ToolDefinition(name="t3", description="d", parameters={}, handler=lambda: None)
    d_min = td_min.to_dict()
    assert "output_schema" not in d_min
    assert "finalize_content" not in d_min  # never leaked


def test_to_dict_never_leaks_internal():
    """dsh 规则: output/execute/finalizeContent/timeoutMs/isConcurrencySafe/presentCall/presentResult
    永不泄漏到 model-facing schema (registry.schemas() 也不应)。"""
    td = ToolDefinition(
        name="t",
        description="d",
        parameters={"type": "object"},
        handler=lambda: None,
        security_scope="write",
        is_concurrency_safe=True,
        finalize_content=lambda a, v: None,
        present_call=lambda a: {"pending": True},
    )
    d = td.to_dict()
    assert "finalize_content" not in d
    assert "present_call" not in d
    assert "present_result" not in d
    assert "handler" not in d


def test_finalize_result_returns_none_when_absent():
    reg = ToolRegistry()
    td = ToolDefinition(name="t", description="d", parameters={}, handler=lambda: None)
    reg.register(td)
    assert reg.finalize_result("t", {}, "v") is None


def test_finalize_result_invokes_callback():
    captured: list[Any] = []

    def finalize(args, value):
        captured.append((args, value))
        return [{"type": "text", "text": "wrapped"}]

    reg = ToolRegistry()
    td = ToolDefinition(
        name="t", description="d", parameters={},
        handler=lambda: None,
        finalize_content=finalize,
    )
    reg.register(td)
    out = reg.finalize_result("t", {"x": 1}, "raw")
    assert out == [{"type": "text", "text": "wrapped"}]
    assert captured == [({"x": 1}, "raw")]


def test_finalize_result_swallows_exceptions():
    """finalize 必须 total 且不抛 — caller catches."""

    def bad_finalize(args, value):
        raise RuntimeError("oops")

    reg = ToolRegistry()
    td = ToolDefinition(
        name="t", description="d", parameters={},
        handler=lambda: None,
        finalize_content=bad_finalize,
    )
    reg.register(td)
    assert reg.finalize_result("t", {}, "v") is None  # swallowed


def test_registry_execute_still_works():
    reg = ToolRegistry()
    td = ToolDefinition(
        name="echo",
        description="echo back",
        parameters={"type": "object", "properties": {"msg": {"type": "string"}}},
        handler=lambda msg: {"echoed": msg},
        security_scope="read",
    )
    reg.register(td)
    res = reg.execute("echo", msg="hi")
    assert res.is_ok
    assert res.data == {"echoed": "hi"}


def test_registry_execute_handles_exception():
    reg = ToolRegistry()
    td = ToolDefinition(
        name="boom",
        description="boom",
        parameters={},
        handler=lambda: 1 / 0,
    )
    reg.register(td)
    res = reg.execute("boom")
    assert not res.is_ok
    assert "Tool execution failed" in (res.error or "")


def test_get_all_definitions_excludes_internal():
    reg = ToolRegistry()
    td = ToolDefinition(
        name="t",
        description="d",
        parameters={"type": "object"},
        handler=lambda: None,
        is_concurrency_safe=True,
        finalize_content=lambda a, v: None,
        present_call=lambda a: None,
        present_result=lambda a, v: None,
    )
    reg.register(td)
    defs = reg.get_all_definitions()
    assert len(defs) == 1
    assert "finalize_content" not in defs[0]
    assert "present_call" not in defs[0]
    assert "present_result" not in defs[0]
    assert "handler" not in defs[0]


def test_is_concurrency_safe_default_false():
    td = ToolDefinition(name="t", description="d", parameters={}, handler=lambda: None)
    assert td.is_concurrency_safe is False


def test_is_concurrency_safe_override():
    td = ToolDefinition(
        name="t", description="d", parameters={},
        handler=lambda: None,
        is_concurrency_safe=True,
    )
    assert td.is_concurrency_safe is True


def test_present_call_result_callbacks():
    def present_call(args):
        return {"pending_card": args}

    def present_result(args, value):
        return {"done_card": value}

    td = ToolDefinition(
        name="t", description="d", parameters={},
        handler=lambda: "ok",
        present_call=present_call,
        present_result=present_result,
    )
    assert td.present_call({"x": 1}) == {"pending_card": {"x": 1}}
    assert td.present_result({"x": 1}, "ok") == {"done_card": "ok"}


def test_register_unregister_get():
    reg = ToolRegistry()
    td1 = ToolDefinition(name="a", description="a", parameters={}, handler=lambda: 1)
    td2 = ToolDefinition(name="b", description="b", parameters={}, handler=lambda: 2)
    reg.register(td1)
    reg.register(td2)
    assert reg.has_tool("a")
    assert reg.has_tool("b")
    assert not reg.has_tool("c")
    assert reg.get("a").data == td1
    assert reg.get("b").data == td2
    reg.unregister("a")
    assert not reg.has_tool("a")


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