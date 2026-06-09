from __future__ import annotations

"""Unified Lifecycle Hook System.

Provides hook points at key lifecycle stages:
- PRE_TASK / POST_TASK — around each user prompt
- ON_ERROR — on tool/provider errors
- ON_STOP — when session stops (max turns, hard interrupt, budget)
- PRE_COMPACT / POST_COMPACT — around context compression

Hooks are callables that receive a HookContext and optionally return a modified one.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


logger = logging.getLogger(__name__)


class HookType(str, Enum):
    PRE_TASK = "pre_task"
    POST_TASK = "post_task"
    ON_ERROR = "on_error"
    ON_STOP = "on_stop"
    PRE_COMPACT = "pre_compact"
    POST_COMPACT = "post_compact"


@dataclass(frozen=True)
class HookContext:
    hook_type: HookType
    session_id: str
    prompt: str = ""
    output: str = ""
    tool_name: str = ""
    error_message: str = ""
    stop_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


HookCallback = Callable[[HookContext], HookContext | None]


@dataclass
class HookResult:
    modified_context: HookContext | None
    blocked: bool = False
    error: str = ""


@dataclass
class HookEntry:
    name: str
    hook_type: HookType
    callback: HookCallback
    priority: int = 100


class HookManager:
    def __init__(self) -> None:
        self._hooks: list[HookEntry] = []

    def register(
        self,
        name: str,
        hook_type: HookType,
        callback: HookCallback,
        priority: int = 100,
    ) -> None:
        entry = HookEntry(
            name=name,
            hook_type=hook_type,
            callback=callback,
            priority=priority,
        )
        self._hooks.append(entry)
        self._hooks.sort(key=lambda h: h.priority)

    def unregister(self, name: str) -> bool:
        before = len(self._hooks)
        self._hooks = [h for h in self._hooks if h.name != name]
        return len(self._hooks) < before

    def trigger(self, context: HookContext) -> HookResult:
        entries = [h for h in self._hooks if h.hook_type == context.hook_type]
        current: HookContext = context

        for entry in entries:
            try:
                result = entry.callback(current)
                if result is not None:
                    current = result
            except Exception as e:
                logger.warning("Hook '%s' failed: %s", entry.name, e)
                return HookResult(modified_context=current, error=str(e))

        return HookResult(modified_context=current)

    def has_hooks(self, hook_type: HookType) -> bool:
        return any(h.hook_type == hook_type for h in self._hooks)

    def list_hooks(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "name": h.name,
                "type": h.hook_type.value,
                "priority": h.priority,
            }
            for h in self._hooks
        )

    def clear(self) -> None:
        self._hooks.clear()
