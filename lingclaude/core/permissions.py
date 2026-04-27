from __future__ import annotations

from dataclasses import dataclass, field

READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "read", "grep", "glob", "ls", "find", "head", "tail",
    "cat", "view", "search", "list", "stat", "wc",
})


@dataclass(frozen=True)
class PermissionContext:
    deny_names: frozenset[str] = field(default_factory=frozenset)
    deny_prefixes: tuple[str, ...] = ()
    auto_approve_names: frozenset[str] = field(default_factory=lambda: READ_ONLY_TOOLS)

    @classmethod
    def from_config(
        cls,
        deny_tools: list[str] | None = None,
        deny_prefixes: list[str] | None = None,
        auto_approve: list[str] | None = None,
    ) -> PermissionContext:
        auto = READ_ONLY_TOOLS
        if auto_approve is not None:
            auto = frozenset(name.lower() for name in auto_approve) | READ_ONLY_TOOLS
        return cls(
            deny_names=frozenset(name.lower() for name in (deny_tools or [])),
            deny_prefixes=tuple(prefix.lower() for prefix in (deny_prefixes or [])),
            auto_approve_names=auto,
        )

    def blocks(self, tool_name: str) -> bool:
        lowered = tool_name.lower()
        return lowered in self.deny_names or any(lowered.startswith(prefix) for prefix in self.deny_prefixes)

    def is_auto_approved(self, tool_name: str) -> bool:
        return tool_name.lower() in self.auto_approve_names and not self.blocks(tool_name)

    def requires_approval(self, tool_name: str) -> bool:
        if self.blocks(tool_name):
            return True
        return not self.is_auto_approved(tool_name)

    def filter_tools(self, tools: tuple[object, ...], name_getter: object = None) -> tuple[object, ...]:
        result = []
        for tool in tools:
            name = name_getter(tool) if name_getter else getattr(tool, "name", str(tool))
            if not self.blocks(name):
                result.append(tool)
        return tuple(result)
