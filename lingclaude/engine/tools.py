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

import warnings
from dataclasses import dataclass, field
from typing import Any, Callable

from lingclaude.core.types import Result


@dataclass
class ToolOutputDefinition:
    """dsh ToolOutputDefinition Python 端实现。

    Attributes:
        schema: Raw JSON Schema node enforced against successful canonical value.
        render: Pure projection from validated args+value to model-facing content blocks.
        presentation_meta: Optional pure projection (UI metadata, replay only top-level).
    """

    schema: dict[str, Any]
    render: Callable[[Any, Any], list[dict[str, Any]]]
    presentation_meta: Callable[[Any, Any], Any] | None = None


@dataclass(frozen=True)
class ToolDefinition:
    """A registered tool — model schema + canonical output + execution metadata.

    dsh 对位：
        name + description + parameters  → dsh ToolSchema
        output → dsh ToolOutputDefinition
        handler → dsh execute
        finalize_content → dsh finalizeContent (sync last-mile content invariant)
        is_concurrency_safe → dsh isConcurrencySafe (around-dispatch decision)
        present_call / present_result → dsh presentCall/presentResult (UI hooks)

    兼容性: handler / finalize_content / present_* 默认可空, 老调用方无需改。
    解耦（P1）: handler_name 是新插片接口 — 定义与 handler 实现解耦，
    允许"同一定义换 handler"。handler 字段保留用于向后兼容（Callable 直传）；
    若同时设置 handler_name + handler，则 handler 优先（直传更具体）。
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any] | None = None
    security_scope: str = "read"
    output: ToolOutputDefinition | None = None
    is_concurrency_safe: bool = False
    finalize_content: Callable[[Any, Any], Any] | None = None
    present_call: Callable[[Any], Any] | None = None
    present_result: Callable[[Any, Any], Any] | None = None
    # T0-8: 显式 required 参数列表（空 = 全部参数视为 required，保持旧行为）。
    # MCP 工具带完整 JSON Schema 时用它区分可选参数，避免模型被迫填所有参数。
    required_params: tuple[str, ...] = ()
    # T1-3 深化: 工具级超时（秒）— None = 用 pipeline 全局默认超时
    timeout: float | None = None
    # P1 解耦: handler_name 插片引用 — ToolDefinition 不直接绑定 handler Callable，
    # 而是通过 HandlerRegistry 按名查找；这样定义（schema）与实现解耦。
    # 优先级：handler (Callable, 向后兼容) > handler_name (按名查找) > None
    handler_name: str | None = None


    def __post_init__(self) -> None:
        # T3 强制化（opencode 架构演进项）：handler 直传已废弃 — 定义（schema）
        # 与实现（Callable）解耦后，直传使 ToolDefinition 不可序列化/复用。
        # 迁移路径：register_handler(name, fn) + handler_name=name。
        # 存量测试/边缘调用方允许 LINGCLAUDE_ALLOW_DIRECT_HANDLER=1 豁免。
        if self.handler is not None:
            import os
            if not os.environ.get("LINGCLAUDE_ALLOW_DIRECT_HANDLER"):
                warnings.warn(
                    f"ToolDefinition(handler=...) 已废弃: '{self.name}' 应改用 "
                    f"register_handler() + handler_name（T3 解耦纪律）",
                    DeprecationWarning,
                    stacklevel=2,
                )

    def to_dict(self) -> dict[str, Any]:
        d = {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "security_scope": self.security_scope,
            "is_concurrency_safe": self.is_concurrency_safe,
        }
        if self.timeout is not None:
            d["timeout"] = self.timeout
        if self.output is not None:
            d["output_schema"] = self.output.schema
        return d


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        # P1 解耦: handler 注册表 — ToolDefinition 可通过 handler_name 按名引用，
        # 定义（schema）与实现（handler Callable）解耦；同一 handler_name 可被多个 ToolDefinition 复用。
        self._handlers: dict[str, Callable[..., Any]] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

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