"""Subagent backend abstraction (P1-2)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SubagentStatus(str, Enum):
    """子代理运行状态。"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


@dataclass(frozen=True)
class SubagentRequest:
    """A sub-agent task request."""

    task: str
    context: str = ""
    max_rounds: int = 5
    provider: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    # T1-6: 并行执行时的任务数量（>1 表示并行模式）
    parallel: int = 1
    # T1-6: 是否启用控制通道（abort/status）
    control_channel: bool = False


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
    status: SubagentStatus = SubagentStatus.COMPLETED


class SubagentBackend(ABC):
    """Backend contract for executing sub-agents."""

    name: str = "base"

    @abstractmethod
    def run(self, request: SubagentRequest, ctx: SubagentContext) -> SubagentResult:
        """Execute the task and return a result."""
        raise NotImplementedError

    # T1-6: 控制通道 — 中止运行中的子代理
    def abort(self, agent_id: str) -> bool:
        """Abort a running sub-agent. Returns True if successfully aborted."""
        return False

    # T1-6: 控制通道 — 查询子代理状态
    def status(self, agent_id: str) -> SubagentStatus:
        """Get current status of a sub-agent."""
        return SubagentStatus.COMPLETED
