"""P1 (2026-09-12, codex 审计 #2): 强类型工具结果协议测试。

覆盖:
- ToolError / ToolResult / parse_tool_result / is_tool_error 语义
- ToolPipeline.execute_typed 返回强类型结果 + error_code
- ToolExecutor._execute_tool_typed 错误码路径（TOOL_NOT_FOUND fallback 收紧）
- sub_agent._execute_tool_typed 强类型
- 消灭 '"error"' in json 字符串探测的迁移点
"""

from __future__ import annotations

import json

import pytest

from lingclaude.core.types import (
    Result,
    ToolError,
    ToolErrorCode,
    ToolResult,
    is_tool_error,
    parse_tool_result,
)


# === 1. ToolError ===

def test_tool_error_to_dict():
    err = ToolError("boom", code="X", tool_name="read", detail={"a": 1})
    d = err.to_dict()
    assert d["error"] == "boom"
    assert d["error_code"] == "X"
    assert d["tool_name"] == "read"
    assert d["detail"] == {"a": 1}


def test_tool_error_str():
    err = ToolError("msg", code="X")
    assert str(err) == "msg"


# === 2. ToolResult ===

def test_tool_result_ok():
    tr = ToolResult.ok({"content": "hi"})
    assert tr.is_ok and not tr.is_error
    assert tr.to_dict() == {"content": "hi"}


def test_tool_result_ok_scalar():
    tr = ToolResult.ok("just-a-string")
    assert tr.to_dict() == {"result": "just-a-string"}


def test_tool_result_err():
    tr = ToolResult.err("denied", code=ToolErrorCode.PERMISSION_DENIED, tool_name="bash")
    assert tr.is_error and not tr.is_ok
    d = tr.to_dict()
    assert d["error"] == "denied"
    assert d["error_code"] == "PERMISSION_DENIED"


def test_tool_result_from_dict_error():
    tr = ToolResult.from_dict({"error": "no", "error_code": "TOOL_NOT_FOUND"}, tool_name="x")
    assert tr.is_error
    assert tr.error.code == "TOOL_NOT_FOUND"
    assert tr.error.tool_name == "x"


def test_tool_result_from_result():
    r = Result.fail("no file", code="NO_FILE")
    tr = ToolResult.from_result(r, tool_name="read")
    assert tr.is_error
    assert tr.error.code == "NO_FILE"


# === 3. parse_tool_result ===

def test_parse_tool_result_passthrough():
    tr = ToolResult.ok(1)
    assert parse_tool_result(tr) is tr


def test_parse_tool_result_result():
    tr = parse_tool_result(Result.ok({"k": "v"}))
    assert tr.is_ok
    assert tr.data == {"k": "v"}


def test_parse_tool_result_dict_error():
    tr = parse_tool_result({"error": "boom"}, tool_name="w")
    assert tr.is_error
    assert tr.error.tool_name == "w"


def test_parse_tool_result_dict_ok():
    tr = parse_tool_result({"content": "c"})
    assert tr.is_ok


def test_parse_tool_result_tool_error():
    tr = parse_tool_result(ToolError("e", code="E"), tool_name="t")
    assert tr.is_error
    assert tr.error.code == "E"
    assert tr.error.tool_name == "t"


def test_parse_tool_result_arbitrary():
    assert parse_tool_result(42).data == 42


# === 4. is_tool_error ===

def test_is_tool_error_variants():
    assert is_tool_error(ToolResult.err("x"))
    assert not is_tool_error(ToolResult.ok("y"))
    assert is_tool_error(ToolError("z"))
    assert is_tool_error({"error": "boom"})
    assert not is_tool_error({"content": "fine"})
    assert not is_tool_error("plain text")
    assert not is_tool_error(None)


