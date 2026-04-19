from __future__ import annotations

import pytest

from lingclaude.engine.tools import ToolDefinition
from lingclaude.engine.tool_router import (
    ToolCategory,
    ToolManifest,
    ToolRouter,
    RoutingResult,
    create_default_router,
)


def _make_tool(name: str, desc: str = "desc", params: dict | None = None) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=desc,
        parameters=params or {"x": {"type": "string"}},
    )


def _make_large_toolset(n: int, prefix: str = "tool_") -> tuple[ToolDefinition, ...]:
    categories = list(ToolCategory)
    tools = []
    for i in range(n):
        cat = categories[i % len(categories)]
        tools.append(ToolDefinition(
            name=f"{prefix}{cat.value}_{i}",
            description=f"Tool {i} for {cat.value}",
            parameters={"arg": {"type": "string"}},
            security_scope="read",
        ))
    return tuple(tools)


class TestToolCategory:
    def test_is_str_enum(self) -> None:
        assert isinstance(ToolCategory.CORE, str)
        assert ToolCategory.CORE == "core"
        assert ToolCategory.FILESYSTEM == "filesystem"

    def test_all_categories_have_value(self) -> None:
        for cat in ToolCategory:
            assert cat.value
            assert isinstance(cat.value, str)


class TestToolManifest:
    def test_create_manifest(self) -> None:
        m = ToolManifest(name="bash", category=ToolCategory.EXECUTION, tags=("shell",))
        assert m.name == "bash"
        assert m.category == ToolCategory.EXECUTION
        assert m.priority == 5
        assert not m.always_include

    def test_frozen(self) -> None:
        m = ToolManifest(name="read", category=ToolCategory.FILESYSTEM)
        with pytest.raises(AttributeError):
            m.name = "write"  # type: ignore[misc]

    def test_matches_tag(self) -> None:
        m = ToolManifest(name="grep", category=ToolCategory.SEARCH, tags=("content", "regex"))
        assert m.matches_tag("content")
        assert m.matches_tag("regex")
        assert m.matches_tag("missing") is False


class TestRoutingResult:
    def test_reduction_ratio(self) -> None:
        r = RoutingResult(tools=(), categories=(), total_available=100, selected_count=30)
        assert r.reduction_ratio == 0.7

    def test_reduction_ratio_zero_total(self) -> None:
        r = RoutingResult(tools=(), categories=(), total_available=0, selected_count=0)
        assert r.reduction_ratio == 0.0

    def test_no_reduction(self) -> None:
        r = RoutingResult(tools=(), categories=(), total_available=10, selected_count=10)
        assert r.reduction_ratio == 0.0


