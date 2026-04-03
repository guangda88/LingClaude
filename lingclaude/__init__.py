"""灵克 (LingClaude) — 开源 AI 编程助手，内置自优化能力"""

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
