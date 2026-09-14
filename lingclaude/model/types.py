"""model/types.py — 兼容 re-export 层（契约已在 core/model_types.py 单源）。

依据: 灵元 1.0 主干零 import 插片。
模型类型契约（ModelMessage/ModelConfig/ModelProvider 等）是主干 provider_proto，
故下沉 lingclaude.core.model_types；本模块仅 re-export 保持兼容
（tests/ 22 处 + model/ 内部引用不断）。

迁移方向: 新代码一律 from lingclaude.core.model_types import ...
"""
from __future__ import annotations

from lingclaude.core.model_types import (
    MessageRole,
    ModelConfig,
    ModelMessage,
    ModelProvider,
    ModelResponse,
    ModelUsage,
    ToolCall,
)

__all__ = [
    "MessageRole",
    "ModelConfig",
    "ModelMessage",
    "ModelProvider",
    "ModelResponse",
    "ModelUsage",
    "ToolCall",
]
