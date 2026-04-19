from __future__ import annotations

from lingclaude.model.retry import GlmRetryPolicy, is_rate_limit_error
from lingclaude.model.types import (
    MessageRole,
    ModelMessage,
    ModelResponse,
    ModelUsage,
    ModelProvider,
    ModelConfig,
)
from lingclaude.model.factory import create_provider

__all__ = [
    "MessageRole",
    "ModelMessage",
    "ModelResponse",
    "ModelUsage",
    "ModelProvider",
    "ModelConfig",
    "create_provider",
    "GlmRetryPolicy",
    "is_rate_limit_error",
]

def create_hybrid_provider(api_provider: ModelProvider) -> ModelProvider:
    from lingclaude.model.local_provider import LocalModelProvider
    from lingclaude.model.hybrid_router import HybridRouterProvider
    local = LocalModelProvider()
    return HybridRouterProvider(local_provider=local, api_provider=api_provider)
