from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Subsystem:
    name: str
    path: str
    file_count: int
    notes: str


@dataclass(frozen=True)
class ModuleEntry:
    name: str
    responsibility: str
    source_hint: str
    status: str = "planned"


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, object] = field(default_factory=dict)
    status: str = "mirrored"


@dataclass(frozen=True)
class PermissionDenial:
    tool_name: str
    reason: str


@dataclass
class UsageSummary:
    input_tokens: int = 0
    output_tokens: int = 0

    def add_turn(self, prompt: str, output: str) -> UsageSummary:
        return UsageSummary(
            input_tokens=self.input_tokens + len(prompt.split()),
            output_tokens=self.output_tokens + len(output.split()),
        )

    def add_usage(self, input_tokens: int, output_tokens: int) -> UsageSummary:
        return UsageSummary(
            input_tokens=self.input_tokens + input_tokens,
            output_tokens=self.output_tokens + output_tokens,
        )

    def to_dict(self) -> dict[str, int]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


@dataclass(frozen=True)
class RoutedMatch:
    kind: str
    name: str
    source_hint: str
    score: int
