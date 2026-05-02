from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class PlanMode:
    def __init__(self) -> None:
        self._active: bool = False

    @property
    def is_active(self) -> bool:
        return self._active

    def enter(self) -> dict[str, str]:
        self._active = True
        logger.info("Plan mode activated")
        return {"status": "active"}

    def exit(self) -> dict[str, str]:
        self._active = False
        logger.info("Plan mode deactivated")
        return {"status": "inactive"}

    def filter_tools(self, tools: list[dict[str, any]]) -> list[dict[str, any]]:
        if not self._active:
            return tools
        return []
