"""LingClaude — 基于灵芝系统的自优化AI运行时

融合 claude-code-port 引擎架构与 LingFlow 自优化框架，
构建面向自用的AI模型运行时，支持逐步自动优化。
"""

from lingclaude.core.types import Result
from lingclaude.core.config import LingClaudeConfig, load_config
from lingclaude.core.session import Session, SessionManager
from lingclaude.core.permissions import PermissionContext
from lingclaude.core.query_engine import QueryEngine, QueryEngineConfig, TurnResult

__version__ = "0.1.0"

__all__ = [
    "Result",
    "LingClaudeConfig",
    "load_config",
    "Session",
    "SessionManager",
    "PermissionContext",
    "QueryEngine",
    "QueryEngineConfig",
    "TurnResult",
]
