from __future__ import annotations

import http.client
import json
import logging
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Generator
from urllib.parse import urlparse

from lingclaude.core.types import Result
from lingclaude.model.retry import (
    GlmRetryPolicy,
    is_rate_limit_error,
)
from lingclaude.model.types import (
    ModelConfig,
    ModelMessage,
    ModelProvider,
    ModelResponse,
    ModelUsage,
    ToolCall,
)

try:
    import tiktoken as _tiktoken
except ImportError:
    _tiktoken = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class OpenAIProvider(ModelProvider):
    def __init__(self, config: ModelConfig | None = None) -> None:
        self._config = config or ModelConfig()
        self._encoder: Any = None
        self._retry_policy = GlmRetryPolicy()
        if config:
            self._retry_policy.configure_primary(config.model)

    def complete(
        self,
        messages: tuple[ModelMessage, ...],
        config: ModelConfig | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Result[ModelResponse]:
        cfg = config or self._config
        if not cfg.api_key:
            return Result.fail("OpenAI API key 未设置。请在 config.yaml 中配置 model.api_key 或设置 OPENAI_API_KEY 环境变量")
        try:
            return self._call_with_retry(messages, cfg, tools)
        except Exception as e:
            return Result.fail(f"OpenAI API 调用失败: {e}")

    async def acomplete(
        self,
        messages: tuple[ModelMessage, ...],
        config: ModelConfig | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Result[ModelResponse]:
        cfg = config or self._config
        if not cfg.api_key:
            return Result.fail("OpenAI API key 未设置")
        try:
            return await self._call_api_async(messages, cfg, tools)
        except Exception as e:
            return Result.fail(f"OpenAI API 调用失败: {e}")

    def count_tokens(self, text: str) -> int:
        if _tiktoken is not None:
            if self._encoder is None:
                self._encoder = _tiktoken.encoding_for_model(self._config.model)
            return len(self._encoder.encode(text))
        return len(text) // 4

    def stream_complete(
        self,
        messages: tuple[ModelMessage, ...],
        config: ModelConfig | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        cfg = config or self._config
        if not cfg.api_key:
            yield {"type": "error", "error": "OpenAI API key 未设置"}
            return

        max_retries = 3
        for attempt in range(max_retries + 1):
            retry_cfg = self._config_for_model(cfg)
            got_429 = False
            error_text = ""

            for event in self._do_stream(messages, retry_cfg, tools):
                if event.get("type") == "error":
                    err = event.get("error", "")
                    if is_rate_limit_error(err):
                        got_429 = True
                        error_text = err
                        break
                yield event

            if not got_429:
                self._retry_policy.record_success()
                return

            if attempt < max_retries:
                self._retry_policy.record_failure()
                backoff = self._retry_policy.get_backoff(attempt + 1)
                logger.warning(
                    "流式 429 限流 (attempt %d/%d)，%s 退避 %.1fs",
                    attempt + 1, max_retries,
                    self._retry_policy.current_model, backoff,
                )
                yield {"type": "status", "message": f"限流等待 {backoff:.0f}s ({attempt+1}/{max_retries})..."}
                time.sleep(backoff)

                if self._retry_policy.is_primary and self._retry_policy.should_degrade():
                    self._retry_policy.degrade()
                elif self._retry_policy.is_degraded and self._retry_policy.should_retry_primary():
                    self._retry_policy.reset_to_primary()
                continue

            yield {"type": "error", "error": f"流式请求限流，已重试 {max_retries} 次: {error_text}"}
            return

    def _do_stream(
        self,
        messages: tuple[ModelMessage, ...],
        cfg: ModelConfig,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        base = cfg.base_url or "https://api.openai.com/v1"
        parsed = urlparse(base)
        host = parsed.hostname or "api.openai.com"
        port = parsed.port or 443
        path = (parsed.path or "/v1").rstrip("/") + "/chat/completions"
        body = self._build_request_body(messages, cfg, tools)
        body["stream"] = True
        headers = {
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        content_parts: list[str] = []
        tool_call_accumulators: dict[int, dict[str, Any]] = {}
        finish_reason = "stop"
        usage = ModelUsage()

        try:
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=60)
            conn.request("POST", path, body=json.dumps(body).encode("utf-8"), headers=headers)
            resp = conn.getresponse()

            if resp.status != 200:
                error_body = resp.read().decode("utf-8", errors="replace")
                conn.close()
                yield {"type": "error", "error": f"HTTP {resp.status}: {error_body}"}
                return

            stream_start = time.monotonic()
            STREAM_MAX_SECONDS = 120
            while True:
                if time.monotonic() - stream_start > STREAM_MAX_SECONDS:
                    yield {"type": "error", "error": "流式响应超时（120秒），请重试或检查网络连接"}
                    conn.close()
                    return
                line = resp.readline()
                if not line:
                    break
                line = line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if not line.startswith("data:"):
                    continue
                data_str = line[len("data:"):].strip()
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                choices = data.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                fr = choices[0].get("finish_reason")
                if fr:
                    finish_reason = fr

                if "content" in delta and delta["content"]:
                    content_parts.append(delta["content"])
                    yield {"type": "text_delta", "text": delta["content"]}

                raw_tc = delta.get("tool_calls")
                if raw_tc:
                    for tc_chunk in raw_tc:
                        idx = tc_chunk.get("index", 0)
                        if idx not in tool_call_accumulators:
                            tool_call_accumulators[idx] = {
                                "id": tc_chunk.get("id", ""),
                                "name": "",
                                "arguments": "",
                            }
                        acc = tool_call_accumulators[idx]
                        if tc_chunk.get("id"):
                            acc["id"] = tc_chunk["id"]
                        fn = tc_chunk.get("function", {})
                        if fn.get("name"):
                            acc["name"] = fn["name"]
                        if fn.get("arguments"):
                            acc["arguments"] += fn["arguments"]

                usage_raw = data.get("usage")
                if usage_raw:
                    usage = ModelUsage(
                        input_tokens=usage_raw.get("prompt_tokens", 0),
                        output_tokens=usage_raw.get("completion_tokens", 0),
                    )

            conn.close()

            for idx in sorted(tool_call_accumulators):
                acc = tool_call_accumulators[idx]
                yield {
                    "type": "tool_call_complete",
                    "id": acc["id"],
                    "name": acc["name"],
                    "arguments": acc["arguments"],
                }

            yield {
                "type": "finish",
                "reason": finish_reason,
                "content": "".join(content_parts),
                "usage": usage,
                "model": cfg.model,
            }
        except http.client.HTTPException as e:
            yield {"type": "error", "error": f"HTTP 错误: {e}"}
        except OSError as e:
            yield {"type": "error", "error": f"网络错误: {e}"}
        except Exception as e:
            yield {"type": "error", "error": str(e)}

    def _build_request_body(
        self, messages: tuple[ModelMessage, ...], cfg: ModelConfig,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> dict[str, Any]:
        msg_dicts: list[dict[str, Any]] = []
        has_system = any(getattr(m.role, "value", m.role) == "system" for m in messages)
        if cfg.system_prompt and not has_system:
            msg_dicts.append({"role": "system", "content": cfg.system_prompt})
        for m in messages:
            msg_dicts.append(m.to_dict())
        body: dict[str, Any] = {
            "model": cfg.model,
            "messages": msg_dicts,
            "max_tokens": cfg.max_tokens,
            "temperature": cfg.temperature,
        }
        if tools:
            body["tools"] = [
                {"type": "function", "function": t} for t in tools
            ]
        return body

    def _call_with_retry(
        self, messages: tuple[ModelMessage, ...], cfg: ModelConfig,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Result[ModelResponse]:
        max_retries = 3
        for attempt in range(max_retries + 1):
            retry_cfg = self._config_for_model(cfg)
            result = self._call_api_sync(messages, retry_cfg, tools)

            if result.is_ok:
                self._retry_policy.record_success()
                return result

            error = result.error or ""
            if is_rate_limit_error(error):
                if attempt < max_retries:
                    self._retry_policy.record_failure()
                    backoff = self._retry_policy.get_backoff(attempt + 1)
                    logger.warning(
                        "429 限流 (attempt %d/%d)，%s 退避 %.1fs",
                        attempt + 1, max_retries,
                        self._retry_policy.current_model, backoff,
                    )
                    time.sleep(backoff)

                    if self._retry_policy.is_primary and self._retry_policy.should_degrade():
                        self._retry_policy.degrade()
                    elif self._retry_policy.is_degraded and self._retry_policy.should_retry_primary():
                        self._retry_policy.reset_to_primary()
                    continue
                return Result.fail(
                    f"模型限流，已重试 {max_retries} 次仍失败。"
                    f"最终模型: {self._retry_policy.current_model}，请稍后再试。"
                )

            return result

        return Result.fail("模型调用超出最大重试次数")

    def _config_for_model(self, cfg: ModelConfig) -> ModelConfig:
        target_model = self._retry_policy.current_model
        is_glm = any(m in cfg.model for m in ("glm-", "GLM-"))
        if is_glm and target_model != cfg.model:
            from dataclasses import replace
            return replace(cfg, model=target_model)
        return cfg

    def _call_api_sync(
        self, messages: tuple[ModelMessage, ...], cfg: ModelConfig,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Result[ModelResponse]:
        base = cfg.base_url or "https://api.openai.com/v1"
        url = base.rstrip("/") + "/chat/completions"
        body = self._build_request_body(messages, cfg, tools)
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
            with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 — 固定 OpenAI API URL
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            return Result.fail(f"OpenAI API 返回 HTTP {e.code}: {error_body}")
        except urllib.error.URLError as e:
            return Result.fail(f"网络错误: {e.reason}。请检查网络连接")

        return self._parse_response(data, cfg.model)

    async def _call_api_async(
        self, messages: tuple[ModelMessage, ...], cfg: ModelConfig,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Result[ModelResponse]:
        import aiohttp

        base = cfg.base_url or "https://api.openai.com/v1"
        url = base.rstrip("/") + "/chat/completions"
        body = self._build_request_body(messages, cfg, tools)
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
        message = choice.get("message", {})
        content = message.get("content", "") or ""
        finish_reason = choice.get("finish_reason", "stop")

        raw_tool_calls = message.get("tool_calls")
        parsed_calls: tuple[ToolCall, ...] = ()
        if raw_tool_calls:
            parsed_calls = tuple(
                ToolCall(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments=tc["function"]["arguments"],
                )
                for tc in raw_tool_calls
            )
            if not content and parsed_calls:
                content = ""

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
                tool_calls=parsed_calls,
            )
        )
