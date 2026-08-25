"""Tests for SubagentManager (P1-2 registry + factory + orchestrator)."""
from __future__ import annotations

import pytest

from lingclaude.engine.subagent import (
    AcpSubagentBackend,
    InProcessSubagentBackend,
    SubagentBackend,
    SubagentContext,
    SubagentManager,
    SubagentRequest,
    SubagentResult,
)


class StubBackend(SubagentBackend):
    name = "stub"

    def run(self, request: SubagentRequest, ctx: SubagentContext) -> SubagentResult:
        return SubagentResult(
            agent_id="stub-1",
            task=request.task,
            output=f"stub:{request.task}",
            provider=self.name,
        )


class FailingBackend(SubagentBackend):
    name = "failing"

    def run(self, request: SubagentRequest, ctx: SubagentContext) -> SubagentResult:
        raise RuntimeError("boom")


class TestSubagentManagerDefaults:
    def test_builtin_backends_registered(self) -> None:
        m = SubagentManager()
        assert isinstance(m.get_backend(None), InProcessSubagentBackend)
        assert isinstance(m.get_backend("inprocess"), InProcessSubagentBackend)
        assert isinstance(m.get_backend("acp"), AcpSubagentBackend)

    def test_unknown_provider_falls_back_to_default(self) -> None:
        m = SubagentManager()
        assert isinstance(m.get_backend("nope"), InProcessSubagentBackend)


class TestSubagentManagerRegistration:
    def test_register_instance(self) -> None:
        m = SubagentManager()
        m.register(StubBackend())
        assert isinstance(m.get_backend("stub"), StubBackend)

    def test_register_class_with_aliases(self) -> None:
        m = SubagentManager()
        m.register(StubBackend, names=("stub", "stub-alias"))
        assert isinstance(m.get_backend("stub"), StubBackend)
        assert isinstance(m.get_backend("stub-alias"), StubBackend)

    def test_list_backends(self) -> None:
        m = SubagentManager()
        names = m.list_backends()
        assert "inprocess" in names
        assert "acp" in names


class TestSubagentManagerRun:
    def test_run_inprocess(self) -> None:
        m = SubagentManager()
        req = SubagentRequest(task="hello", provider="inprocess")
        ctx = SubagentContext(runtime=object(), model_provider=object())
        result = m.run(req, ctx)
        assert result.provider == "inprocess"

    def test_run_stub(self) -> None:
        m = SubagentManager()
        m.register(StubBackend)
        req = SubagentRequest(task="x", provider="stub")
        result = m.run(req, object())
        assert result.success is True
        assert result.output == "stub:x"

    def test_run_converges_backend_exception(self) -> None:
        m = SubagentManager()
        m.register(FailingBackend)
        req = SubagentRequest(task="x", provider="failing")
        result = m.run(req, object())
        assert result.success is False
        assert "boom" in (result.error or "")

    def test_run_backend_without_provider_fills_backend_name(self) -> None:
        class NoProviderBackend(SubagentBackend):
            name = "noprov"

            def run(self, request, ctx):  # type: ignore[no-untyped-def]
                return SubagentResult(agent_id="n", task=request.task, output="o")

        m = SubagentManager()
        m.register(NoProviderBackend)
        result = m.run(SubagentRequest(task="t", provider="noprov"), object())
        assert result.provider == "noprov"

    def test_run_uses_resolved_default_for_none(self) -> None:
        m = SubagentManager(default="acp")
        # default acp has no live server; run should still resolve to AcpSubagentBackend
        assert isinstance(m.get_backend(None), AcpSubagentBackend)


# ── 任务3: SubagentCapabilities 4 flag 验收 ────────────────────────────────


class TestSubagentCapabilitiesFlags:
    """DSH SubagentCapabilities 4 flag: outputSchema / depthLimit / toolFilter / persona"""

    def test_output_schema_field_exists(self):
        from lingclaude.engine.subagent.base import SubagentRequest
        req = SubagentRequest(task="test", output_schema='{"type":"object"}')
        assert req.output_schema == '{"type":"object"}'

    def test_depth_limit_field_exists(self):
        from lingclaude.engine.subagent.base import SubagentRequest
        req = SubagentRequest(task="test", depth_limit=3)
        assert req.depth_limit == 3

    def test_tool_filter_field_exists(self):
        from lingclaude.engine.subagent.base import SubagentRequest
        req = SubagentRequest(task="test", tool_filter=("read", "grep"))
        assert req.tool_filter == ("read", "grep")

    def test_persona_field_exists(self):
        from lingclaude.engine.subagent.base import SubagentRequest
        req = SubagentRequest(task="test", persona="你是一个代码审查员")
        assert req.persona == "你是一个代码审查员"

    def test_context_has_current_depth(self):
        from lingclaude.engine.subagent.base import SubagentContext
        ctx = SubagentContext(current_depth=2, parent_agent_id="lingke-123")
        assert ctx.current_depth == 2
        assert ctx.parent_agent_id == "lingke-123"


class TestSubagentManagerDepthEnforcement:
    """manager.run 应在 current_depth >= depth_limit 时拒绝执行"""

    def test_depth_limit_blocks_execution(self):
        from lingclaude.engine.subagent.base import (
            SubagentContext,
            SubagentRequest,
            SubagentStatus,
        )
        from lingclaude.engine.subagent.manager import SubagentManager

        mgr = SubagentManager()
        # 注册一个假 backend（不调用 run）
        class _Never:
            name = "never"
            def run(self, request, ctx):
                raise AssertionError("backend.run 不应在 depth 拦截前被调用")
        mgr.register(_Never(), names=("never",))

        req = SubagentRequest(task="test", provider="never", depth_limit=2)
        ctx = SubagentContext(current_depth=2)
        result = mgr.run(req, ctx)
        assert result.success is False
        assert "depth limit exceeded" in result.error
        assert result.status == SubagentStatus.FAILED

    def test_tool_filter_derives_allowed_tools(self):
        """tool_filter 应替换 ctx.allowed_tools"""
        from lingclaude.engine.subagent.base import (
            SubagentContext,
            SubagentRequest,
        )
        from lingclaude.engine.subagent.manager import SubagentManager

        mgr = SubagentManager()
        captured = {}

        class _Capture:
            name = "capture"
            def run(self, request, ctx):
                captured["allowed_tools"] = ctx.allowed_tools
                from lingclaude.engine.subagent.base import SubagentResult
                return SubagentResult(agent_id="x", task=request.task, output="ok")

        mgr.register(_Capture(), names=("capture",))
        req = SubagentRequest(task="t", provider="capture", tool_filter=("read",))
        ctx = SubagentContext(allowed_tools=("read", "grep", "glob"))
        mgr.run(req, ctx)
        assert captured["allowed_tools"] == ("read",)

    def test_persona_injected_into_config(self):
        from lingclaude.engine.subagent.base import (
            SubagentContext,
            SubagentRequest,
        )
        from lingclaude.engine.subagent.manager import SubagentManager

        mgr = SubagentManager()
        captured = {}

        class _Capture:
            name = "capture"
            def run(self, request, ctx):
                captured["persona"] = request.config.get("_persona")
                from lingclaude.engine.subagent.base import SubagentResult
                return SubagentResult(agent_id="x", task=request.task, output="ok")

        mgr.register(_Capture(), names=("capture",))
        req = SubagentRequest(task="t", provider="capture", persona="你是审查员")
        ctx = SubagentContext()
        mgr.run(req, ctx)
        assert captured["persona"] == "你是审查员"
