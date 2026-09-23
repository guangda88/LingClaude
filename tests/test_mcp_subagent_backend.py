"""MCPSubagentBackend 测试 — 第三后端（MCP server → SubagentBackend 适配器）。

用 FakeClient 模拟 MCP server（不 spawn 真实子进程，快且稳）：
- 提供 run/abort/status tools
- 可配置失败模式（connect 失败 / run 返回 error / 超长输出）
"""

from __future__ import annotations

import pytest

from lingclaude.core.seam import SeamRegistry, SeamType
from lingclaude.engine.mcp_client import MCPTool, Result
from lingclaude.engine.subagent import (
    MCPBackendConfig,
    MCPSubagentBackend,
    SubagentContext,
    SubagentManager,
    SubagentRequest,
    SubagentStatus,
)


class FakePool:
    """模拟 MCPClientPool：直接返回 FakeClient，不缓存、不 spawn。"""

    def __init__(self, client) -> None:
        self._client = client

    def get(self, key, transport, **kwargs):
        if self._client is None:
            return Result.fail("connection refused", code="CONNECT_FAILED")
        return Result.ok(self._client)


class FakeClient:
    """模拟 MCPStdioClient：list_tools + call_tool 固定行为。"""

    def __init__(
        self,
        tools: list[str] | None = None,
        run_result=None,
        run_error: str | None = None,
        long_output: bool = False,
    ) -> None:
        self._tools = tools or ["run", "abort", "status"]
        self._run_result = run_result
        self._run_error = run_error
        self._long_output = long_output
        self.called: list[tuple[str, dict]] = []

    def list_tools(self) -> Result[list[MCPTool]]:
        return Result.ok([MCPTool(name=t) for t in self._tools])

    def call_tool(self, name: str, arguments: dict) -> Result:
        self.called.append((name, arguments))
        if name == "run":
            if self._run_error:
                return Result.fail(self._run_error, code="TOOL_ERROR")
            if self._run_result is not None:
                return Result.ok(self._run_result)
            output = "x" * 20000 if self._long_output else "done"
            return Result.ok({"output": output, "success": True, "agent_id": "fake-1", "status": "completed"})
        if name == "abort":
            return Result.ok(True)
        if name == "status":
            return Result.ok("running")
        return Result.fail(f"unknown tool {name}", code="TOOL_NOT_FOUND")


def _backend(client=None, **cfg_kwargs) -> MCPSubagentBackend:
    cfg = MCPBackendConfig(transport="stdio", command=("python", "fake_server.py"), **cfg_kwargs)
    return MCPSubagentBackend(config=cfg, pool=FakePool(client))


def _req(**kwargs) -> SubagentRequest:
    defaults = dict(task="hello", provider="mcp")
    defaults.update(kwargs)
    return SubagentRequest(**defaults)


class TestMCPBackendConfig:
    def test_requires_command_for_stdio(self) -> None:
        with pytest.raises(ValueError):
            MCPBackendConfig(transport="stdio", command=())

    def test_requires_url_for_http(self) -> None:
        with pytest.raises(ValueError):
            MCPBackendConfig(transport="http", url="")

    def test_rejects_unknown_transport(self) -> None:
        with pytest.raises(ValueError):
            MCPBackendConfig(transport="carrier-pigeon", command=("x",))

    def test_key_generation(self) -> None:
        b = _backend(None)
        assert b.key == "mcp:python fake_server.py"


