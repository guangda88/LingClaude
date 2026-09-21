"""engine/loop — 循环层公共出口（P0-A 迁移）。

公共出口白名单见 docs/CORE_SURFACE_CONTRACT.md §二。
铁律：本包不得 import lingclaude.cli.*；内部模块间不得互传私有槽位，
一切经 LoopHooks 或显式参数。
"""

from lingclaude.engine.loop.hooks import (
    DefaultLoopHooks,
    LoopHooks,
    default_hooks_for,
)
from lingclaude.engine.loop.l5_conversation_loop import (
    L5ConversationConfig,
    L5ConversationLoop,
    L5RoundResult,
)
from lingclaude.engine.loop.tool_loop_detector import (
    _LOOP_ABORT_MSG,
    _LOOP_WARN_HINT,
    _R5_THRESHOLDS,
    _ToolLoopDetector,
)
from lingclaude.engine.loop.sub_agent import (
    SubAgent,
    SubAgentConfig,
    SubAgentResult,
)

__all__ = [
    "LoopHooks",
    "DefaultLoopHooks",
    "default_hooks_for",
    "L5ConversationLoop",
    "L5ConversationConfig",
    "L5RoundResult",
    "_ToolLoopDetector",
    "_LOOP_WARN_HINT",
    "_LOOP_ABORT_MSG",
    "_R5_THRESHOLDS",
    "SubAgent",
    "SubAgentConfig",
    "SubAgentResult",
]
