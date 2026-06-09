from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 120.0


@dataclass
class ProviderResponse:
    content: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    status: str = "ok"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamChunk:
    delta: str
    finish_reason: str | None = None
    provider: str = ""
    model: str = ""


class ProviderPool:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=_REQUEST_TIMEOUT)

    async def call(
        self,
        api_key: str,
        base_url: str,
        model: str,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.7,
        extra: dict[str, Any] | None = None,
    ) -> ProviderResponse:
        url = f"{base_url.rstrip('/')}/chat/completions"
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if extra:
            body.update(extra)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        t0 = time.monotonic()
        try:
            resp = await self._client.post(url, json=body, headers=headers)
            latency = (time.monotonic() - t0) * 1000
        except httpx.TimeoutException:
            latency = (time.monotonic() - t0) * 1000
            return ProviderResponse(
                content="", model=model, provider="",
                latency_ms=latency, status="timeout",
            )
        except httpx.HTTPError as e:
            latency = (time.monotonic() - t0) * 1000
            return ProviderResponse(
                content="", model=model, provider="",
                latency_ms=latency, status=f"error: {e}",
            )

        if resp.status_code == 429:
            latency = (time.monotonic() - t0) * 1000
            return ProviderResponse(
                content="", model=model, provider="",
                latency_ms=latency, status="429",
            )

        if resp.status_code >= 400:
            latency = (time.monotonic() - t0) * 1000
            text = resp.text[:500]
            return ProviderResponse(
                content="", model=model, provider="",
                latency_ms=latency, status=f"http_{resp.status_code}",
                raw={"error": text},
            )

        try:
            data = resp.json()
        except json.JSONDecodeError:
            return ProviderResponse(
                content="", model=model, provider="",
                latency_ms=latency, status="invalid_json",
            )

        choices = data.get("choices", [])
        content = ""
        finish_reason = None
        if choices:
            msg = choices[0].get("message", {})
            content = msg.get("content", "")
            finish_reason = choices[0].get("finish_reason")

        if not content and not finish_reason:
            return ProviderResponse(
                content="", model=model, provider="",
                latency_ms=latency, status="empty_response",
            )

        usage = data.get("usage", {})
        return ProviderResponse(
            content=content,
            model=data.get("model", model),
            provider="",
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            latency_ms=latency,
            status="ok",
            raw=data,
        )

    async def call_stream(
        self,
        api_key: str,
        base_url: str,
        model: str,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.7,
        extra: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        url = f"{base_url.rstrip('/')}/chat/completions"
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if extra:
            body.update(extra)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        try:
            async with self._client.stream("POST", url, json=body, headers=headers) as resp:
                if resp.status_code == 429:
                    yield StreamChunk(delta="", finish_reason="error", model=model)
                    return
                if resp.status_code >= 400:
                    yield StreamChunk(delta="", finish_reason="error", model=model)
                    return

                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload.strip() == "[DONE]":
                        yield StreamChunk(delta="", finish_reason="stop", model=model)
                        return
                    try:
                        chunk_data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk_data.get("choices", [])
                    if not choices:
                        continue
                    delta_obj = choices[0].get("delta", {})
                    content = delta_obj.get("content", "")
                    finish = choices[0].get("finish_reason")
                    resp_model = chunk_data.get("model", model)
                    yield StreamChunk(
                        delta=content,
                        finish_reason=finish,
                        model=resp_model,
                    )
        except httpx.TimeoutException:
            yield StreamChunk(delta="", finish_reason="timeout", model=model)
        except httpx.HTTPError as e:
            logger.error("Stream error: %s", e)
            yield StreamChunk(delta="", finish_reason="error", model=model)

    async def close(self) -> None:
        await self._client.aclose()
