from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
import logging
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from lingclaude.core.types import Result

logger = logging.getLogger(__name__)


@dataclass
class MCPServerInfo:
    key: str
    name: str
    agent_id: str
    working_dir: str | None = None
    module_path: str | None = None
    tools: tuple[str, ...] = ()
    # T1-5 深化: tools/list 发现的 inputSchema 缓存
    tool_schemas: dict[str, dict[str, Any]] = field(default_factory=dict)
    # T1-5: 标准 MCP client 字段 — transport: "module"(默认,进程内) / "stdio" / "http"
    transport: str = "module"
    command: tuple[str, ...] = ()
    url: str | None = None
    # 2026-09-12（codex 审计 P1-3）：可用性状态 — "available" | "unavailable"
    # stdio 型 server 启动时检查 command[0] 是否在 PATH；不存在 → unavailable，
    # 工具不暴露给模型（消除「假可用」死工具）。
    status: str = "available"
    status_reason: str | None = None


@dataclass
class ToolCallResult:
    success: bool
    output: Any
    server_key: str
    tool_name: str
    error: str | None = None
    duration_ms: float = 0.0


@dataclass
class _ModuleCache:
    _modules: dict[str, Any] = field(default_factory=dict)
    _functions: dict[str, Callable[..., Any]] = field(default_factory=dict)


_SERVERS: dict[str, MCPServerInfo] = {}


def register_server(
    key: str,
    name: str,
    agent_id: str,
    tools: tuple[str, ...] | list[str],
    *,
    working_dir: str | None = None,
    module_path: str | None = None,
    # T1-5: 标准 MCP client 参数
    transport: str = "module",
    command: tuple[str, ...] | list[str] | None = None,
    url: str | None = None,
    tool_schemas: dict[str, dict[str, Any]] | None = None,
) -> None:
    """注册 MCP server。stdio 型启动时检查二进制是否在 PATH（P1-3 假可用修复）。"""
    status = "available"
    status_reason: str | None = None
    if transport == "stdio" and command:
        bin0 = command[0]
        if not shutil.which(bin0):
            status = "unavailable"
            status_reason = f"MCP server binary not found in PATH: {bin0}"
            logger.warning(
                "MCP server %s (%s) unavailable: %s",
                key, name, status_reason,
            )
    _SERVERS[key] = MCPServerInfo(
        key=key,
        name=name,
        agent_id=agent_id,
        working_dir=working_dir,
        module_path=module_path,
        tools=tuple(tools),
        transport=transport,
        command=tuple(command) if command else (),
        url=url,
        tool_schemas=tool_schemas or {},
        status=status,
        status_reason=status_reason,
    )


def unregister_server(key: str) -> bool:
    """按 key 卸载 MCP server（热替换插片用）。

    清理注册表 + 模块/函数缓存（_load_module 的缓存条目）。
    返回是否确实卸载（key 不存在返回 False）。
    """
    removed = _SERVERS.pop(key, None) is not None
    if removed:
        _cache._modules.pop(key, None)
        _cache._functions.pop(key, None)
        logger.info("MCP server 已卸载: %s", key)
    return removed


def find_server(tool_name: str) -> MCPServerInfo | None:
    """按工具名找 server（第一个匹配；冲突时返回第一个，见 find_server_with_conflicts）。"""
    for info in _SERVERS.values():
        if tool_name in info.tools:
            return info
    return None


def find_server_with_conflicts(tool_name: str) -> tuple[MCPServerInfo | None, list[str]]:
    """查找工具归属 + 冲突报告（P2）。

    Returns:
        (primary, conflicts)
        - primary: 第一个注册该工具的 server（与 find_server 语义一致）
        - conflicts: 其它也声明该工具的 server key 列表（空 = 无冲突）
    """
    matches: list[MCPServerInfo] = [
        info for info in _SERVERS.values() if tool_name in info.tools
    ]
    if not matches:
        return None, []
    return matches[0], [m.key for m in matches[1:]]


