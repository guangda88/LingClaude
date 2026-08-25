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
        if tool.handler is None:
            return Result.fail(f"Tool has no handler: {name}", code="NO_HANDLER")
        try:
            return Result.ok(tool.handler(**kwargs))
        except Exception as e:
            return Result.fail(f"Tool execution failed: {e}", code="EXECUTION_ERROR")

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