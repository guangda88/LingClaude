from __future__ import annotations

import pytest

from lingclaude.engine.mcp_proxy import (
    MCPServerInfo,
    ToolCallResult,
    register_server,
    find_server,
    list_all_tools,
    list_servers,
    get_stats,
    call_tool,
    call_tool_async,
    clear_cache,
    _SERVERS,
)


@pytest.fixture(autouse=True)
def _clean_servers():
    _SERVERS.clear()
    clear_cache()
    yield
    _SERVERS.clear()
    clear_cache()


def _register_test_servers() -> None:
    register_server(
        key="test_agent_a",
        name="测试AgentA",
        agent_id="agent_a",
        tools=["tool_read", "tool_write", "tool_search"],
    )
    register_server(
        key="test_agent_b",
        name="测试AgentB",
        agent_id="agent_b",
        tools=["tool_bash", "tool_git_status"],
    )
    register_server(
        key="test_agent_c",
        name="测试AgentC",
        agent_id="agent_c",
        tools=["tool_analyze", "tool_report"],
    )


class TestRegisterServer:
    def test_register_single(self) -> None:
        register_server("s1", "Server1", "s1", tools=["a", "b"])
        info = find_server("a")
        assert info is not None
        assert info.key == "s1"
        assert info.name == "Server1"
        assert "a" in info.tools
        assert "b" in info.tools

    def test_register_overwrites(self) -> None:
        register_server("s1", "Old", "s1", tools=["x"])
        register_server("s1", "New", "s1", tools=["y"])
        info = find_server("y")
        assert info is not None
        assert info.name == "New"

    def test_register_with_working_dir(self) -> None:
        register_server("s1", "S", "s1", tools=["t"], working_dir="/tmp")
        servers = list_servers()
        assert len(servers) == 1
        assert servers[0].working_dir == "/tmp"


class TestFindServer:
    def test_find_existing_tool(self) -> None:
        _register_test_servers()
        info = find_server("tool_bash")
        assert info is not None
        assert info.key == "test_agent_b"

    def test_find_first_match(self) -> None:
        _register_test_servers()
        info = find_server("tool_read")
        assert info is not None
        assert info.key == "test_agent_a"

    def test_find_missing_returns_none(self) -> None:
        _register_test_servers()
        assert find_server("nonexistent") is None

    def test_find_empty_registry(self) -> None:
        assert find_server("anything") is None


class TestListAllTools:
    def test_lists_all_unique(self) -> None:
        _register_test_servers()
        tools = list_all_tools()
        assert len(tools) == 7
        assert "tool_read" in tools
        assert "tool_bash" in tools
        assert "tool_analyze" in tools

    def test_deduplicates(self) -> None:
        register_server("s1", "A", "a", tools=["dup_tool"])
        register_server("s2", "B", "b", tools=["dup_tool"])
        tools = list_all_tools()
        assert tools.count("dup_tool") == 1

    def test_empty_registry(self) -> None:
        assert list_all_tools() == ()


class TestListServers:
    def test_returns_all(self) -> None:
        _register_test_servers()
        servers = list_servers()
        assert len(servers) == 3

    def test_empty(self) -> None:
        assert list_servers() == ()


class TestGetStats:
    def test_stats(self) -> None:
        _register_test_servers()
        stats = get_stats()
        assert stats["total_servers"] == 3
        assert stats["total_tools"] == 7
        assert stats["by_agent"]["测试AgentA"] == 3
        assert stats["by_agent"]["测试AgentB"] == 2

    def test_empty(self) -> None:
        stats = get_stats()
        assert stats["total_servers"] == 0
        assert stats["total_tools"] == 0


class TestCallTool:
    def test_call_unknown_tool(self) -> None:
        result = call_tool("nonexistent")
        assert result.is_error

    def test_call_tool_no_server(self) -> None:
        result = call_tool("ghost_tool")
        assert result.is_error or (result.is_ok and not result.data.success)

    def test_call_tool_with_mock_module(self, tmp_path) -> None:
        server_py = tmp_path / "mock_server.py"
        server_py.write_text(
            "from fastmcp import FastMCP\n"
            "mcp = FastMCP('mock')\n"
            "@mcp.tool()\n"
            "def greet(name: str) -> str:\n"
            "    return f'hello {name}'\n"
        )
        register_server(
            "mock",
            "Mock",
            "mock",
            tools=["greet"],
            working_dir=str(tmp_path),
            module_path=str(server_py),
        )
        result = call_tool("greet", name="灵克")
        assert result.is_ok
        data = result.data
        assert data.success is True
        assert "灵克" in data.output
        assert data.server_key == "mock"
        assert data.duration_ms >= 0


class TestCallToolAsync:
    @pytest.mark.asyncio
    async def test_async_call_unknown(self) -> None:
        result = await call_tool_async("nonexistent")
        assert result.is_error


class TestClearCache:
    def test_clear(self) -> None:
        register_server("s1", "S", "s1", tools=["t"])
        clear_cache()
        from lingclaude.engine.mcp_proxy import _cache
        assert len(_cache._modules) == 0
        assert len(_cache._functions) == 0


class TestInitFromLingflowRegistry:
    def test_init(self) -> None:
        from lingclaude.engine.mcp_proxy import init_from_lingflow_registry
        count = init_from_lingflow_registry()
        assert count >= 10
        info = find_server("add_memo")
        assert info is not None
        assert info.key == "lingyi"
        tools = list_all_tools()
        assert len(tools) == 152

    def test_stats_after_init(self) -> None:
        from lingclaude.engine.mcp_proxy import init_from_lingflow_registry
        init_from_lingflow_registry()
        stats = get_stats()
        assert stats["total_servers"] >= 12
        assert stats["total_tools"] == 152

    def test_find_tool_across_servers(self) -> None:
        from lingclaude.engine.mcp_proxy import init_from_lingflow_registry
        init_from_lingflow_registry()
        assert find_server("read_file").key == "lingke"
        assert find_server("list_skills").key == "lingtong"
        assert find_server("add_memo").key == "lingyi"
        assert find_server("execute_command").key == "lingxi"
        assert find_server("hello_world").key == "zhibridge"