def list_all_tools() -> tuple[str, ...]:
    """列出所有可用工具（跳过 unavailable server，P1-3 假可用修复）。"""
    seen: set[str] = set()
    for info in _SERVERS.values():
        if info.status == "unavailable":
            continue
        for t in info.tools:
            seen.add(t)
    return tuple(sorted(seen))


def list_servers() -> tuple[MCPServerInfo, ...]:
    return tuple(_SERVERS.values())


def get_stats() -> dict[str, Any]:
    """MCP 运行时统计（P2 扩展：连接池 + 冲突报告 + schema cache）。"""
    from lingclaude.engine.mcp_client import get_client_pool

    # 工具冲突报告：同一工具被多个 server 声明
    tool_owners: dict[str, list[str]] = {}
    for info in _SERVERS.values():
        for t in info.tools:
            tool_owners.setdefault(t, []).append(info.key)
    conflicts = {
        t: owners for t, owners in tool_owners.items() if len(owners) > 1
    }

    pool = get_client_pool()
    return {
        "total_servers": len(_SERVERS),
        "total_tools": len(list_all_tools()),
        "by_agent": {
            info.name: len(info.tools) for info in _SERVERS.values()
        },
        "pool_size": pool.size,
        "schema_cache_entries": len(_schema_cache),
        "tool_conflicts": conflicts,
    }


_cache = _ModuleCache()
# P2 (2026-09-12): 进程级 schema 缓存 — tool_name → (properties, required)
_schema_cache: dict[str, tuple[dict[str, Any], list[str]]] = {}


def _ensure_path(working_dir: str) -> None:
    if working_dir not in sys.path:
        sys.path.insert(0, working_dir)
    src = str(Path(working_dir) / "src")
    if Path(src).exists() and src not in sys.path:
        sys.path.insert(0, src)


