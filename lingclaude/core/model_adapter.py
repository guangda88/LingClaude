"""LINGKERNEL_v1 task #1 (激进拆包 D1) - ModelAdapter 模块

dsh 对位: `llm/llm` - message/stream 词表 + adapter seam。
从 query_engine.py 抽取: _call_model / stream_call_model / _resolve_model_config
的模型调用层。

设计:
- ModelAdapter 不知道 QueryEngine (零反向依赖)
- 通过 call / stream_call 两个方法暴露, 内部处理 fallback 链
- resolve_config 独立成纯函数, 便于测试
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Generator

from lingclaude.core.types import Result


logger = logging.getLogger(__name__)


@dataclass
class ModelCallResult:
    content: str
    tool_calls: tuple = ()
    finish_reason: str = ""
    used_fallback: bool = False
    provider_name: str = ""


class ModelAdapter:
    """模型调用 seam (query_engine 模型层抽取)。

    QueryEngine 持有 ModelAdapter; 所有 provider 交互走这里。
    """

    def __init__(self, provider: Any | None = None) -> None:
        self._provider = provider

    @property
    def provider(self) -> Any | None:
        return self._provider

    def set_provider(self, provider: Any) -> None:
        self._provider = provider

    def call(
        self,
        messages: tuple,
        tools: Any = None,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Result[ModelCallResult]:
        """单次模型调用。失败返回 Result.fail, 不抛异常。"""
        if self._provider is None:
            return Result.fail("No model provider configured", code="NO_PROVIDER")
        try:
            result = self._provider.complete(messages, tools=tools)
            if result.is_error:
                return Result.fail(str(result.error), code="PROVIDER_ERROR")
            resp = result.data
            return Result.ok(ModelCallResult(
                content=resp.content or "",
                tool_calls=getattr(resp, "tool_calls", ()) or (),
                finish_reason=getattr(resp, "finish_reason", ""),
                provider_name=type(self._provider).__name__,
            ))
        except Exception as e:
            logger.warning("model call failed: %s", e)
            return Result.fail(f"Model call failed: {e}", code="EXECUTION_ERROR")

    def stream_call(
        self,
        messages: tuple,
        tools: Any = None,
    ) -> Generator[dict[str, Any], None, None] | None:
        """流式模型调用。返回 generator 或 None (provider 不支持/未配置)。

        yield 的 dict 结构: {"type": "chunk"|"tool_calls"|"done", ...}
        """
        if self._provider is None:
            return None
        stream_fn = getattr(self._provider, "stream", None)
        if stream_fn is None:
            return None
        try:
            return stream_fn(messages, tools=tools)
        except Exception as e:
            logger.warning("stream call failed: %s", e)
            return None


def resolve_model_config(
    prompt: str,
    config: Any,
    *,
    behavior_hallucination_risk: float = 0.0,
) -> tuple[Any, str]:
    """路由决策 (纯函数, 便于测试)。

    返回 (model_config, reason)。规则:
    - 幻觉风险 > 0.5 -> 强模型 (config.strong 或第一个)
    - 默认 -> config 默认模型
    """
    reason = "default"
    chosen = config
    if behavior_hallucination_risk > 0.5:
        strong = getattr(config, "strong", None)
        if strong is not None:
            chosen = strong
            reason = f"hallucination_risk={behavior_hallucination_risk:.2f}->strong"
    return chosen, reason