def test_is_tool_error_string_serialized():
    # 老路径：pipeline 返回 JSON 序列化字符串 → 顶层 dict 无 error → 非错误
    s = json.dumps({"content": "ok"})
    assert not is_tool_error(s)
    # 失败序列化 → dict 有 error → 错误
    s2 = json.dumps({"error": "denied", "error_code": "PERMISSION_DENIED"})
    assert is_tool_error(s2)


# === 5. ToolPipeline.execute_typed ===

def _mk_registry_tool(name="t", handler=None, scope="read"):
    from lingclaude.engine.tools import ToolDefinition, ToolRegistry
    if handler is None:
        handler = lambda: {"ok": True}
    reg = ToolRegistry()
    reg.register(ToolDefinition(
        name=name, description="t", parameters={"type": "object"},
        handler=handler, security_scope=scope,
    ))
    return reg


def test_execute_typed_ok():
    from lingclaude.engine.tool_pipeline import ToolPipeline
    reg = _mk_registry_tool()
    p = ToolPipeline(reg)
    tr = p.execute_typed("t", {})
    assert tr.is_ok
    assert tr.data == {"ok": True}


def test_execute_typed_permission_error_code():
    from lingclaude.engine.tool_pipeline import ToolPipeline
    reg = _mk_registry_tool()
    p = ToolPipeline(reg)
    tr = p.execute_typed("t", {}, permissions_blocks=lambda n: True)
    assert tr.is_error
    assert tr.error.code == ToolErrorCode.PERMISSION_DENIED


def test_execute_typed_tool_not_found_code():
    from lingclaude.engine.tool_pipeline import ToolPipeline
    reg = _mk_registry_tool()
    p = ToolPipeline(reg)
    tr = p.execute_typed("nonexistent", {})
    assert tr.is_error
    assert tr.error.code == ToolErrorCode.TOOL_NOT_FOUND


def test_execute_typed_guard_exception_code():
    from lingclaude.engine.tool_pipeline import GuardDecision, ToolPipeline
    reg = _mk_registry_tool()
    p = ToolPipeline(reg)

    def bad_guard(tool_def, ctx):
        raise RuntimeError("guard crashed")

    p.add_guard(bad_guard)
    tr = p.execute_typed("t", {})
    assert tr.is_error
    assert tr.error.code == ToolErrorCode.GUARD_EXCEPTION


def test_execute_typed_dangerous_code():
    from lingclaude.engine.tool_pipeline import ToolPipeline
    reg = _mk_registry_tool("bash")
    p = ToolPipeline(reg, critical_tools=("bash",))
    tr = p.execute_typed("bash", {"command": "rm -rf /"})
    assert tr.is_error
    assert tr.error.code == ToolErrorCode.DANGEROUS_COMMAND


# === 6. ToolExecutor 强类型路径（错误码 + MCP fallback 收紧） ===

class _FakeEngine:
    """最小 QueryEngine 桩（满足 ToolExecutor 依赖面）。"""

    def __init__(self):
        self._tool_call_count = 0
        self._session_cache_hits = 0
        self.config = type("C", (), {"max_tool_calls_per_session": 100})()
        self._cache = type("C", (), {"read_file": lambda self, p: (_raise_not_found(p), False)})()
        self._monitor = type("C", (), {"record_file_read": lambda self, p, c: False})()
        self._dementia_detector = type("C", (), {"record_file_read": lambda self, p: None, "record_tool_call": lambda self, n, a: None})()
        self._runtime = type("C", (), {"execute_tool": self._fake_execute})()

    def _fake_execute(self, name, **kwargs):
        # 返回 pipeline 风格的 dict（含 error_code）
        if name == "blocked":
            return {"error": "Tool blocked by permissions: blocked", "error_code": "PERMISSION_DENIED", "pipeline_aborted": True}
        if name == "notfound":
            return {"error": "Tool not found: notfound", "error_code": "TOOL_NOT_FOUND", "pipeline_aborted": True}
        if name == "denied_text":
            # 老路径：文案含 "not found" 但 error_code 是权限拒绝（此前会误触发 MCP fallback）
            return {"error": "Tool not found/blocked by permissions", "error_code": "PERMISSION_DENIED", "pipeline_aborted": True}
        return {"result": f"ran {name}"}

    def _ensure_mcp(self):
        return None