def _load_module(server: MCPServerInfo) -> Any:
    if server.key in _cache._modules:
        return _cache._modules[server.key]

    module = None

    if server.module_path:
        p = Path(server.module_path)
        ALLOWED_MODULE_DIRS = {Path("/home/ai/lingclaude"), Path("/home/ai/lingmessage"), Path("/tmp")}  # nosec B108 — /tmp 用于 MCP 模块热加载，已有白名单约束
        if not any(p.resolve().is_relative_to(d) for d in ALLOWED_MODULE_DIRS):
            logger.error("Module path outside allowed dirs: %s", server.module_path)
            return None
        if p.exists():
            spec = importlib.util.spec_from_file_location(
                f"mcp_proxy_{server.key}", p,
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
    elif server.working_dir:
        _ensure_path(server.working_dir)
        module = _try_import(server)

    if module is not None:
        _cache._modules[server.key] = module
        logger.info("MCP Proxy: loaded module for %s", server.name)

    return module


_MODULE_IMPORTS: dict[str, tuple[str, ...]] = {
    "lingyang": ("src.mcp_server",),
    "lingke": ("lingclaude_mcp",),

    "lingtong": ("lingflow_mcp",),
    "lingtongask": ("mcp_server",),
    "lingzhi": ("mcp_servers.zhineng_server",),
    "lingresearch": (),
    "lingmessage_annotate": (),
    "lingmessage_bus": (),
    "lingmessage_signing": (),
    "lingxi": (),
    "zhibridge": (),
    "lingminopt": (),
}


def _try_import(server: MCPServerInfo) -> Any:
    candidates = _MODULE_IMPORTS.get(server.key, ())
    for mod_name in candidates:
        try:
            return importlib.import_module(mod_name)
        except ImportError:
            continue

    if server.working_dir:
        fallback = Path(server.working_dir) / "mcp_server.py"
        if fallback.exists():
            spec = importlib.util.spec_from_file_location(
                f"mcp_proxy_{server.key}", fallback,
            )
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod

    return None


def _get_tool_function(server: MCPServerInfo, tool_name: str) -> Callable[..., Any] | None:
    cache_key = f"{server.key}:{tool_name}"
    if cache_key in _cache._functions:
        return _cache._functions[cache_key]

    module = _load_module(server)
    if module is None:
        return None

    if hasattr(module, "mcp"):
        mcp_instance = module.mcp
        if hasattr(mcp_instance, "_tool_manager"):
            mgr = mcp_instance._tool_manager
            if hasattr(mgr, "_tools") and tool_name in mgr._tools:
                tool_obj = mgr._tools[tool_name]
                fn = getattr(tool_obj, "fn", tool_obj)
                _cache._functions[cache_key] = fn
                return fn

    if hasattr(module, tool_name):
        fn = getattr(module, tool_name)
        if callable(fn):
            _cache._functions[cache_key] = fn
            return fn

    return None


def call_tool(tool_name: str, **kwargs: Any) -> Result[ToolCallResult]:
    server = find_server(tool_name)
    if server is None:
        return Result.fail(
            f"No server found for tool: {tool_name}",
            code="TOOL_NOT_FOUND",
        )
    # P1-3 (2026-09-12): 假可用修复 — 注册时已判 unavailable 的 server 拒绝调用
    if server.status == "unavailable":
        return Result.fail(
            f"MCP server '{server.key}' unavailable: {server.status_reason or 'binary not found'}",
            code="SERVER_UNAVAILABLE",
        )

    t0 = time.monotonic()

    # T1-5: 标准 MCP client 路径（stdio/http transport）
    # P2 (2026-09-12): 连接池复用 — 不再每次新建/关闭 client
    if server.transport in ("stdio", "http"):
        from lingclaude.engine.mcp_client import get_client_pool

        try:
            pool = get_client_pool()
            client_result = pool.get(
                server.key,
                server.transport,
                command=server.command,
                url=server.url,
                cwd=server.working_dir,
            )
            if client_result.is_error:
                return Result.fail(
                    client_result.error or f"{server.transport} connect failed",
                    code="CONNECT_FAILED",
                )
            client = client_result.data
            result = client.call_tool(tool_name, kwargs)
            elapsed = (time.monotonic() - t0) * 1000
            if result.is_error:
                return Result.ok(ToolCallResult(
                    success=False,
                    output=None,
                    server_key=server.key,
                    tool_name=tool_name,
                    error=result.error,
                    duration_ms=elapsed,
                ))
            return Result.ok(ToolCallResult(
                success=True,
                output=result.data,
                server_key=server.key,
                tool_name=tool_name,
                duration_ms=elapsed,
            ))
        except Exception as e:  # noqa: BLE001 — 标准 client 调用异常包装
            elapsed = (time.monotonic() - t0) * 1000
            return Result.ok(ToolCallResult(
                success=False,
                output=None,
                server_key=server.key,
                tool_name=tool_name,
                error=str(e),
                duration_ms=elapsed,
            ))

    fn = _get_tool_function(server, tool_name)
    if fn is None:
        return Result.fail(
            f"Could not load tool function: {tool_name} from {server.name}",
            code="MODULE_LOAD_FAILED",
        )

    try:
        result = fn(**kwargs)
        elapsed = (time.monotonic() - t0) * 1000
        return Result.ok(ToolCallResult(
            success=True,
            output=result,
            server_key=server.key,
            tool_name=tool_name,
            duration_ms=elapsed,
        ))
    except Exception as e:
        elapsed = (time.monotonic() - t0) * 1000
        return Result.ok(ToolCallResult(
            success=False,
            output=None,
            server_key=server.key,
            tool_name=tool_name,
            error=str(e),
            duration_ms=elapsed,
        ))


async def call_tool_async(tool_name: str, **kwargs: Any) -> Result[ToolCallResult]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: call_tool(tool_name, **kwargs))


# ---------------------------------------------------------------------------
# T0-8: MCP 工具参数 schema — 供 query_engine._build_mcp_tool_defs 注入注册
# 优先级：FastMCP Tool.parameters（JSON Schema）> 函数签名推导 > ({}, [])
# ---------------------------------------------------------------------------

_SIGNATURE_TYPE_MAP: dict[type, str] = {
    str: "string", int: "integer", float: "number", bool: "boolean",
    list: "array", tuple: "array", dict: "object", set: "array",
}

