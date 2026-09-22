from __future__ import annotations

import http.client
import json
import logging
import ssl
import time
import os
import urllib.error
import urllib.request
from dataclasses import replace
from typing import Any, Generator
from urllib.parse import urlparse

from lingclaude.core.types import Result
from lingclaude.core.datalog import log_model_call
from lingclaude.model.retry import (
    GlmRetryPolicy,
    is_hard_quota_error,
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

# k3 系列推理模型只接受 temperature=1（实测 400: "invalid temperature:
# only 1 is allowed for this model"）。前缀匹配覆盖 k3 / k3-256k / kimi-k3 等。
# 2026-09-14 灵元：策略外置 —— 前缀列表迁到 policies/model_policy.yaml，
# 改温度约束只改 YAML 不动代码（PolicyLoader mtime watch 热更）。
_FALLBACK_TEMP_LOCKED_PREFIXES: tuple[str, ...] = ("k3", "kimi-k3", "kimi-k2-thinking")


def _temp_locked_prefixes() -> tuple[str, ...]:
    """从 policies/model_policy.yaml 读取温度锁前缀；读失败回退内置默认。"""
    try:
        from lingclaude.core import policy_loader

        data = policy_loader.load("model_policy")
        raw = data.get("temp_locked_model_prefixes")
        if isinstance(raw, list) and raw:
            prefixes = tuple(str(p).strip() for p in raw if str(p).strip())
            if prefixes:
                return prefixes
    except Exception:  # noqa: BLE001 — 策略加载失败绝不影响主流程
        pass
    return _FALLBACK_TEMP_LOCKED_PREFIXES


def _effective_temperature(model: str, temperature: float) -> float:
    """部分推理模型对 temperature 有硬约束，越界值直接 400。"""
    m = model.lower()
    if any(m == p or m.startswith(p) for p in _temp_locked_prefixes()):
        return 1.0
    return temperature


# ── proxy3 客户端支持（2026-09-15）：双 header 注入 + free/* opt-in ──
# proxy3 (:8765) 鉴权要求 Authorization Bearer（api_keys.json）+ X-Agent-Id；
# free/* 虚拟模型另需 opt-in 门（x-free-pool-optin: true，默认禁入防敏感
# 内容外泄）。fail-open：非 proxy3 端点返回空 dict，零影响。
_PROXY3_HOST_SUFFIX = "127.0.0.1:8765"
_PROXY3_AGENT_ID = os.environ.get("LINGCLAUDE_PROXY3_AGENT_ID", "lingclaude")


def _proxy3_extra_headers(base_url: str, body: dict[str, Any]) -> dict[str, str]:
    """base_url 指向 proxy3 时补齐鉴权/身份/opt-in header，否则返回空。

    敏感守卫（2026-09-15）：free/* 请求先对 messages 内容做密钥形态扫描
    （复用 core/redact._SENSITIVE_PATTERNS），命中即**停发 opt-in header**
    ——请求被 proxy3 403 `free_pool_optin_required` 挡回，敏感内容不进
    may-log 免费池。这是内容级守卫，不依赖调用方自觉。
    """
    try:
        host = (urlparse(base_url).netloc or "").lower()
        if ":8765" not in host or host.split(":")[0] not in ("127.0.0.1", "localhost"):
            return {}
        headers = {"X-Agent-Id": _PROXY3_AGENT_ID}
        model = str(body.get("model", ""))
        if model.startswith("free/") and not _messages_contain_secrets(body):
            headers["x-free-pool-optin"] = "true"
        return headers
    except Exception:  # noqa: BLE001 — 判定失败绝不阻塞主流程
        return {}


def _messages_contain_secrets(body: dict[str, Any]) -> bool:
    """扫描请求 messages 是否含密钥形态（守卫 may-log 免费池）。"""
    from lingclaude.core.redact import contains_sensitive

    for msg in body.get("messages", ()):  # 扫描全部角色内容
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str) and contains_sensitive(content):
            return True
        if isinstance(content, list):  # 多模态 content blocks
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str) \
                        and contains_sensitive(block["text"]):
                    return True
    return False



