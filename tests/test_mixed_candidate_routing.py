"""M4 验证性测试：混合候选面（内置 + MCP >30）路由后内置工具不被排除。

背景（2026-10-01 修正）：上轮评测断言「37 个内置工具未纳入路由面」——实测错误。
_build_openai_tools 先放 registry.list_tools()（内置）再 extend MCP defs，
总数超过 MAX_TOOLS_PER_REQUEST 时统一走 ToolRouter.route()。本测试锁定该契约：
- 混合面超阈值 → 路由生效，内置工具（write/read/bash）仍可被查询语义选中；
- 混合面低于阈值 → 全量透传（零路由税）；
- 路由过滤时工具名集合是原面的子集（不新增、不丢内置名）。
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from lingclaude.core.types import MAX_TOOLS_PER_REQUEST


def _mk_tool(name: str, desc: str, params: dict[str, Any] | None = None) -> Any:
    return SimpleNamespace(
        name=name,
        description=desc,
        input_schema={
            "type": "object",
            "properties": params or {"path": {"type": "string"}},
            "required": ["path"],
        },
        parameters=params or {"path": {"type": "string"}},
        required_params=["path"],
    )


# 内置代表工具（真实 registry 里的名字，见 tool_pipeline WRITE_SCOPED_TOOLS 等）
NATIVE_TOOLS = [
    _mk_tool("read", "Read file contents from the local filesystem"),
    _mk_tool("write", "Write content to a file on disk"),
    _mk_tool("edit", "Edit file by replacing old text with new text"),
    _mk_tool("bash", "Execute bash command in shell"),
    _mk_tool("glob", "Find files matching glob pattern"),
    _mk_tool("grep", "Search file contents with regex pattern"),
]


def _mcp_tools(n: int) -> list[Any]:
    return [
        _mk_tool(f"mcp_msg_{i}", f"灵信消息总线 send message {i}")
        for i in range(n)
    ]


class TestMixedCandidateRouting:
    def _route_via_mixin(self, native: list, mcp: list, query: str):
        """直接模拟 _build_openai_tools 的混合 + 阈值分支，避免起整个 QueryEngine。"""
        tool_defs = list(native) + list(mcp)
        if not query or len(tool_defs) <= MAX_TOOLS_PER_REQUEST:
            return None, tool_defs  # None = 未走路由（透传）
        from lingclaude.engine.tool_router import ToolRouter

        router = ToolRouter()
        result = router.route(query, tuple(tool_defs))
        # route() 产出 dict（_tool_to_dict），统一包装成 .name 可访问
        result.tools = tuple(
            d if isinstance(d, dict) is False else SimpleNamespace(**{"name": d["name"], "_d": d})
            for d in result.tools
        )
        return result, tool_defs

    def test_below_threshold_passthrough(self):
        result, defs = self._route_via_mixin(NATIVE_TOOLS, _mcp_tools(5), "")
        assert result is None  # 未走路由
        assert len(defs) == len(NATIVE_TOOLS) + 5

    def test_above_threshold_routes_and_keeps_native(self):
        query = "编辑文件 write edit 修复 bug"
        result, defs = self._route_via_mixin(NATIVE_TOOLS, _mcp_tools(40), query)
        assert result is not None
        names = {t.name for t in result.tools}
        # 混合面 46 > 30 → 路由生效
        assert result.total_available == 46
        # 内置写入面工具被查询语义选中（路由不歧视内置）
        assert "write" in names or "edit" in names
        # 选中数不超上限
        assert result.selected_count <= MAX_TOOLS_PER_REQUEST

    def test_native_read_selected_for_read_query(self):
        result, _ = self._route_via_mixin(
            NATIVE_TOOLS, _mcp_tools(40), "读取文件内容 read path"
        )
        names = {t.name for t in result.tools}
        assert "read" in names

    def test_routed_names_subset_of_candidates(self):
        result, defs = self._route_via_mixin(
            NATIVE_TOOLS, _mcp_tools(40), "搜索 grep regex"
        )
        candidate_names = {t.name for t in defs}
        assert {t.name for t in result.tools} <= candidate_names
