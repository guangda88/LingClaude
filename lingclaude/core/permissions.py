from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PermissionContext:
    deny_names: frozenset[str] = field(default_factory=frozenset)
    deny_prefixes: tuple[str, ...] = ()

    @classmethod
    def from_config(cls, deny_tools: list[str] | None = None, deny_prefixes: list[str] | None = None) -> PermissionContext:
        return cls(
            deny_names=frozenset(name.lower() for name in (deny_tools or [])),
            deny_prefixes=tuple(prefix.lower() for prefix in (deny_prefixes or [])),
        )

    def blocks(self, tool_name: str) -> bool:
        lowered = tool_name.lower()
        return lowered in self.deny_names or any(lowered.startswith(prefix) for prefix in self.deny_prefixes)

    def filter_tools(self, tools: tuple[object, ...], name_getter: object = None) -> tuple[object, ...]:
        result = []
        for tool in tools:
            name = name_getter(tool) if name_getter else getattr(tool, "name", str(tool))
            if not self.blocks(name):
                result.append(tool)
        return tuple(result)