def _extract_usage(usage_raw: dict[str, Any] | None) -> ModelUsage | None:
    """统一 usage 解析（2026-09-22 cache 0% 修复）。

    prompt_tokens_details.cached_tokens 是缓存命中唯一来源，此前只有
    流式尾帧（choices=[] 终结 chunk）解析点读取它，带 choices 的 chunk
    与非流式 complete() 两处都会把带 cached 的 ModelUsage 覆盖/丢失，
    表现为 toolbar 恒显 cache 0%。「缓存信息」与「真 0 命中」无法区分，
    本函数解析到什么就带什么，缺失语义交给显示层（0=未知不显示）。
    """
    if not usage_raw or not isinstance(usage_raw, dict):
        return None
    ptd = usage_raw.get("prompt_tokens_details")
    cached = ptd.get("cached_tokens", 0) if isinstance(ptd, dict) else 0
    return ModelUsage(
        input_tokens=usage_raw.get("prompt_tokens", 0),
        output_tokens=usage_raw.get("completion_tokens", 0),
        cached_tokens=int(cached or 0),
    )


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
        # F12d:本地推理服务(waterfall/deepseek proxy 等)无 key 合法,放行。
        # 只对云端 base 拒发 — 与 task_router F12b 跳过语义同源(cfg.is_local_base)。
        # ⚠️ 2026-08-29 实证:此检查若去掉,路由到本地 waterfall(无 key)时报
        # 「OpenAI API key 未设置」——该 bug 由本检查回滚引入,勿再移除。
        if not cfg.api_key and not cfg.is_local_base():
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
        if not cfg.api_key and not cfg.is_local_base():
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
        # F12d:本地服务无 key 合法 — 放行(与 complete/acomplete 同条件)。
        if not cfg.api_key and not cfg.is_local_base():
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
                self._retry_policy.record_success(actual_model=retry_cfg.model)
                return

            # 2026-09-16: 硬性配额耗尽（GLM 1308 5h 限额等）——退避重试无意义
            # （重置时间在小时级），直接终止让上层切 provider，不再空烧 60s。
            if is_hard_quota_error(error_text):
                logger.warning("硬配额耗尽，跳过退避重试直接失败: %s", error_text[:120])
                yield {"type": "error", "error": f"硬配额耗尽（需等待重置或切换 provider）: {error_text}"}
                return

            if attempt < max_retries:
                # is_rate_limit=True: 连续 429 必须累积熔断计数（默认 False
                # 会清零 _circuit_consecutive_429，导致熔断器永不开启——
                # 1308 事故中连续 429 未熔断即此因）。
                self._retry_policy.record_failure(is_rate_limit=True)
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
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = (parsed.path or "/v1").rstrip("/") + "/chat/completions"
        body = self._build_request_body(messages, cfg, tools)
        body["stream"] = True
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if cfg.api_key:  # F12d:本地无 key 时省略 Authorization,不发空 Bearer
            headers["Authorization"] = f"Bearer {cfg.api_key}"
        headers.update(_proxy3_extra_headers(base, body))

        content_parts: list[str] = []
        tool_call_accumulators: dict[int, dict[str, Any]] = {}
        finish_reason = "stop"
        usage = ModelUsage()
        _t0 = time.monotonic()

        # F12i:流挂起可见性 — readline 最长阻塞 120s,期间零事件用户以为卡死。
        yield {"type": "status", "message": f"连接 {host}:{port} 等待首 token..."}

        try:
            if parsed.scheme == "https":
                ctx = ssl.create_default_context()
                conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=120)
            else:
                conn = http.client.HTTPConnection(host, port, timeout=120)
            conn.request("POST", path, body=json.dumps(body).encode("utf-8"), headers=headers)
            resp = conn.getresponse()

            if resp.status != 200:
                error_body = resp.read().decode("utf-8", errors="replace")
                conn.close()
                yield {"type": "error", "error": f"HTTP {resp.status}: {error_body}"}
                return

            # 修复:此前从流开始计总时长,长思考模型(glm-5.3-flash 带推理)
            # 正常输出超 120s 即被误杀。改为"静默超时"——每收到一个 chunk
            # 重置计时,只有持续无数据才判定连接死亡。
            stream_start = time.monotonic()
            STREAM_IDLE_SECONDS = 180
            while True:
                if time.monotonic() - stream_start > STREAM_IDLE_SECONDS:
                    yield {"type": "error", "error": "流式响应静默超时（180秒无数据），请重试或检查网络连接"}
                    conn.close()
                    return
                line = resp.readline()
                if not line:
                    break
                stream_start = time.monotonic()  # 有数据到达 → 重置静默计时
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

                # 2026-09-21: usage 尾帧修复——OpenAI 协议里 stream_options.
                # include_usage 的 usage 在 choices 为空数组的终结 chunk 中到达
                # （volcengine/智谱实测 choices:[]），此前 `if not choices: continue`
                # 把它直接跳过 → usage 恒 0、缓存命中不可观测。解析必须在
                # choices 守卫之前。
                usage_raw = data.get("usage")
                # 2026-09-22: 统一走 _extract_usage（缓存命中可观测）
                _u = _extract_usage(usage_raw)
                if _u is not None:
                    usage = _u

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
                # 2026-09-22: 此前此处只读 prompt/completion 并覆盖上一解析点
                # 的 ModelUsage → 尾帧带 cached 也被丢弃（cache 0% 根因之一）
                _u = _extract_usage(usage_raw)
                if _u is not None:
                    usage = _u

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
            # atomcode#1 (P0): usage 在流中到达，此处置记 cost
            log_model_call(
                model=cfg.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                finish_reason=finish_reason,
                latency_ms=(time.monotonic() - _t0) * 1000,
                path="stream",
                cached_tokens=usage.cached_tokens,
            )
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
            "temperature": _effective_temperature(cfg.model, cfg.temperature),
        }
        # atomcode#1 (P0): SSE 默认不带 usage，显式请求使 cost 可统计。
        # 不支持该字段的端点会忽略它（OpenAI 协议对未知字段宽容）。
        body["stream_options"] = {"include_usage": True}
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
                self._retry_policy.record_success(actual_model=retry_cfg.model)
                return result

            error = result.error or ""
            if is_rate_limit_error(error):
                # 2026-09-16: 硬配额耗尽与流式路径同语义（见 stream_complete
                # 429 分支）——GLM 1308 5h 限额退避重试无意义，直接失败让
                # 上层切 provider，不再空烧 60s。
                if is_hard_quota_error(error):
                    logger.warning("硬配额耗尽，跳过退避重试直接失败: %s", error[:120])
                    return Result.fail(f"硬配额耗尽（需等待重置或切换 provider）: {error}")

                if attempt < max_retries:
                    # is_rate_limit=True: 连续 429 必须累积熔断计数（默认 False
                    # 会清零 _circuit_consecutive_429，导致熔断器永不开启）。
                    self._retry_policy.record_failure(is_rate_limit=True)
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
            return replace(cfg, model=target_model)
        return cfg

    def _prepare_request(
        self, messages: tuple[ModelMessage, ...], cfg: ModelConfig,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> tuple[str, dict[str, Any], dict[str, str]]:
        """构造 chat/completions 请求三元组 (url, body, headers)。

        同步/异步两条 API 调用链共用——逐字重复收敛(维护点 2→1)。
        """
        base = cfg.base_url or "https://api.openai.com/v1"
        url = base.rstrip("/") + "/chat/completions"
        body = self._build_request_body(messages, cfg, tools)
        headers = {
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        }
        headers.update(_proxy3_extra_headers(base, body))
        return url, body, headers

    def _call_api_sync(
        self, messages: tuple[ModelMessage, ...], cfg: ModelConfig,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Result[ModelResponse]:
        url, body, headers = self._prepare_request(messages, cfg, tools)
        _t0 = time.monotonic()

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

        return self._parse_response(data, cfg.model, latency_ms=(time.monotonic() - _t0) * 1000)

    async def _call_api_async(
        self, messages: tuple[ModelMessage, ...], cfg: ModelConfig,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Result[ModelResponse]:
        import aiohttp

        url, body, headers = self._prepare_request(messages, cfg, tools)
        _t0 = time.monotonic()

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=300),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    return Result.fail(f"OpenAI API 返回 HTTP {resp.status}: {text}")
                data = await resp.json()

        return self._parse_response(data, cfg.model, latency_ms=(time.monotonic() - _t0) * 1000)

    def _parse_response(
        self, data: dict[str, Any], model: str, latency_ms: float = 0.0
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
        # 2026-09-22: 此前非流式路径彻底不读 prompt_tokens_details（cache 0% 根因之二）
        usage = _extract_usage(usage_raw) or ModelUsage()

        model_used = data.get("model", model)
        # atomcode#1 (P0): 非流式路径 cost 埋点（sync/async 共用本出口）
        log_model_call(
            model=model_used,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            finish_reason=finish_reason,
            latency_ms=latency_ms,
            path="complete",
            cached_tokens=usage.cached_tokens,
        )

        return Result.ok(
            ModelResponse(
                content=content,
                model=model_used,
                usage=usage,
                finish_reason=finish_reason,
                raw=data,
                tool_calls=parsed_calls,
            )
        )
