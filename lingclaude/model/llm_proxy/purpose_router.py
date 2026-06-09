from __future__ import annotations

import logging
from dataclasses import dataclass

from lingclaude.model.llm_proxy.config import ProxyConfig

logger = logging.getLogger(__name__)

_KEYWORD_MAP: dict[str, list[str]] = {
    "coding": ["code", "debug", "refactor", "function", "class", "implement", "编程", "代码", "调试"],
    "chinese_reasoning": ["分析", "推理", "为什么", "原因", "总结", "对比"],
    "english_general": ["explain", "describe", "what is", "how does", "compare"],
    "fast_response": ["hi", "hello", "thanks", "ok", "你好", "谢谢"],
    "thinking": ["think", "reason", "step by step", "chain of thought", "思考", "推理"],
    "long_context": ["document", "analyze this text", "summarize the", "文档", "长文本"],
    "vision": ["image", "picture", "screenshot", "图片", "截图", "图"],
    "embedding": ["embed", "vector", "向量", "语义"],
    "safety": ["safety", "moderate", "审核", "安全"],
}


@dataclass
class RoutingResult:
    route_key: str
    provider: str
    model: str
    api_key: str
    base_url: str


class PurposeRouter:
    def __init__(self, config: ProxyConfig) -> None:
        self._config = config

    def reload(self, config: ProxyConfig) -> None:
        self._config = config

    def classify(self, messages: list[dict], hint: str | None = None) -> str:
        if hint and hint in self._config.task_routes:
            return hint
        text = self._extract_text(messages).lower()
        scores: dict[str, int] = {}
        for route, keywords in _KEYWORD_MAP.items():
            if route not in self._config.task_routes:
                continue
            score = sum(1 for kw in keywords if kw in text)
            if score:
                scores[route] = score
        if scores:
            return max(scores, key=lambda k: scores[k])
        return "fast_response"

    def resolve(self, route_key: str, available_providers: set[str] | None = None) -> RoutingResult | None:
        route = self._config.task_routes.get(route_key)
        if not route or not route.models:
            return self._fallback(available_providers)

        for entry in route.models:
            if available_providers and entry.provider not in available_providers:
                continue
            pcfg = self._config.providers.get(entry.provider)
            if not pcfg or not pcfg.api_key:
                continue
            return RoutingResult(
                route_key=route_key,
                provider=entry.provider,
                model=entry.model,
                api_key=pcfg.api_key,
                base_url=pcfg.base_url,
            )

        return self._fallback(available_providers)

    def _fallback(self, available: set[str] | None = None) -> RoutingResult | None:
        pcfg = self._config.providers.get(self._config.default_provider)
        if not pcfg:
            return None
        if available and pcfg.name not in available:
            for pname in available:
                p = self._config.providers.get(pname)
                if p and p.api_key:
                    pcfg = p
                    break
        return RoutingResult(
            route_key="fallback",
            provider=pcfg.name,
            model=pcfg.default_model or (pcfg.models[0] if pcfg.models else ""),
            api_key=pcfg.api_key,
            base_url=pcfg.base_url,
        )

    @staticmethod
    def _extract_text(messages: list[dict]) -> str:
        parts: list[str] = []
        for msg in messages[:6]:
            content = msg.get("content", "")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(item.get("text", ""))
        return " ".join(parts)[:2000]
