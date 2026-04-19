from __future__ import annotations

import logging
from typing import Any

from lingclaude.core.types import Result
from lingclaude.model.intelligent_router import IntelligentRouter, TaskComplexity
from lingclaude.model.types import (
    ModelConfig,
    ModelMessage,
    ModelProvider,
    ModelResponse,
)

logger = logging.getLogger(__name__)


class HybridRouterProvider(ModelProvider):
    """Routes between local model and API based on task complexity.

    Strategy:
    - SIMPLE tasks → local model (zero API cost)
    - MEDIUM tasks → local model (still cheap, good enough for most coding)
    - COMPLEX tasks → API provider (highest quality)

    This saves tokens by offloading everything possible to the local GPU.
    """

    _LOCAL_OK = frozenset({TaskComplexity.SIMPLE, TaskComplexity.MEDIUM})

    def __init__(
        self,
        local_provider: ModelProvider,
        api_provider: ModelProvider,
        router: IntelligentRouter | None = None,
        local_threshold: TaskComplexity = TaskComplexity.MEDIUM,
    ) -> None:
        self._local = local_provider
        self._api = api_provider
        self._router = router or IntelligentRouter()
        self._local_threshold = local_threshold
        self._local_count = 0
        self._api_count = 0

    def complete(
        self,
        messages: tuple[ModelMessage, ...],
        config: ModelConfig | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Result[ModelResponse]:
        query = self._extract_query(messages)
        decision = self._router.route(query)

        use_local = decision.complexity in self._LOCAL_OK
        if tools and len(tools) > 3:
            use_local = False
        if not use_local:
            if decision.task_type.name in ("SEARCH", "DOCUMENTATION", "CODE_ANALYSIS"):
                use_local = True

        if use_local:
            logger.info(
                "Routing to LOCAL: complexity=%s, task=%s",
                decision.complexity.value, decision.task_type.name,
            )
            result = self._local.complete(messages, config, tools)
            if result.is_ok:
                self._local_count += 1
                return result
            logger.warning("Local model failed, falling back to API: %s", result.error)

        logger.info(
            "Routing to API: complexity=%s, task=%s",
            decision.complexity.value, decision.task_type.name,
        )
        self._api_count += 1
        return self._api.complete(messages, config, tools)

    async def acomplete(
        self,
        messages: tuple[ModelMessage, ...],
        config: ModelConfig | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Result[ModelResponse]:
        query = self._extract_query(messages)
        decision = self._router.route(query)

        use_local = decision.complexity in self._LOCAL_OK
        if tools and len(tools) > 3:
            use_local = False

        if use_local:
            result = await self._local.acomplete(messages, config, tools)
            if result.is_ok:
                self._local_count += 1
                return result

        self._api_count += 1
        return await self._api.acomplete(messages, config, tools)

    def count_tokens(self, text: str) -> int:
        return self._local.count_tokens(text)

    def get_stats(self) -> dict[str, int]:
        return {
            "local_requests": self._local_count,
            "api_requests": self._api_count,
            "total_requests": self._local_count + self._api_count,
            "savings_ratio": (
                self._local_count / (self._local_count + self._api_count)
                if (self._local_count + self._api_count) > 0
                else 0.0
            ),
        }

    def _extract_query(self, messages: tuple[ModelMessage, ...]) -> str:
        for msg in reversed(messages):
            if msg.role.value == "user":
                return msg.content
        return ""
