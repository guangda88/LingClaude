"""Tool registry with dsh-aligned 4-field ToolDefinition.

LINGKERNEL_v1 task #3 (D+5).
dsh reference: packages/core/tools/src/index.ts ToolDefinition + ToolOutputDefinition.

新增 4 字段：
- output: ToolOutputDefinition (canonical schema + render + presentationMeta)
- is_concurrency_safe: bool (around-dispatch decision)
- finalize_content: callable for last content-only invariant
- present_call / present_result: UI render callbacks

字段命名遵循 dsh 风格 (output, isConcurrencySafe -> is_concurrency_safe,
finalizeContent -> finalize_content, presentCall/Result -> present_call/result).
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from lingclaude.core.seam import SeamRegistry, SeamType
from lingclaude.core.types import Result, ToolDefinition, ToolOutputDefinition

logger = logging.getLogger(__name__)

# ── 2026-09-24 启动提速第二轮: 工具插片装载惰性化 ──────────────────────────
# 此前 _load_tool_plugins 在 CodingRuntime 构造期同步 exec_module 6 个插片
# （全部用户首输入前必付）。现改为：装配期挂一个轻量句柄（LazyToolPlugins），
# 真实装载收口到本函数 —— warm 线程提前热身 + 首次 execute_tool miss 兜底
# 共用同一个双检锁函数，保证 exactly-once、无并发重复装载。
_PLUGINS_DIR = "lingclaude/plugins/tools"
_PLUGIN_LOAD_LOCK = threading.Lock()
_PLUGIN_LOAD_DONE = False
_PLUGIN_LOAD_ERROR = ""


def _ensure_tool_plugins_loaded() -> bool:
    """工具插片惰性装载（收口点，幂等）。

    返回是否最终处于「已装载」状态。fail-soft：目录缺失/装载异常 →
    记 _PLUGIN_LOAD_ERROR 后返回 False，主干 execute 回退内部 handler
    （对齐 _load_tool_plugins docstring 的 fail-soft 纪律，不影响 SPECS 34 工具）。
    """
    global _PLUGIN_LOAD_DONE, _PLUGIN_LOAD_ERROR
    if _PLUGIN_LOAD_DONE:
        return True
    with _PLUGIN_LOAD_LOCK:
        if _PLUGIN_LOAD_DONE:  # 双检锁: warm 线程与首调用兜底竞速时 exactly-once
            return True
        try:
            from lingclaude.core.plugin_loader import PluginLoader  # S3 纪律: 函数内延迟 import

            results = PluginLoader().load_plugins_from_dir(_PLUGINS_DIR)
            loaded = [n for n, r in results.items() if r.is_ok]
            if results:
                logger.info(
                    "ToolRegistry: 工具插件目录 %s 惰性装载 %d 个: %s",
                    _PLUGINS_DIR, len(loaded), ", ".join(sorted(loaded)),
                )
            _PLUGIN_LOAD_DONE = True
        except Exception as e:  # noqa: BLE001 — fail-soft，回退内部 handler
            _PLUGIN_LOAD_ERROR = f"{type(e).__name__}: {e}"
            logger.warning("ToolRegistry: 工具插件惰性装载失败（fail-soft）: %s", _PLUGIN_LOAD_ERROR)
        return _PLUGIN_LOAD_DONE


class _ToolSeamProxy:
    """P5: SeamRegistry.TOOL 槽位的可执行代理 —— 满足 ToolPlugin 协议。

    ToolDefinition 是 schema 定义（handler 由注册表按 handler_name 解析），
    不直接可执行；本代理把 execute 委托回 ToolRegistry（含 handler 解析 + Result 语义），
    使 SeamRegistry.get(TOOL, name) 返回的实体可直接调用。
    """

    __slots__ = ("_registry", "_name")

    def __init__(self, registry: "ToolRegistry", name: str) -> None:
        self._registry = registry
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def execute(self, **kwargs: Any) -> Any:
        return self._registry.execute(self._name, **kwargs)

    def __repr__(self) -> str:
        return f"<ToolSeamProxy {self._name} on {id(self._registry):x}>"


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        # P1 解耦: handler 注册表 — ToolDefinition 可通过 handler_name 按名引用，
        # 定义（schema）与实现（handler Callable）解耦；同一 handler_name 可被多个 ToolDefinition 复用。
        self._handlers: dict[str, Callable[..., Any]] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool
        # P5: 同步到进程内 SeamRegistry（统一查询视图 —— SeamRegistry.get(TOOL, name)）
        # 注册 _ToolSeamProxy：满足 ToolPlugin 协议（name + execute），execute 委托本注册表。
        # 与 ProviderRegistry 相同覆盖语义（同名覆盖 = 热更）。
        SeamRegistry.register(SeamType.TOOL, tool.name, _ToolSeamProxy(self, tool.name))

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)
        # P5: 同步注销（热拔插 —— unregister 只影响后续 get，已持引用不受影响）
        SeamRegistry.unregister(SeamType.TOOL, name)

    def get(self, name: str) -> Result[ToolDefinition]:
        tool = self._tools.get(name)
        if tool is None:
            return Result.fail(f"Tool not found: {name}", code="NOT_FOUND")
        return Result.ok(tool)

    def list_tools(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._tools.values())

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def execute(self, name: str, **kwargs: Any) -> Result[Any]:
        tool = self._tools.get(name)
        if tool is None:
            return Result.fail(f"Tool not found: {name}", code="NOT_FOUND")
        # 2026-09-24 启动提速: 惰性装载兜底（miss 回填）。插件装载已移出构造期,
        # 若首调用时 warm 线程尚未装完, 此处同步补装再走 seam (仅对内部已知工具
        # 回填; NOT_FOUND 早退在前, 未知名不触发装载)。
        _ensure_tool_plugins_loaded()
        # Q1 (2026-09-14): 主干热路径走 seam —— 同名覆盖即热拔插。
        # 先查 SeamRegistry.TOOL 槽位。但本注册表 register() 时自己会注册指向自身的
        # _ToolSeamProxy（tools.py register），若直接走它会造成 execute→proxy→execute 死循环。
        # 因此只有当 seam 里命中的是「外部换血代理」（非指向本 registry 的影子）才优先走；
        # 指向自身的影子 / miss 一律回退内部 handler 解析（graceful degrade）。
        proxy = SeamRegistry.get_optional(SeamType.TOOL, name)
        if proxy is not None and hasattr(proxy, "execute"):
            if not (isinstance(proxy, _ToolSeamProxy) and proxy._registry is self):
                try:
                    return Result.ok(proxy.execute(**kwargs))
                except Exception as e:
                    return Result.fail(f"Tool execution failed (seam proxy): {e}", code="EXECUTION_ERROR")
        # P1 解耦: 解析 handler — 优先 Callable（向后兼容），其次 handler_name 按名查找
        handler = self._resolve_handler(tool)
        if handler is None:
            return Result.fail(f"Tool has no handler: {name}", code="NO_HANDLER")
        try:
            return Result.ok(handler(**kwargs))
        except Exception as e:
            return Result.fail(f"Tool execution failed: {e}", code="EXECUTION_ERROR")

    def _resolve_handler(self, tool: ToolDefinition) -> Callable[..., Any] | None:
        """P1 解耦: 解析 handler — handler > handler_name > None"""
        if tool.handler is not None:
            return tool.handler
        if tool.handler_name is not None:
            return self._handlers.get(tool.handler_name)
        return None

    def register_handler(self, handler_name: str, handler: Callable[..., Any]) -> None:
        """P1 解耦: 注册 handler Callable（按名）— 后续 ToolDefinition 可通过 handler_name 引用。"""
        self._handlers[handler_name] = handler

    def reset(self) -> None:
        """清空注册表（仅测试用）。

        P5: 与 ProviderRegistry.reset 对称 —— ToolRegistry 实例是每 runtime 新建的，
        但 SeamRegistry 是类级共享，若不在此同步清空，跨测试会泄漏工具名。
        """
        for name in list(self._tools.keys()):
            SeamRegistry.unregister(SeamType.TOOL, name)
        self._tools.clear()
        self._handlers.clear()

    def get_handler(self, handler_name: str) -> Callable[..., Any] | None:
        """P1 解耦: 按名查询 handler。"""
        return self._handlers.get(handler_name)

    def list_handlers(self) -> tuple[str, ...]:
        """P1 解耦: 列出所有已注册 handler 名。"""
        return tuple(self._handlers.keys())

    def get_all_definitions(self) -> tuple[dict[str, Any], ...]:
        return tuple(tool.to_dict() for tool in self._tools.values())

    def finalize_result(
        self, name: str, args: Any, result: Any
    ) -> Any:
        """Invoke tool's finalize_content callback (dsh finalizeContent semantics).

        Returns replacement content list, or None to preserve original.
        Must be total and must not throw — caller catches exceptions.
        """
        tool = self._tools.get(name)
        if tool is None or tool.finalize_content is None:
            return None
        try:
            return tool.finalize_content(args, result)
        except Exception:
            return None