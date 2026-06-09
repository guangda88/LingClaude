from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("/home/ai/lingcode/config.json")


@dataclass
class ProviderConfig:
    name: str
    type: str
    api_key: str
    base_url: str
    default_model: str
    models: list[str]
    rpm: float
    burst: int


@dataclass
class RouteEntry:
    provider: str
    model: str


@dataclass
class TaskRoute:
    description: str
    models: list[RouteEntry]


@dataclass
class ProxyConfig:
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    task_routes: dict[str, TaskRoute] = field(default_factory=dict)
    default_provider: str = "glm"
    global_rpm: float = 60.0
    cooldown_429: float = 30.0
    peer_proxy_url: str = ""
    _config_mtime: float = 0.0
    _config_path: Path = field(default=CONFIG_PATH)

    @classmethod
    def load(cls, path: Path | str | None = None) -> ProxyConfig:
        p = Path(path) if path else CONFIG_PATH
        if not p.exists():
            logger.warning("ProxyConfig: %s not found", p)
            return cls()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error("ProxyConfig: failed to load: %s", e)
            return cls()

        cfg = cls(_config_path=p, _config_mtime=p.stat().st_mtime)
        routing = raw.get("routing", {})
        cfg.default_provider = routing.get("default_target", "glm")
        cfg.peer_proxy_url = routing.get("peer_proxy_url", "")

        for name, pdef in routing.get("providers", {}).items():
            rate = pdef.get("rate_limit", {})
            key = pdef.get("api_key", "")
            if key.startswith("$"):
                key = os.environ.get(key[1:], "")
            cfg.providers[name] = ProviderConfig(
                name=name,
                type=pdef.get("type", "openai"),
                api_key=key,
                base_url=pdef.get("base_url", ""),
                default_model=pdef.get("model", ""),
                models=pdef.get("models", []),
                rpm=float(rate.get("rpm", 10)),
                burst=int(rate.get("burst", 3)),
            )

        for key, tr in routing.get("task_routes", {}).items():
            entries = [
                RouteEntry(provider=m["provider"], model=m["model"])
                for m in tr.get("models", [])
            ]
            cfg.task_routes[key] = TaskRoute(
                description=tr.get("description", ""),
                models=entries,
            )

        logger.info("ProxyConfig: %d providers, %d routes", len(cfg.providers), len(cfg.task_routes))
        return cfg

    def should_reload(self) -> bool:
        try:
            return self._config_path.stat().st_mtime > self._config_mtime
        except OSError:
            return False