class TestToolRouter:
    def test_register_and_get_manifest(self) -> None:
        router = ToolRouter()
        router.register_tool("bash", ToolCategory.EXECUTION, tags=("shell",))
        m = router.get_manifest("bash")
        assert m is not None
        assert m.category == ToolCategory.EXECUTION

    def test_get_manifest_missing(self) -> None:
        router = ToolRouter()
        assert router.get_manifest("missing") is None

    def test_list_manifests(self) -> None:
        router = ToolRouter()
        router.register_tool("a", ToolCategory.CORE)
        router.register_tool("b", ToolCategory.SEARCH)
        assert len(router.list_manifests()) == 2

    def test_route_auto_small_toolset_returns_all(self) -> None:
        router = ToolRouter()
        tools = tuple(_make_tool(f"t{i}") for i in range(5))
        result = router.route("test query", tools)
        assert result.selected_count == 5
        assert result.total_available == 5

    def test_route_auto_large_toolset_filters(self) -> None:
        router = ToolRouter(max_tools=10)
        router.register_tool("tool_execution_0", ToolCategory.EXECUTION, tags=("bash", "shell"))
        router.register_tool("tool_filesystem_1", ToolCategory.FILESYSTEM, tags=("file", "read"), always_include=True)
        tools = _make_large_toolset(50)
        result = router.route("run bash command", tools)
        assert result.selected_count <= 10
        assert result.total_available == 50

    def test_route_detects_git_category(self) -> None:
        router = ToolRouter(max_tools=5)
        router.register_tool("git_status", ToolCategory.GIT, tags=("vcs",))
        tools = (
            _make_tool("git_status"),
            _make_tool("bash"),
            _make_tool("read"),
            _make_tool("write"),
            _make_tool("grep"),
        )
        result = router.route("show me the git commit history", tools)
        assert ToolCategory.GIT in result.categories

    def test_route_detects_search_category(self) -> None:
        router = ToolRouter(max_tools=5)
        router.register_tool("grep", ToolCategory.SEARCH, tags=("regex",))
        tools = (_make_tool("grep"), _make_tool("bash"))
        result = router.route("搜索代码中的 TODO 关键词", tools)
        assert ToolCategory.SEARCH in result.categories

    def test_route_detects_filesystem_category(self) -> None:
        router = ToolRouter(max_tools=5)
        router.register_tool("edit", ToolCategory.FILESYSTEM, tags=("file",))
        tools = (_make_tool("edit"), _make_tool("bash"))
        result = router.route("编辑文件内容", tools)
        assert ToolCategory.FILESYSTEM in result.categories

    def test_route_detects_audio_category(self) -> None:
        router = ToolRouter(max_tools=5)
        router.register_tool("stt", ToolCategory.AUDIO, tags=("voice",))
        tools = (_make_tool("stt"), _make_tool("bash"))
        result = router.route("语音转文字", tools)
        assert ToolCategory.AUDIO in result.categories

    def test_route_core_fallback_on_ambiguous_query(self) -> None:
        router = ToolRouter(max_tools=5)
        tools = _make_large_toolset(20)
        result = router.route("hello how are you", tools)
        assert result.selected_count <= 5

    def test_route_all_mode(self) -> None:
        router = ToolRouter(max_tools=3)
        tools = _make_large_toolset(50)
        result = router.route("anything", tools, mode="all")
        assert result.selected_count == 50

    def test_route_core_mode(self) -> None:
        router = ToolRouter()
        router.register_tool("bash", ToolCategory.EXECUTION, always_include=True)
        router.register_tool("read", ToolCategory.FILESYSTEM, always_include=True)
        router.register_tool("grep", ToolCategory.SEARCH, always_include=True)
        tools = (
            _make_tool("bash"),
            _make_tool("read"),
            _make_tool("grep"),
            _make_tool("write"),
            _make_tool("edit"),
        )
        result = router.route("anything", tools, mode="core")
        assert result.selected_count == 3
        names = {t["name"] for t in result.tools}
        assert names == {"bash", "read", "grep"}

    def test_always_include_tools_preserved(self) -> None:
        router = ToolRouter(max_tools=5)
        router.register_tool("bash", ToolCategory.EXECUTION, always_include=True)
        router.register_tool("read", ToolCategory.FILESYSTEM, always_include=True)
        router.register_tool("glob", ToolCategory.SEARCH, always_include=True)
        router.register_tool("grep", ToolCategory.SEARCH, always_include=True)
        tools = (
            _make_tool("bash"),
            _make_tool("read"),
            _make_tool("glob"),
            _make_tool("grep"),
            _make_tool("write"),
            _make_tool("edit"),
            _make_tool("stt"),
            _make_tool("git_status"),
            _make_tool("ast_replace"),
            _make_tool("index_project"),
        )
        result = router.route("random query xyz", tools)
        names = {t["name"] for t in result.tools}
        for name in ("bash", "read", "glob", "grep"):
            assert name in names, f"{name} should always be included"

    def test_priority_ordering(self) -> None:
        router = ToolRouter(max_tools=3)
        router.register_tool("low", ToolCategory.CORE, priority=1)
        router.register_tool("high", ToolCategory.EXECUTION, priority=10)
        router.register_tool("mid", ToolCategory.CORE, priority=5)
        tools = (_make_tool("low"), _make_tool("high"), _make_tool("mid"))
        result = router.route("execute something", tools)
        names = [t["name"] for t in result.tools]
        assert names[0] == "high"

    def test_unregistered_tools_get_default_score(self) -> None:
        router = ToolRouter(max_tools=10)
        tools = (
            _make_tool("unknown_tool"),
            _make_tool("another_unknown"),
        )
        result = router.route("test", tools)
        assert result.selected_count == 2

    def test_tool_dict_format(self) -> None:
        router = ToolRouter()
        tool = _make_tool("test", params={"path": {"type": "string"}, "n": {"type": "integer"}})
        result = router.route("test", (tool,))
        t = result.tools[0]
        assert t["name"] == "test"
        assert t["parameters"]["type"] == "object"
        assert "path" in t["parameters"]["properties"]
        assert set(t["parameters"]["required"]) == {"path", "n"}


