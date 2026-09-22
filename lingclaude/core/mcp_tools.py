"""MCP 工具 mixin — 工具构建/MCP 注册与发现（从 query_engine 拆出，瘦身）。

QueryEngine 通过多继承接入本 mixin；方法内 self 即 QueryEngine 实例，
依赖其 _runtime/_tool_router/_mcp_initialized 等成员。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from lingclaude.core.types import MAX_TOOLS_PER_REQUEST, ToolDefinition

logger = logging.getLogger(__name__)


class McpToolsMixin:
    """工具列表构建 + MCP 注册/发现。"""

    def _build_openai_tools(self, query: str = "") -> tuple[dict[str, Any], ...] | None:
        if self._runtime is None:
            return None
        tool_defs = list(self._runtime.registry.list_tools())

        mcp_defs = self._build_mcp_tool_defs()
        if mcp_defs:
            tool_defs.extend(mcp_defs)

        # T0-1: plan 模式下过滤模型可见工具列表（只留读域 + plan_mode 自身）
        # is True 严格判定 — MagicMock runtime 的自动属性是 Mock 而非 bool，不能触发过滤
        plan_mode = getattr(self._runtime, "plan_mode", None)
        if plan_mode is not None and getattr(plan_mode, "is_active", False) is True:
            tool_defs = plan_mode.filter_tools(tool_defs)

        if not tool_defs:
            return None
        # S3: 延迟 import 消除 core→engine 倒装 — 常量取值轻量，仅此处使用；
        # 路由本身走 self._tool_router。MAX_TOOLS_PER_REQUEST 已下沉 core.types。
        if not query or len(tool_defs) <= MAX_TOOLS_PER_REQUEST:
            # Schema 透传修复：优先用 t.input_schema（完整 JSON Schema，含 required/type/嵌套），
            # t.parameters 仅作 fallback（旧行为）。断点原在丢弃 input_schema 导致灵信等
            # 带必填参数的 MCP 工具 schema 压平、调用必败（MCP_CALL_FAILED）。
            def _build_schema(t: Any) -> dict[str, Any]:
                full = getattr(t, "input_schema", None)
                if isinstance(full, dict) and full:
                    # input_schema 已是合法 JSON Schema（来自 mcp_client MCPTool.input_schema）
                    # 深拷贝防跨工具共享可变状态
                    return json.loads(json.dumps(full))
                # fallback：从压平的 parameters/required_params 重建
                return {
                    "type": "object",
                    "properties": {k: v for k, v in t.parameters.items()},
                    "required": (
                        list(t.required_params)
                        if getattr(t, "required_params", ())
                        else list(t.parameters.keys())
                    ),
                }

            return tuple(
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": _build_schema(t),
                }
                for t in tool_defs
            )
        result = self._tool_router.route(query, tool_defs)
        logger.info(
            "ToolRouter: %d/%d tools selected for query (categories: %s)",
            result.selected_count, result.total_available,
            ", ".join(c.value for c in result.categories),
        )
        return result.tools

    def _build_mcp_tool_defs(self) -> list[Any]:
        from lingclaude.engine import mcp_proxy

        self._ensure_mcp()
        # 懒发现：只列出已 discover 的工具；未 discover 的 server 首次调用时才触发。
        # 原行为：_ensure_mcp() 里一次性 discover 全部 server（100+ 工具 schema 全量注册）。
        # 新行为：server 元数据已注册，但 tools/list 推迟到首次调用。
        mcp_names = mcp_proxy.list_all_tools()
        if not mcp_names:
            return []

        native_names = {t.name for t in self._runtime.registry.list_tools()}
        server_map: dict[str, str] = {}
        for info in mcp_proxy.list_servers():
            for t in info.tools:
                if t not in server_map:
                    server_map[t] = info.name

        defs: list[ToolDefinition] = []
        for name in mcp_names:
            if name in native_names:
                continue
            server_name = server_map.get(name, "unknown")
            # Schema 透传修复：优先完整 input_schema（保留 required/嵌套结构）
            full_schema: dict[str, Any] = {}
            try:
                server = mcp_proxy._SERVERS.get(server_name)
                if server is not None:
                    full_schema = server.tool_schemas.get(name) or {}
            except Exception:
                full_schema = {}
            try:
                props, required = mcp_proxy.get_tool_schema(name)
            except Exception:
                props, required = {}, []
            # 若拿到完整 schema 则直接透传；否则退回 parameters+required_params 重建
            if full_schema:
                defs.append(ToolDefinition(
                    name=name,
                    description=f"[MCP:{server_name}] {name}",
                    parameters={},  # 不再用压平 properties，走 input_schema 路径
                    required_params=tuple(required),
                    input_schema=full_schema,
                ))
            else:
                defs.append(ToolDefinition(
                    name=name,
                    description=f"[MCP:{server_name}] {name}",
                    parameters=dict(props),
                    required_params=tuple(required),
                ))
        return defs

    def _ensure_mcp(self) -> None:
        if self._mcp_initialized:
            return
        self._mcp_initialized = True
        from lingclaude.engine import mcp_proxy

        try:
            mcp_proxy.init_from_lingflow_registry()
        except Exception:
            logger.debug("MCP proxy registry init skipped")
        # T1-5 深化: 批量注册 LACP manifest 中声明 MCP transport 的插件
        try:
            from lingclaude.lacp.manifest import scan_and_register_mcp_plugins
            # 扫描 manifest 目录并注册（不阻塞主流程，失败静默）
            count = scan_and_register_mcp_plugins([])
            if count > 0:
                logger.info("T1-5: registered %d MCP servers from LACP manifests", count)
        except Exception as e:
            logger.debug("LACP MCP manifest scan skipped: %s", e)
        # T1-5 深化: 不再立即 discover。懒发现——首次调用时才触发 tools/list。
        # 原逻辑在这里 self._discover_mcp_tools() 一次性 spawn 全部 stdio/http server，
        # 100+ schema 全量注册进函数清单，是启动/首次工具调用的大头。
        # 改为只登记 server 元数据，真正 discover 推迟到首次 call。

    def _discover_mcp_tools(self) -> None:
        """[保留接口兼容] 对全部空 server 执行 tools/list。

        已废弃为内部兼容路径。正常流程走 _lazy_discover(server_key)。
        """
        from lingclaude.engine.mcp_client import discover_and_register
        from lingclaude.engine import mcp_proxy

        empty_servers = [
            s for s in mcp_proxy.list_servers()
            if s.transport in ("stdio", "http") and not s.tools
        ]
        if not empty_servers:
            return

        def _discover_one(server_key: str, command: tuple[str, ...], url: str | None, cwd: str | None) -> None:
            try:
                if command:
                    success, names, schemas = discover_and_register(
                        key=server_key,
                        name=f"mcp-{server_key}",
                        transport="stdio",
                        command=list(command),
                        cwd=cwd,
                    )
                elif url:
                    success, names, schemas = discover_and_register(
                        key=server_key,
                        name=f"mcp-{server_key}",
                        transport="http",
                        url=url,
                    )
                else:
                    return
                if success:
                    # T1-5 深化: 将 inputSchema 写入 server.tool_schemas
                    server = mcp_proxy._SERVERS.get(server_key)
                    if server is not None:
                        server.tool_schemas.update(schemas)
                        # 更新 tools 列表
                        server.tools = tuple(names)
                    logger.info("MCP tools/list discovered %d tools for %s", len(names), server_key)
            except Exception as e:
                logger.warning("MCP tools/list discovery failed for %s: %s", server_key, e)

        # 并行发现（limit 3 避免并发过多）
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(3, len(empty_servers))) as pool:
            futures = []
            for s in empty_servers:
                fut = pool.submit(_discover_one, s.key, s.command, s.url, s.working_dir)
                futures.append(fut)
            for fut in concurrent.futures.as_completed(futures, timeout=30):
                try:
                    fut.result()
                except Exception:
                    pass
