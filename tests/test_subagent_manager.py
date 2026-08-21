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
