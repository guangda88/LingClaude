from __future__ import annotations

import http.client
import json
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Generator
from urllib.parse import urlparse

from lingclaude.core.types import Result
from lingclaude.model.types import (
    ModelConfig,
    ModelMessage,
    ModelProvider,
    ModelResponse,
    ModelUsage,
    ToolCall,
)


class AnthropicProvider(ModelProvider):
    def __init__(self, config: ModelConfig | None = None) -> None:
        self._config = config or ModelConfig(model="claude-sonnet-4-20250514")

    def complete(
        self,
        messages: tuple[ModelMessage, ...],
        config: ModelConfig | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Result[ModelResponse]:
        cfg = config or self._config
        if not cfg.api_key:
            return Result.fail("Anthropic API key 未设置。请在 config.yaml 中配置 model.api_key 或设置 ANTHROPIC_API_KEY 环境变量")
        try:
            return self._call_api_sync(messages, cfg, tools)
        except Exception as e:
            return Result.fail(f"Anthropic API 调用失败: {e}")

    async def acomplete(
        self,
        messages: tuple[ModelMessage, ...],
        config: ModelConfig | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Result[ModelResponse]:
        cfg = config or self._config
        if not cfg.api_key:
            return Result.fail("Anthropic API key 未设置")
        try:
            return await self._call_api_async(messages, cfg, tools)
        except Exception as e:
            return Result.fail(f"Anthropic API 调用失败: {e}")

    def count_tokens(self, text: str) -> int:
        return len(text) // 4

    def _build_request_body(
        self,
        messages: tuple[ModelMessage, ...],
        cfg: ModelConfig,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        system_prompt = cfg.system_prompt
        msg_dicts: list[dict[str, Any]] = []
        for m in messages:
            if m.role.value == "system":
                system_prompt = m.content
            elif m.role.value == "assistant" and m.tool_calls:
                content_blocks: list[dict[str, Any]] = []
                if m.content:
                    content_blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    try:
                        input_data = json.loads(tc.arguments)
                    except (json.JSONDecodeError, TypeError):
                        input_data = {}
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": input_data,
                    })
                msg_dicts.append({"role": "assistant", "content": content_blocks})
            elif m.role.value == "tool":
                tool_result_block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id or "",
                    "content": m.content,
                }
                msg_dicts.append({"role": "user", "content": [tool_result_block]})
            else:
                msg_dicts.append({"role": m.role.value, "content": m.content})
        return system_prompt, msg_dicts

    def _build_body(
        self,
        messages: tuple[ModelMessage, ...],
        cfg: ModelConfig,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> dict[str, Any]:
        system_prompt, msg_dicts = self._build_request_body(messages, cfg, tools)
        body: dict[str, Any] = {
            "model": cfg.model,
            "messages": msg_dicts,
            "max_tokens": cfg.max_tokens,
        }
        if system_prompt:
            body["system"] = system_prompt
        if tools:
            body["tools"] = [
                {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
                }
                for t in tools
            ]
        return body

    def _call_api_sync(
        self,
        messages: tuple[ModelMessage, ...],
        cfg: ModelConfig,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Result[ModelResponse]:
        url = (cfg.base_url or "https://api.anthropic.com") + "/v1/messages"
        body = self._build_body(messages, cfg, tools)

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
        self,
        messages: tuple[ModelMessage, ...],
        cfg: ModelConfig,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Result[ModelResponse]:
        import aiohttp

        url = (cfg.base_url or "https://api.anthropic.com") + "/v1/messages"
        body = self._build_body(messages, cfg, tools)

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
        parsed_calls: list[ToolCall] = []
        for block in content_blocks:
            block_type = block.get("type", "")
            if block_type == "text":
                content += block.get("text", "")
            elif block_type == "tool_use":
                input_data = block.get("input", {})
                parsed_calls.append(ToolCall(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    arguments=json.dumps(input_data, ensure_ascii=False),
                ))

        usage_raw = data.get("usage", {})
        usage = ModelUsage(
            input_tokens=usage_raw.get("input_tokens", 0),
            output_tokens=usage_raw.get("output_tokens", 0),
        )

        stop_reason = data.get("stop_reason", "end_turn")
        finish_reason = "tool_calls" if parsed_calls else stop_reason

        return Result.ok(
            ModelResponse(
                content=content,
                model=data.get("model", model),
                usage=usage,
                finish_reason=finish_reason,
                raw=data,
                tool_calls=tuple(parsed_calls),
            )
        )

    def stream_complete(
        self,
        messages: tuple[ModelMessage, ...],
        config: ModelConfig | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        cfg = config or self._config
        if not cfg.api_key:
            yield {"type": "error", "error": "Anthropic API key 未设置"}
            return
        yield from self._do_stream(messages, cfg, tools)

    def _do_stream(
        self,
        messages: tuple[ModelMessage, ...],
        cfg: ModelConfig,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        base = cfg.base_url or "https://api.anthropic.com"
        parsed = urlparse(base)
        host = parsed.hostname or "api.anthropic.com"
        port = parsed.port or 443
        path = (parsed.path or "").rstrip("/") + "/v1/messages"
        body = self._build_body(messages, cfg, tools)
        body["stream"] = True

        headers = {
            "x-api-key": cfg.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        content_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        current_tool_call: dict[str, Any] = {}
        finish_reason = "end_turn"
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
                    yield {"type": "error", "error": "流式响应超时（120秒）"}
                    conn.close()
                    return
                try:
                    line = resp.readline()
                except StopIteration:
                    break
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

                event_type = data.get("type", "")

                if event_type == "content_block_delta":
                    delta = data.get("delta", {})
                    delta_type = delta.get("type", "")
                    if delta_type == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            content_parts.append(text)
                            yield {"type": "text_delta", "text": text}
                    elif delta_type == "input_json_delta":
                        partial = delta.get("partial_json", "")
                        if current_tool_call:
                            current_tool_call["arguments"] += partial

                elif event_type == "content_block_start":
                    cb = data.get("content_block", {})
                    if cb.get("type") == "tool_use":
                        current_tool_call = {
                            "id": cb.get("id", ""),
                            "name": cb.get("name", ""),
                            "arguments": "",
                        }

                elif event_type == "content_block_stop":
                    if current_tool_call and current_tool_call.get("name"):
                        tool_calls.append(current_tool_call)
                        current_tool_call = {}

                elif event_type == "message_delta":
                    delta = data.get("delta", {})
                    if delta.get("stop_reason"):
                        finish_reason = delta["stop_reason"]
                    usage_raw = data.get("usage", {})
                    if usage_raw:
                        usage = ModelUsage(
                            input_tokens=usage_raw.get("input_tokens", usage.input_tokens),
                            output_tokens=usage_raw.get("output_tokens", usage.output_tokens),
                        )

                elif event_type == "message_start":
                    msg = data.get("message", {})
                    msg_usage = msg.get("usage", {})
                    if msg_usage:
                        usage = ModelUsage(
                            input_tokens=msg_usage.get("input_tokens", 0),
                            output_tokens=msg_usage.get("output_tokens", 0),
                        )

            conn.close()

            for tc in tool_calls:
                yield {
                    "type": "tool_call_complete",
                    "id": tc["id"],
                    "name": tc["name"],
                    "arguments": tc["arguments"],
                }

            yield {
                "type": "finish",
                "reason": "tool_calls" if tool_calls else finish_reason,
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
