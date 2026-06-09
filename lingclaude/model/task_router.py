"""Task Router — Multi-provider task routing based on config.json

Reads routing.providers and routing.task_routes from /home/ai/lingcode/config.json.
Maps IntelligentRouter.TaskType to task_routes keys, iterates ordered model lists,
returns a complete ModelConfig with correct api_key, base_url, and model for the
routed provider. Falls back to default provider ("cheap") if no route matches.
"""
from __future__ import annotations

import json
import logging
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lingclaude.model.types import ModelConfig
from lingclaude.model.intelligent_router import TaskType
from lingclaude.core.rate_limiter import LeakyBucket, ProviderSlot

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("/home/ai/lingcode/config.json")

TASK_TYPE_TO_ROUTE: dict[TaskType, str] = {
    TaskType.CODE_GENERATION: "coding",
    TaskType.CODE_ANALYSIS: "coding",
    TaskType.CODE_REFACTORING: "coding",
    TaskType.DEBUGGING: "coding",
    TaskType.TESTING: "coding",
    TaskType.ANALYSIS: "chinese_reasoning",
    TaskType.OPTIMIZATION: "chinese_reasoning",
    TaskType.DOCUMENTATION: "english_general",
    TaskType.SEARCH: "fast_response",
    TaskType.OTHER: "fast_response",
}


@dataclass
class _ProviderInfo:
    type: str
    api_key: str
    base_url: str
    default_model: str
    models: list[str]
    rpm: float
    burst: int


@dataclass
class _ModelRef:
    provider: str
    model: str


@dataclass
class _TaskRoute:
    description: str
    models: list[_ModelRef]


class TaskRouter:
    def __init__(self, config_path: Path | str | None = None) -> None:
        self._path = Path(config_path) if config_path else CONFIG_PATH
        self._providers: dict[str, _ProviderInfo] = {}
        self._task_routes: dict[str, _TaskRoute] = {}
        self._default_provider: str = "cheap"
        self._slots: dict[str, ProviderSlot] = {}
        self._lock = threading.Lock()
        self._round_robin_idx: dict[str, int] = {}
        self._load_config()

    def _load_config(self) -> None:
        if not self._path.exists():
            logger.warning("TaskRouter: config not found at %s, using empty config", self._path)
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error("TaskRouter: failed to load config: %s", e)
            return

        routing = raw.get("routing", {})
        self._default_provider = routing.get("default_target", "cheap")

        for name, pdef in routing.get("providers", {}).items():
            rate = pdef.get("rate_limit", {})
            self._providers[name] = _ProviderInfo(
                type=pdef.get("type", "openai"),
                api_key=pdef.get("api_key", ""),
                base_url=pdef.get("base_url", ""),
                default_model=pdef.get("model", ""),
                models=pdef.get("models", []),
                rpm=float(rate.get("rpm", 10)),
                burst=int(rate.get("burst", 3)),
            )
            bucket = LeakyBucket(
                max_tokens=float(rate.get("burst", 3)),
                refill_rate=float(rate.get("rpm", 10)) / 60.0,
            )
            self._slots[name] = ProviderSlot(name=name, bucket=bucket)

        for key, tr in routing.get("task_routes", {}).items():
            models = [_ModelRef(provider=m["provider"], model=m["model"]) for m in tr.get("models", [])]
            self._task_routes[key] = _TaskRoute(description=tr.get("description", ""), models=models)

        logger.info(
            "TaskRouter: loaded %d providers, %d task routes from %s",
            len(self._providers), len(self._task_routes), self._path,
        )

    def resolve(
        self,
        prompt: str,
        task_type: TaskType | None = None,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> tuple[ModelConfig, str]:
        if task_type is None:
            task_type = TaskType.from_query(prompt)

        route_key = TASK_TYPE_TO_ROUTE.get(task_type, "fast_response")
        route = self._task_routes.get(route_key)

        if route and route.models:
            resolved = self._pick_from_route(route_key, route, max_tokens, temperature)
            if resolved:
                return resolved, route_key

        return self._fallback(max_tokens, temperature), route_key

    def _pick_from_route(self, route_key: str, route: _TaskRoute, max_tokens: int, temperature: float) -> ModelConfig | None:
        models = route.models
        if not models:
            return None

        with self._lock:
            idx = self._round_robin_idx.get(route_key, 0)

        for i in range(len(models)):
            pos = (idx + i) % len(models)
            ref = models[pos]
            pinfo = self._providers.get(ref.provider)
            if not pinfo:
                continue

            slot = self._slots.get(ref.provider)
            if slot and not slot.is_available:
                continue

            with self._lock:
                self._round_robin_idx[route_key] = (pos + 1) % len(models)

            slot = self._slots.get(ref.provider)
            if slot:
                slot.total_requests += 1

            return ModelConfig(
                model=ref.model,
                api_key=pinfo.api_key,
                base_url=pinfo.base_url,
                max_tokens=max_tokens,
                temperature=temperature,
                system_prompt="",
            )

        return None

    def _fallback(self, max_tokens: int, temperature: float) -> ModelConfig:
        pinfo = self._providers.get(self._default_provider)
        if pinfo:
            return ModelConfig(
                model=pinfo.default_model,
                api_key=pinfo.api_key,
                base_url=pinfo.base_url,
                max_tokens=max_tokens,
                temperature=temperature,
                system_prompt="",
            )
        return ModelConfig()

    def record_success(self, provider_name: str) -> None:
        slot = self._slots.get(provider_name)
        if slot:
            slot.consecutive_errors = 0

    def record_error(self, provider_name: str) -> None:
        slot = self._slots.get(provider_name)
        if slot:
            slot.consecutive_errors += 1
            slot.total_errors += 1
            slot.last_error_time = time.monotonic()
            if slot.consecutive_errors >= 3:
                slot.cooldown_until = time.monotonic() + 30.0
                slot.consecutive_errors = 0

    def get_provider_name(self, api_key: str, base_url: str) -> str | None:
        for name, pinfo in self._providers.items():
            if pinfo.api_key == api_key and pinfo.base_url == base_url:
                return name
        return None

    def get_default_provider_name(self) -> str:
        return self._default_provider

    def stats(self) -> dict[str, Any]:
        result: dict[str, Any] = {"providers": {}, "routes": list(self._task_routes.keys())}
        for name, slot in self._slots.items():
            result["providers"][name] = {
                "available": slot.is_available,
                "consecutive_errors": slot.consecutive_errors,
                "total_requests": slot.total_requests,
                "total_errors": slot.total_errors,
            }
        return result
