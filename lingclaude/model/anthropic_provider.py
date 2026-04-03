from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from lingclaude.core.types import Result
from lingclaude.model.types import (
    ModelConfig,
    ModelMessage,
    ModelProvider,
    ModelResponse,
    ModelUsage,
)


class AnthropicProvider(ModelProvider):
    def __init__(self, config: ModelConfig | None = None) -> None:
        self._config = config or ModelConfig(model="claude-sonnet-4-20250514")

    def complete(
        self,
        messages: tuple[ModelMessage, ...],
        config: ModelConfig | None = None,
    ) -> Result[ModelResponse]:
        cfg = config or self._config
        if not cfg.api_key:
            return Result.fail("Anthropic API key 未设置。请在 config.yaml 中配置 model.api_key 或设置 ANTHROPIC_API_KEY 环境变量")
        try:
            return self._call_api_sync(messages, cfg)
        except Exception as e:
            return Result.fail(f"Anthropic API 调用失败: {e}")

    async def acomplete(
        self,
        messages: tuple[ModelMessage, ...],
        config: ModelConfig | None = None,
    ) -> Result[ModelResponse]:
        cfg = config or self._config
        if not cfg.api_key:
            return Result.fail("Anthropic API key 未设置")
        try:
            return await self._call_api_async(messages, cfg)
        except Exception as e:
            return Result.fail(f"Anthropic API 调用失败: {e}")

    def count_tokens(self, text: str) -> int:
        return len(text) // 4

    def _build_request_body(
        self, messages: tuple[ModelMessage, ...], cfg: ModelConfig
    ) -> tuple[str, list[dict[str, Any]]]:
        system_prompt = cfg.system_prompt
        msg_dicts: list[dict[str, Any]] = []
        for m in messages:
            if m.role.value == "system":
                system_prompt = m.content
            else:
                msg_dicts.append({"role": m.role.value, "content": m.content})
        return system_prompt, msg_dicts

    def _call_api_sync(
        self, messages: tuple[ModelMessage, ...], cfg: ModelConfig
    ) -> Result[ModelResponse]:
        url = (cfg.base_url or "https://api.anthropic.com") + "/v1/messages"
        system_prompt, msg_dicts = self._build_request_body(messages, cfg)
        body: dict[str, Any] = {
            "model": cfg.model,
            "messages": msg_dicts,
            "max_tokens": cfg.max_tokens,
        }
        if system_prompt:
            body["system"] = system_prompt

        headers = {
            "x-api-key": cfg.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            return Result.fail(f"Anthropic API 返回 HTTP {e.code}: {error_body}")
        except urllib.error.URLError as e:
            return Result.fail(f"网络错误: {e.reason}。请检查网络连接")

        return self._parse_response(data, cfg.model)

    async def _call_api_async(
        self, messages: tuple[ModelMessage, ...], cfg: ModelConfig
    ) -> Result[ModelResponse]:
        import aiohttp

        url = (cfg.base_url or "https://api.anthropic.com") + "/v1/messages"
        system_prompt, msg_dicts = self._build_request_body(messages, cfg)
        body: dict[str, Any] = {
            "model": cfg.model,
            "messages": msg_dicts,
            "max_tokens": cfg.max_tokens,
        }
        if system_prompt:
            body["system"] = system_prompt

        headers = {
            "x-api-key": cfg.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    return Result.fail(f"Anthropic API 返回 HTTP {resp.status}: {text}")
                data = await resp.json()

        return self._parse_response(data, cfg.model)

    def _parse_response(
        self, data: dict[str, Any], model: str
    ) -> Result[ModelResponse]:
        content_blocks = data.get("content", [])
        if not content_blocks:
            return Result.fail("Anthropic API 返回空 content")

        content = ""
        for block in content_blocks:
            if block.get("type") == "text":
                content += block.get("text", "")

        usage_raw = data.get("usage", {})
        usage = ModelUsage(
            input_tokens=usage_raw.get("input_tokens", 0),
            output_tokens=usage_raw.get("output_tokens", 0),
        )

        return Result.ok(
            ModelResponse(
                content=content,
                model=data.get("model", model),
                usage=usage,
                finish_reason=data.get("stop_reason", "end_turn"),
                raw=data,
            )
        )
