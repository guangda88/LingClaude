from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from lingclaude.core.types import Result

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MCPServerInfo:
    key: str
    name: str
    agent_id: str
    working_dir: str | None = None
    module_path: str | None = None
    tools: tuple[str, ...] = ()


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
) -> None:
    _SERVERS[key] = MCPServerInfo(
        key=key,
        name=name,
        agent_id=agent_id,
        working_dir=working_dir,
        module_path=module_path,
        tools=tuple(tools),
    )


def find_server(tool_name: str) -> MCPServerInfo | None:
    for info in _SERVERS.values():
        if tool_name in info.tools:
            return info
    return None


def list_all_tools() -> tuple[str, ...]:
    seen: set[str] = set()
    for info in _SERVERS.values():
        for t in info.tools:
            seen.add(t)
    return tuple(sorted(seen))


def list_servers() -> tuple[MCPServerInfo, ...]:
    return tuple(_SERVERS.values())


def get_stats() -> dict[str, Any]:
    return {
        "total_servers": len(_SERVERS),
        "total_tools": len(list_all_tools()),
        "by_agent": {
            info.name: len(info.tools) for info in _SERVERS.values()
        },
    }


_cache = _ModuleCache()


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
    "lingyi": ("lingyi.mcp_server",),
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
    import time

    server = find_server(tool_name)
    if server is None:
        return Result.fail(
            f"No server found for tool: {tool_name}",
            code="TOOL_NOT_FOUND",
        )

    fn = _get_tool_function(server, tool_name)
    if fn is None:
        return Result.fail(
            f"Could not load tool function: {tool_name} from {server.name}",
            code="MODULE_LOAD_FAILED",
        )

    t0 = time.monotonic()
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


def clear_cache() -> None:
    _cache._modules.clear()
    _cache._functions.clear()


def init_from_lingflow_registry() -> int:
    try:
        from lingflow_plus.mcp_registry import MCP_SERVERS as REGISTRY
    except ImportError:
        logger.warning("MCP Proxy: lingflow_plus not available, skipping registry init")
        return 0

    count = 0
    for key, cfg in REGISTRY.items():
        register_server(
            key=key,
            name=cfg.name,
            agent_id=cfg.agent_id,
            tools=cfg.tools,
            working_dir=cfg.working_dir,
        )
        count += 1
    logger.info("MCP Proxy: initialized %d servers from lingflow_plus registry", count)
    return count
