from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator

import httpx

from lingclaude.model.llm_proxy.config import ProxyConfig
from lingclaude.model.llm_proxy.rate_gate import RateGate, RatePolicy
from lingclaude.model.llm_proxy.purpose_router import PurposeRouter
from lingclaude.model.llm_proxy.provider_pool import ProviderPool, StreamChunk
from lingclaude.model.llm_proxy.token_gate import TokenBudget, TokenGate
from lingclaude.model.llm_proxy.data_filter import AuditEntry, DataFilter
from lingclaude.model.llm_proxy.retry import RetryPolicy, ProviderRetryBudget, is_retryable, calculate_delay
from lingclaude.model.llm_proxy.metrics import MetricsCollector

logger = logging.getLogger(__name__)


class LLMProxy:
    def __init__(self, config: ProxyConfig, retry_policy: RetryPolicy | None = None) -> None:
        self._config = config
        self._policy = RatePolicy(
            global_rpm=config.global_rpm,
            cooldown_429=config.cooldown_429,
        )
        self._rate = RateGate(self._policy)
        self._router = PurposeRouter(config)
        self._pool = ProviderPool()
        self._token_gate = TokenGate()
        self._data_filter = DataFilter()
        self._retry_policy = retry_policy or RetryPolicy()
        self._metrics = MetricsCollector()
        self._peer_url: str = config.peer_proxy_url

        for name, pcfg in config.providers.items():
            self._rate.register_provider(name, pcfg.rpm, pcfg.burst)
            self._token_gate.register(TokenBudget(
                provider=name, key_id=name,
                total_budget=pcfg.rpm * 5000,
                window_seconds=18000,
            ))

    async def chat(
        self,
        messages: list[dict],
        purpose: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        caller: str = "unknown",
    ) -> dict[str, Any]:
        if self._config.should_reload():
            self._reload()

        route_key = self._router.classify(messages, hint=purpose)
        route = self._config.task_routes.get(route_key)
        candidates = [e.provider for e in (route.models if route else [])]
        if not candidates:
            candidates = list(self._config.providers.keys())

        tried_providers: set[str] = set()
        retry_budgets: dict[str, ProviderRetryBudget] = {}
        total_attempts = 0

        while total_attempts < self._retry_policy.max_retries:
            provider_name = self._rate.pick_from_route(route_key, [
                c for c in candidates if c not in tried_providers
            ])
            if not provider_name:
                break

            tried_providers.add(provider_name)
            resolved = self._router.resolve(route_key, available_providers={provider_name})
            if not resolved:
                self._rate.record_error(provider_name)
                continue

            if not self._token_gate.check_available(provider_name):
                logger.warning("Token budget exhausted for %s, skipping", provider_name)
                continue

            budget = retry_budgets.setdefault(
                provider_name, ProviderRetryBudget(provider=provider_name, max_retries=2)
            )

            resp = await self._pool.call(
                api_key=resolved.api_key,
                base_url=resolved.base_url,
                model=resolved.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )

            total_attempts += 1
            budget.record_attempt()

            if resp.status == "429":
                self._rate.record_429(provider_name)
                delay = calculate_delay(
                    budget.attempts, self._retry_policy.base_delay,
                    self._retry_policy.max_delay, self._retry_policy.jitter,
                )
                logger.warning("429 from %s/%s, backoff %.1fs", provider_name, resolved.model, delay)
                if budget.can_retry():
                    tried_providers.discard(provider_name)
                    await asyncio.sleep(delay)
                continue

            if is_retryable(resp.status) and budget.can_retry():
                self._rate.record_error(provider_name)
                delay = calculate_delay(
                    budget.attempts, self._retry_policy.base_delay,
                    self._retry_policy.max_delay, self._retry_policy.jitter,
                )
                logger.warning("Retryable error %s from %s/%s, backoff %.1fs",
                               resp.status, provider_name, resolved.model, delay)
                tried_providers.discard(provider_name)
                await asyncio.sleep(delay)
                continue

            if resp.status != "ok":
                self._rate.record_error(provider_name)
                self._metrics.record_request(
                    provider=provider_name, model=resolved.model, purpose=route_key,
                    status=resp.status, latency_ms=resp.latency_ms,
                )
                logger.error("Error from %s/%s: %s", provider_name, resolved.model, resp.status)
                continue

            self._rate.record_success(provider_name)
            self._token_gate.record_usage(provider_name, resp.input_tokens, resp.output_tokens)
            self._metrics.record_request(
                provider=provider_name, model=resolved.model, purpose=route_key,
                status="ok", latency_ms=resp.latency_ms,
                tokens_in=resp.input_tokens, tokens_out=resp.output_tokens,
            )
            self._data_filter.record(AuditEntry(
                timestamp=time.monotonic(),
                caller=caller, purpose=route_key,
                provider=provider_name, model=resolved.model,
                input_tokens=resp.input_tokens, output_tokens=resp.output_tokens,
                latency_ms=resp.latency_ms, status="ok",
            ))
            return {
                "id": f"proxy-{int(time.time())}",
                "object": "chat.completion",
                "model": resp.model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": resp.content},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": resp.input_tokens,
                    "completion_tokens": resp.output_tokens,
                    "total_tokens": resp.input_tokens + resp.output_tokens,
                },
                "_proxy_meta": {
                    "provider": provider_name,
                    "model": resolved.model,
                    "route": route_key,
                    "caller": caller,
                    "latency_ms": resp.latency_ms,
                },
            }

        # All local providers failed — try peer proxy as last resort
        if self._peer_url and caller != "_peer_fallback":
            logger.warning("All local providers exhausted, falling back to peer: %s", self._peer_url)
            try:
                peer_result = await self._call_peer(
                    messages=messages, purpose=purpose, max_tokens=max_tokens,
                    temperature=temperature, caller=caller,
                )
                if "error" not in peer_result:
                    return peer_result
                logger.error("Peer fallback returned error dict: %s", peer_result.get("error"))
            except Exception as e:
                logger.error("Peer proxy fallback failed: %s", e)

        logger.error(
            "[proxy_fallback] All providers exhausted (non-stream) caller=%s route=%s attempts=%d",
            caller, route_key, total_attempts,
        )
        return {
            "id": f"proxy-fallback-{int(time.time())}",
            "object": "chat.completion",
            "model": "fallback",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "[proxy_fallback] 所有模型暂时不可用，请稍后重试。",
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "_proxy_meta": {
                "fallback": True,
                "caller": caller,
                "route": route_key,
                "tried": list(tried_providers),
            },
        }

    async def chat_stream(
        self,
        messages: list[dict],
        purpose: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        caller: str = "unknown",
    ) -> AsyncIterator[StreamChunk]:
        if self._config.should_reload():
            self._reload()

        route_key = self._router.classify(messages, hint=purpose)
        route = self._config.task_routes.get(route_key)
        candidates = [e.provider for e in (route.models if route else [])]
        if not candidates:
            candidates = list(self._config.providers.keys())

        tried_providers: set[str] = set()

        for attempt in range(min(len(candidates), 4)):
            provider_name = self._rate.pick_from_route(route_key, [
                c for c in candidates if c not in tried_providers
            ])
            if not provider_name:
                break

            tried_providers.add(provider_name)
            resolved = self._router.resolve(route_key, available_providers={provider_name})
            if not resolved:
                self._rate.record_error(provider_name)
                continue

            if not self._token_gate.check_available(provider_name):
                logger.warning("Token budget exhausted for %s, skipping", provider_name)
                continue

            t0 = time.monotonic()
            collected_tokens = 0
            stream_error = False

            async for chunk in self._pool.call_stream(
                api_key=resolved.api_key,
                base_url=resolved.base_url,
                model=resolved.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            ):
                if chunk.finish_reason == "error" or chunk.finish_reason == "timeout":
                    stream_error = True
                    break
                if chunk.delta:
                    collected_tokens += 1
                chunk.provider = provider_name
                yield chunk

            latency = (time.monotonic() - t0) * 1000

            if stream_error:
                self._rate.record_error(provider_name)
                continue

            if collected_tokens == 0:
                logger.warning(
                    "Empty stream from %s (model=%s, latency=%.0fms), treating as error",
                    provider_name, resolved.model, latency,
                )
                self._rate.record_error(provider_name)
                self._metrics.record_stream(
                    provider=provider_name, model=resolved.model, purpose=route_key,
                    status="empty_stream", latency_ms=latency, chunks=0,
                )
                self._data_filter.record(AuditEntry(
                    timestamp=time.monotonic(),
                    caller=caller, purpose=route_key,
                    provider=provider_name, model=resolved.model,
                    input_tokens=0, output_tokens=0,
                    latency_ms=latency, status="empty_stream",
                ))
                continue

            self._rate.record_success(provider_name)
            self._token_gate.record_usage(provider_name, 0, collected_tokens)
            self._metrics.record_stream(
                provider=provider_name, model=resolved.model, purpose=route_key,
                status="ok", latency_ms=latency, chunks=collected_tokens,
            )
            self._data_filter.record(AuditEntry(
                timestamp=time.monotonic(),
                caller=caller, purpose=route_key,
                provider=provider_name, model=resolved.model,
                input_tokens=0, output_tokens=collected_tokens,
                latency_ms=latency, status="ok",
            ))
            return

        logger.error(
            "[proxy_fallback] All providers exhausted (stream) caller=%s route=%s",
            caller, route_key,
        )
        yield StreamChunk(
            delta="[proxy_fallback] 所有模型暂时不可用，请稍后重试。",
            finish_reason="stop",
        )

    def metrics_stats(self) -> dict:
        return self._metrics.stats()

    def metrics_prometheus(self) -> str:
        return self._metrics.format_prometheus()

    def health(self) -> dict[str, Any]:
        return {
            "providers": self._rate.health(),
            "token_budgets": self._token_gate.stats(),
            "routes": list(self._config.task_routes.keys()),
            "default_provider": self._config.default_provider,
        }

    def audit_stats(self) -> dict:
        return self._data_filter.stats()

    async def _call_peer(
        self,
        messages: list[dict],
        purpose: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        caller: str = "unknown",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "_purpose": purpose,
        }
        url = f"{self._peer_url.rstrip('/')}/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "X-Caller": "_peer_fallback",
            "X-Purpose": purpose or "",
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=body, headers=headers)
            data = resp.json()

        if resp.status_code == 200 and "choices" in data:
            data["_proxy_meta"] = {
                "provider": "peer_fallback",
                "model": data.get("model", "unknown"),
                "route": "peer",
                "caller": caller,
                "peer_url": self._peer_url,
            }
            logger.info("Peer fallback succeeded: model=%s", data.get("model"))
            return data

        logger.error("Peer fallback returned error: %d %s", resp.status_code, data)
        return {"error": f"peer_fallback_failed: {resp.status_code}", "detail": data}

    def _reload(self) -> None:
        new_cfg = ProxyConfig.load(self._config._config_path)
        self._config = new_cfg
        self._router.reload(new_cfg)
        self._policy.cooldown_429 = new_cfg.cooldown_429
        self._policy.global_rpm = new_cfg.global_rpm
        self._peer_url = new_cfg.peer_proxy_url
        for name, pcfg in new_cfg.providers.items():
            if name not in self._rate._slots:
                self._rate.register_provider(name, pcfg.rpm, pcfg.burst)
        logger.info("ProxyConfig reloaded")

    async def close(self) -> None:
        await self._pool.close()
