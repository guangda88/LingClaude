"""Subagent multi-backend support (P1-2).

Provider abstraction:
- InProcessSubagentProvider: 当前实现，同一进程内用 runtime + model provider 执行
- AcpSubagentProvider: Agent Client Protocol 对接外部 agent（如 DSH acp/）
"""

from __future__ import annotations

from lingclaude.engine.subagent.base import (
    SubagentBackend,
    SubagentContext,
    SubagentRequest,
    SubagentResult,
    SubagentStatus,
)
from lingclaude.engine.subagent.inprocess import InProcessSubagentBackend
from lingclaude.engine.subagent.acp import AcpSubagentBackend
from lingclaude.engine.subagent.mcp import MCPBackendConfig, MCPSubagentBackend
from lingclaude.engine.subagent.manager import SubagentManager

__all__ = [
    "SubagentBackend",
    "SubagentContext",
    "SubagentRequest",
    "SubagentResult",
    "SubagentStatus",
    "InProcessSubagentBackend",
    "AcpSubagentBackend",
    "MCPBackendConfig",
    "MCPSubagentBackend",
    "SubagentManager",
]