def _raise_not_found(p):
    raise FileNotFoundError(p)


def test_tool_executor_typed_error_code():
    from lingclaude.core.tool_executor import ToolExecutor
    eng = _FakeEngine()
    te = ToolExecutor(eng)
    tr = te._execute_tool_typed("blocked", "{}")
    assert tr.is_error
    assert tr.error.code == ToolErrorCode.PERMISSION_DENIED


def test_tool_executor_mcp_fallback_only_on_not_found_code():
    """收紧判据：只有 TOOL_NOT_FOUND 才 fallback MCP；权限拒绝文案带 not found 不 fallback。"""
    from lingclaude.core.tool_executor import ToolExecutor
    calls = []
    import lingclaude.engine.mcp_proxy as mp
    orig_call = mp.call_tool

    def spy_call(name, **kw):
        calls.append(name)
        return orig_call(name, **kw)

    mp.call_tool = spy_call
    try:
        eng = _FakeEngine()
        te = ToolExecutor(eng)

        # 1) TOOL_NOT_FOUND → fallback（spy 记录调用）
        tr = te._execute_tool_typed("notfound", "{}")
        assert tr.is_error  # fallback 后 MCP 找不到 server → 仍 error
        assert "notfound" in calls

        # 2) PERMISSION_DENIED（文案含 not found）→ 不 fallback
        calls.clear()
        tr2 = te._execute_tool_typed("denied_text", "{}")
        assert tr2.is_error
        assert tr2.error.code == ToolErrorCode.PERMISSION_DENIED
        assert calls == []  # 未被 MCP 通道绕过
    finally:
        mp.call_tool = orig_call


def test_tool_executor_typed_string_serialization():
    from lingclaude.core.tool_executor import ToolExecutor
    eng = _FakeEngine()
    te = ToolExecutor(eng)
    s = te._execute_tool("blocked", "{}")
    d = json.loads(s)
    # 强类型协议：error_code 是权威错误语义（pipeline_aborted 不再暴露，老字段废弃）
    assert d["error_code"] == "PERMISSION_DENIED"
    assert d["error"] == "Tool blocked by permissions: blocked"


def test_tool_executor_invalid_args_code():
    from lingclaude.core.tool_executor import ToolExecutor
    eng = _FakeEngine()
    te = ToolExecutor(eng)
    tr = te._execute_tool_typed("read", "not-json{{{")
    assert tr.is_error
    assert tr.error.code == ToolErrorCode.INVALID_ARGS


# === 7. sub_agent 强类型 ===

def test_subagent_execute_tool_typed():
    from lingclaude.engine.loop.sub_agent import SubAgent, SubAgentConfig
    eng = _FakeEngine()
    sa = SubAgent(config=SubAgentConfig(), runtime=eng._runtime)
    # 正常工具
    tr = sa._execute_tool_typed("read", json.dumps({"path": "x"}))
    assert tr.is_ok
    # 敏感路径拒绝（.env 是敏感标记）
    from lingclaude.engine.loop.sub_agent import SubAgentConfig as SC
    sa2 = SubAgent(config=SC(), runtime=eng._runtime)
    tr2 = sa2._execute_tool_typed("read", json.dumps({"path": "/tmp/project/.env"}))
    assert tr2.is_error
    assert tr2.error.code == "GUARD_DENIED"


# === 8. P0 主链统一：read 快路径权限预检 ===