# from __future__ import annotations 模块里注解是字符串（PEP 563）
_SIGNATURE_NAME_MAP: dict[str, str] = {
    "str": "string", "int": "integer", "float": "number", "bool": "boolean",
    "list": "array", "tuple": "array", "dict": "object", "set": "array",
    "Any": "string",
}


def _annotation_to_json_type(annotation: Any) -> str:
    if isinstance(annotation, str):
        return _SIGNATURE_NAME_MAP.get(annotation.strip(), "string")
    return _SIGNATURE_TYPE_MAP.get(annotation, "string")


def _schema_from_signature(fn: Callable[..., Any]) -> tuple[dict[str, Any], list[str]]:
    """从 Python 函数签名推导 (properties, required)。无注解参数按 string 处理。"""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return {}, []

    props: dict[str, Any] = {}
    required: list[str] = []
    for pname, param in sig.parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        json_type = "string"
        if param.annotation is not inspect.Parameter.empty:
            json_type = _annotation_to_json_type(param.annotation)
        props[pname] = {"type": json_type}
        if param.default is inspect.Parameter.empty:
            required.append(pname)
    return props, required


def get_tool_schema(tool_name: str) -> tuple[dict[str, Any], list[str]]:
    """取 MCP 工具参数 schema，返回 (properties_map, required_list)。

    优先级：
    1. 进程级 schema cache（P2 新增，避免重复走 FastMCP 私有结构）
    2. server.tool_schemas（tools/list 发现的 inputSchema，stdio/http）
    3. FastMCP Tool.parameters（module transport）
    4. 函数签名推导
    """
    cache_key = f"schema:{tool_name}"
    if cache_key in _schema_cache:
        return _schema_cache[cache_key]

    server = find_server(tool_name)
    if server is None:
        return {}, []

    # T1-5 深化: 优先使用 tools/list 发现的 inputSchema
    tool_schema = server.tool_schemas.get(tool_name)
    if tool_schema and isinstance(tool_schema, dict):
        properties = tool_schema.get("properties", {})
        required = tool_schema.get("required", [])
        if properties:
            result = (dict(properties), list(required))
            _schema_cache[cache_key] = result
            return result

    module = _load_module(server)
    if module is not None and hasattr(module, "mcp"):
        mgr = getattr(module.mcp, "_tool_manager", None)
        tools = getattr(mgr, "_tools", {}) if mgr is not None else {}
        tool_obj = tools.get(tool_name)
        if tool_obj is not None:
            # FastMCP Tool.parameters 属性即完整 JSON Schema
            schema = getattr(tool_obj, "parameters", None)
            if isinstance(schema, dict) and schema.get("properties"):
                result = (dict(schema["properties"]), list(schema.get("required") or []))
                _schema_cache[cache_key] = result
                return result

    fn = _get_tool_function(server, tool_name)
    if fn is None:
        return {}, []
    result = _schema_from_signature(fn)
    _schema_cache[cache_key] = result
    return result


def clear_cache() -> None:
    _cache._modules.clear()
    _cache._functions.clear()
    _schema_cache.clear()


def init_from_lingflow_registry() -> int:
    try:
        from lingflow_plus.mcp_registry import MCP_SERVERS as REGISTRY
    except ImportError:
        logger.warning("MCP Proxy: lingflow_plus not available, skipping registry init")
        return 0

    count = 0
    for key, cfg in REGISTRY.items():
        # 修复:此前丢弃 transport/command/args/url — stdio 型 server(如
        # lingclaude-mcp 的 check_and_optimize)注册后 transport 恒为默认
        # "module" 且无 module_path/working_dir,永远无法加载,静默变死条目。
        transport = cfg.transport.value if hasattr(cfg.transport, "value") else str(cfg.transport)
        command: list[str] = []
        if cfg.command:
            command = [cfg.command, *cfg.args]
        register_server(
            key=key,
            name=cfg.name,
            agent_id=cfg.agent_id,
            tools=cfg.tools,
            working_dir=cfg.working_dir,
            transport=transport,
            command=command,
            url=cfg.url,
        )
        count += 1
    logger.info("MCP Proxy: initialized %d servers from lingflow_plus registry", count)
    return count