class TestDynamicInference:
    def test_auto_infer_git_from_name(self) -> None:
        router = ToolRouter()
        cat = router._infer_category("git_log", "show git commit log")
        assert cat == ToolCategory.GIT

    def test_auto_infer_execution_from_description(self) -> None:
        router = ToolRouter()
        cat = router._infer_category("my_runner", "execute bash shell commands")
        assert cat == ToolCategory.EXECUTION

    def test_auto_infer_filesystem_from_name(self) -> None:
        router = ToolRouter()
        cat = router._infer_category("read_file", "read file content")
        assert cat == ToolCategory.FILESYSTEM

    def test_auto_infer_search_from_name(self) -> None:
        router = ToolRouter()
        cat = router._infer_category("grep_code", "search code patterns")
        assert cat == ToolCategory.SEARCH

    def test_auto_infer_unknown_for_gibberish(self) -> None:
        router = ToolRouter()
        cat = router._infer_category("xyzzy", "plugh")
        assert cat == ToolCategory.UNKNOWN

    def test_learning_enriches_vocab(self) -> None:
        router = ToolRouter()
        git_vocab_before = len(router._vocab[ToolCategory.GIT])
        router._learn(_make_tool("git_stash_list", "list git stashes"))
        git_vocab_after = len(router._vocab[ToolCategory.GIT])
        assert git_vocab_after > git_vocab_before
        assert "stash" in router._vocab[ToolCategory.GIT]

    def test_route_auto_infers_without_manifest(self) -> None:
        router = ToolRouter(max_tools=5)
        tools = (
            _make_tool("git_log", "show git commit history"),
            _make_tool("bash", "execute commands"),
            _make_tool("read_file", "read file content"),
            _make_tool("search_code", "search patterns in code"),
            _make_tool("stt_engine", "speech to text"),
        )
        result = router.route("查看 git log 最近提交", tools)
        names = {t["name"] for t in result.tools}
        assert "git_log" in names

    def test_chinese_query_matches_english_tool(self) -> None:
        router = ToolRouter(max_tools=10)
        tools = (_make_tool("search_code", "search code patterns"),)
        result = router.route("搜索代码", tools)
        assert ToolCategory.SEARCH in result.categories


class TestCreateDefaultRouter:
    def test_core_tools_always_included(self) -> None:
        router = create_default_router()
        for name in ("bash", "read", "glob", "grep"):
            assert name in router._always_include

    def test_no_per_tool_manifests(self) -> None:
        router = create_default_router()
        assert len(router.list_manifests()) == 0

    def test_default_router_routes_with_large_toolset(self) -> None:
        router = create_default_router(max_tools=15)
        from lingclaude.engine.coding import CodingRuntime
        runtime = CodingRuntime()
        tools = runtime.registry.list_tools()
        result = router.route("read the file and edit it", tools)
        assert result.selected_count <= 15
        assert result.total_available == len(tools)

    def test_default_router_git_query(self) -> None:
        router = create_default_router(max_tools=10)
        from lingclaude.engine.coding import CodingRuntime
        runtime = CodingRuntime()
        tools = runtime.registry.list_tools()
        result = router.route("查看 git log 最近5次提交", tools)
        names = {t["name"] for t in result.tools}
        assert "git_log" in names or result.total_available <= 10

    def test_default_router_code_analysis_query(self) -> None:
        router = create_default_router(max_tools=10)
        from lingclaude.engine.coding import CodingRuntime
        runtime = CodingRuntime()
        tools = runtime.registry.list_tools()
        result = router.route("分析这个 Python 文件的函数结构", tools)
        names = {t["name"] for t in result.tools}
        assert "list_functions" in names or result.total_available <= 10

    def test_custom_max_tools(self) -> None:
        router = create_default_router(max_tools=5)
        assert router._max_tools == 5

    def test_no_manifests_still_works(self) -> None:
        router = ToolRouter(max_tools=10)
        tools = _make_large_toolset(50)
        result = router.route("test", tools)
        assert result.selected_count == 10
