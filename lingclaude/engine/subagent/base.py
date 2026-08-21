"""Subagent backend abstraction (P1-2)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SubagentRequest:
    """A sub-agent task request."""

    task: str
    context: str = ""
    max_rounds: int = 5
    provider: str | None = None
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SubagentContext:
    """Runtime context handed to a sub-agent backend."""

    runtime: Any = None
    model_provider: Any = None
    allowed_tools: tuple[str, ...] = field(
        default_factory=lambda: ("read", "grep", "glob", "list_functions", "web_fetch")
    )


@dataclass(frozen=True)
class SubagentResult:
    """Outcome of a sub-agent run."""

    agent_id: str
    task: str
    output: str
    success: bool = True
    error: str | None = None
    tools_used: tuple[str, ...] = ()
    rounds: int = 0
    provider: str | None = None


class SubagentBackend(ABC):
    """Backend contract for executing sub-agents."""

    name: str = "base"

    @abstractmethod
    def run(self, request: SubagentRequest, ctx: SubagentContext) -> SubagentResult:
        """Execute the task and return a result."""
        raise NotImplementedError