class TestMCPBackendRun:
    def test_run_success(self) -> None:
        client = FakeClient()
        backend = _backend(client)
        result = backend.run(_req(), SubagentContext())
        assert result.success is True
        assert result.output == "done"
        assert result.provider == "mcp"
        assert result.status == SubagentStatus.COMPLETED
        # 协议：run 工具收到 task/context/max_rounds
        name, args = client.called[0]
        assert name == "run"
        assert args["task"] == "hello"
        assert args["max_rounds"] == 5

    def test_run_passes_persona_and_tool_filter(self) -> None:
        client = FakeClient()
        backend = _backend(client)
        req = _req(persona="研究员", tool_filter=("read", "grep"))
        backend.run(req, SubagentContext())
        name, args = client.called[0]
        assert args["persona"] == "研究员"
        assert args["tool_filter"] == ["read", "grep"]

    def test_run_server_reports_error(self) -> None:
        client = FakeClient(run_error="server exploded")
        backend = _backend(client)
        result = backend.run(_req(), SubagentContext())
        assert result.success is False
        assert "server exploded" in (result.error or "")

    def test_run_connect_failure_fail_closed(self) -> None:
        backend = _backend(None)  # pool 返回连接失败
        result = backend.run(_req(), SubagentContext())
        assert result.success is False
        assert "connect failed" in (result.error or "")
        assert result.status == SubagentStatus.FAILED

    def test_run_call_exception_converged(self) -> None:
        class BoomClient(FakeClient):
            def call_tool(self, name, arguments):
                raise RuntimeError("transport died")

        backend = _backend(BoomClient())
        result = backend.run(_req(), SubagentContext())
        assert result.success is False
        assert "transport died" in (result.error or "")

    def test_run_long_output_truncated(self) -> None:
        client = FakeClient(long_output=True)
        backend = _backend(client, max_output_chars=100)
        result = backend.run(_req(), SubagentContext())
        assert len(result.output) <= 100 + len("\n...[truncated]")
        assert result.output.endswith("...[truncated]")

    def test_run_dict_status_mapping(self) -> None:
        client = FakeClient(run_result={"output": "partly", "success": False, "error": "nope", "status": "failed"})
        backend = _backend(client)
        result = backend.run(_req(), SubagentContext())
        assert result.success is False
        assert result.status == SubagentStatus.FAILED
        assert result.output == "partly"


class TestMCPBackendControlChannel:
    def test_abort_via_mcp_tool(self) -> None:
        client = FakeClient()
        backend = _backend(client)
        assert backend.abort("fake-1") is True
        assert any(name == "abort" for name, _ in client.called)

    def test_abort_best_effort_without_tool(self) -> None:
        client = FakeClient(tools=["run"])  # 无 abort tool
        backend = _backend(client)
        assert backend.abort("fake-1") is True  # 本地兜底

    def test_status_via_mcp_tool(self) -> None:
        client = FakeClient()
        backend = _backend(client)
        assert backend.status("fake-1") == SubagentStatus.RUNNING

    def test_status_fallback_local(self) -> None:
        client = FakeClient(tools=["run"])  # 无 status tool
        backend = _backend(client)
        backend.run(_req(), SubagentContext())
        agent_id = backend._local_status and next(iter(backend._local_status))
        # run 后本地有 RUNNING → 查询回退本地
        if agent_id:
            assert backend.status(agent_id) in (SubagentStatus.RUNNING, SubagentStatus.COMPLETED)


class TestMCPBackendManagerInterop:
    def test_register_and_run_via_manager(self) -> None:
        m = SubagentManager()
        backend = _backend(FakeClient())
        m.register(backend)
        req = _req(provider="mcp")
        result = m.run(req, SubagentContext())
        assert result.success is True
        assert result.output == "done"

    def test_register_with_aliases(self) -> None:
        m = SubagentManager()
        m.register(_backend(FakeClient()), names=("mcp", "lingzhi"))
        assert isinstance(m.get_backend("lingzhi"), MCPSubagentBackend)
        assert m.has_backend("mcp")

    def test_not_registered_by_default(self) -> None:
        m = SubagentManager()
        # E5 纪律：MCP 后端不默认注册（未配置即 fail-closed），回退默认后端
        assert not any(b == "mcp" for b in m.list_backends())

    def test_agent_seam_protocol_compliance(self) -> None:
        """MCPSubagentBackend 满足 AgentSeam 协议（Phase 2 契约）。"""
        backend = _backend(FakeClient())
        assert hasattr(backend, "name")
        assert callable(getattr(backend, "run", None))
        assert callable(getattr(backend, "abort", None))
        assert callable(getattr(backend, "status", None))
        # 注册为 AGENT 插片后协议检查通过
        SeamRegistry.register(SeamType.AGENT, "mcp", backend, validate_namespace=False)
        try:
            missing = SeamRegistry.check_protocol(SeamType.AGENT, backend)
            assert missing == []
        finally:
            SeamRegistry.unregister(SeamType.AGENT, "mcp")
