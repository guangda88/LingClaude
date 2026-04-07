from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


from lingclaude.core.types import Result


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any] | None = None
    security_scope: str = "read"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "security_scope": self.security_scope,
        }


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
