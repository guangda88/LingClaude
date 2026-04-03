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

try:
    import tiktoken as _tiktoken
except ImportError:
    _tiktoken = None  # type: ignore[assignment]


class OpenAIProvider(ModelProvider):
    def __init__(self, config: ModelConfig | None = None) -> None:
        self._config = config or ModelConfig()
        self._encoder: Any = None

    def complete(
        self,
        messages: tuple[ModelMessage, ...],
        config: ModelConfig | None = None,
    ) -> Result[ModelResponse]:
        cfg = config or self._config
        if not cfg.api_key:
            return Result.fail("OpenAI API key 未设置。请在 config.yaml 中配置 model.api_key 或设置 OPENAI_API_KEY 环境变量")
        try:
            return self._call_api_sync(messages, cfg)
        except Exception as e:
            return Result.fail(f"OpenAI API 调用失败: {e}")

    async def acomplete(
        self,
        messages: tuple[ModelMessage, ...],
        config: ModelConfig | None = None,
    ) -> Result[ModelResponse]:
        cfg = config or self._config
        if not cfg.api_key:
            return Result.fail("OpenAI API key 未设置")
        try:
            return await self._call_api_async(messages, cfg)
        except Exception as e:
            return Result.fail(f"OpenAI API 调用失败: {e}")

    def count_tokens(self, text: str) -> int:
        if _tiktoken is not None:
            if self._encoder is None:
                self._encoder = _tiktoken.encoding_for_model(self._config.model)
            return len(self._encoder.encode(text))
        return len(text) // 4

    def _build_request_body(
        self, messages: tuple[ModelMessage, ...], cfg: ModelConfig
    ) -> dict[str, Any]:
        msg_dicts: list[dict[str, Any]] = []
        if cfg.system_prompt:
            msg_dicts.append({"role": "system", "content": cfg.system_prompt})
        for m in messages:
            msg_dicts.append(m.to_dict())
        return {
            "model": cfg.model,
            "messages": msg_dicts,
            "max_tokens": cfg.max_tokens,
            "temperature": cfg.temperature,
        }

    def _call_api_sync(
        self, messages: tuple[ModelMessage, ...], cfg: ModelConfig
    ) -> Result[ModelResponse]:
        base = cfg.base_url or "https://api.openai.com/v1"
        url = base.rstrip("/") + "/chat/completions"
        body = self._build_request_body(messages, cfg)
        headers = {
            "Authorization": f"Bearer {cfg.api_key}",
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
            return Result.fail(f"OpenAI API 返回 HTTP {e.code}: {error_body}")
        except urllib.error.URLError as e:
            return Result.fail(f"网络错误: {e.reason}。请检查网络连接")

        return self._parse_response(data, cfg.model)

    async def _call_api_async(
        self, messages: tuple[ModelMessage, ...], cfg: ModelConfig
    ) -> Result[ModelResponse]:
        import aiohttp

        base = cfg.base_url or "https://api.openai.com/v1"
        url = base.rstrip("/") + "/chat/completions"
        body = self._build_request_body(messages, cfg)
        headers = {
            "Authorization": f"Bearer {cfg.api_key}",
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
                    return Result.fail(f"OpenAI API 返回 HTTP {resp.status}: {text}")
                data = await resp.json()

        return self._parse_response(data, cfg.model)

    def _parse_response(
        self, data: dict[str, Any], model: str
    ) -> Result[ModelResponse]:
        choices = data.get("choices", [])
        if not choices:
            return Result.fail("OpenAI API 返回空 choices")

        choice = choices[0]
        content = choice.get("message", {}).get("content", "")
        finish_reason = choice.get("finish_reason", "stop")

        usage_raw = data.get("usage", {})
        usage = ModelUsage(
            input_tokens=usage_raw.get("prompt_tokens", 0),
            output_tokens=usage_raw.get("completion_tokens", 0),
        )

        return Result.ok(
            ModelResponse(
                content=content,
                model=data.get("model", model),
                usage=usage,
                finish_reason=finish_reason,
                raw=data,
            )
        )