class _BlockingRuntime:
    """带 tool_pipeline + _blocks 的 runtime 桩。"""

    def __init__(self, blocked: bool = False):
        self.blocked = blocked
        self.registry = type("R", (), {"get": lambda self, n: type("T", (), {"is_ok": True, "data": type("D", (), {"security_scope": "read"})})()})()
        from lingclaude.engine.tool_pipeline import ToolPipeline
        self.tool_pipeline = ToolPipeline(self.registry)

    def _blocks(self, tool_name: str) -> bool:
        return self.blocked


class _CacheEngine(_FakeEngine):
    def __init__(self, runtime):
        super().__init__()
        self._runtime = runtime


def test_read_fastpath_blocked_by_permission():
    """read 快路径必须过权限预检：blocked=True 时不得读 cache。"""
    from lingclaude.core.tool_executor import ToolExecutor
    calls = []

    class _Cache:
        def read_file(self, path):
            calls.append(path)  # 若被调用则说明绕过权限
            return ("SECRET", False)

    runtime = _BlockingRuntime(blocked=True)
    eng = _CacheEngine(runtime)
    eng._cache = _Cache()
    te = ToolExecutor(eng)
    tr = te._execute_tool_typed("read", json.dumps({"path": "/etc/secret.txt"}))
    assert tr.is_error
    assert tr.error.code == "PERMISSION_DENIED"
    assert calls == []  # cache 未被触碰 → 快路径没有绕过权限


def test_read_fastpath_allowed_reads_cache():
    """read 快路径放行后走 cache。"""
    from lingclaude.core.tool_executor import ToolExecutor
    calls = []

    class _Cache:
        def read_file(self, path):
            calls.append(path)
            return ("content-abc", True)

    runtime = _BlockingRuntime(blocked=False)
    eng = _CacheEngine(runtime)
    eng._cache = _Cache()
    te = ToolExecutor(eng)
    tr = te._execute_tool_typed("read", json.dumps({"path": "/etc/ok.txt"}))
    assert tr.is_ok
    assert tr.data == {"content": "content-abc", "cache_hit": True}
    assert calls == ["/etc/ok.txt"]


def test_read_fastpath_sensitive_path_blocked():
    """read 快路径过 sensitive guard：sensitive 路径被拒绝，不读 cache。"""
    from lingclaude.core.tool_executor import ToolExecutor
    from lingclaude.engine.tool_pipeline import GuardDecision

    calls = []

    class _Cache:
        def read_file(self, path):
            calls.append(path)
            return ("SECRET", False)

    runtime = _BlockingRuntime(blocked=False)

    def sens_guard(tool_def, ctx):
        from lingclaude.engine.sensitive_path_gate import check_sensitive_path
        for cand in (ctx.args.get("path"),):
            if cand and check_sensitive_path(str(cand))[0]:
                return GuardDecision(decision="deny", reason="sensitive path")
        return GuardDecision(decision="abstain")

    runtime.tool_pipeline.add_guard(sens_guard)
    eng = _CacheEngine(runtime)
    eng._cache = _Cache()
    te = ToolExecutor(eng)
    tr = te._execute_tool_typed("read", json.dumps({"path": "/tmp/proj/" + "." + "env"}))
    assert tr.is_error
    assert tr.error.code == "GUARD_DENIED"
    assert calls == []


def test_check_permission_does_not_execute_handler():
    """check_permission 只跑权限段，不执行 handler。"""
    from lingclaude.engine.tool_pipeline import ToolPipeline
    from lingclaude.engine.tools import ToolDefinition, ToolRegistry

    executed = []

    def handler():
        executed.append(True)
        return {"ran": True}

    reg = ToolRegistry()
    reg.register(ToolDefinition(
        name="t", description="t", parameters={"type": "object"},
        handler=handler, security_scope="read",
    ))
    p = ToolPipeline(reg)
    denied = p.check_permission("t", {}, permissions_blocks=lambda n: True)
    assert denied is not None
    assert executed == []  # handler 未被调用
    # 放行
    ok = p.check_permission("t", {}, permissions_blocks=lambda n: False)
    assert ok is None
    assert executed == []
