"""model_types.py — 灵元 1.0 主干契约（provider_proto）。

依据: docs/LINGYUAN_1.0_ANALYSIS_AND_ROADMAP.md
  - 主干 8 文件之一 = provider_proto（模型协议契约）
  - 主干零 import 插片：类型契约是主干，model/ 插片引用主干契约，
    而非主干引用 model 层（消除 core/ → model/ 顶层倒装 5 处）

内容: 模型消息/用量/响应/配置纯数据类型 + ModelProvider 抽象协议。
model/types.py 保留为兼容 re-export 层（tests/ 22 处 + model/ 内部引用不断）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any

from lingclaude.core.types import Result


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True)
class ModelMessage:
    role: MessageRole
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] | None = None
    # T1-4: 多模态 — 图片内容（base64 + mime type）
    # 生产者: query_engine._extract_image_content（read 图片结果的三条工具路径均接线）。
    # openai_provider 经 to_dict 透传为 OpenAI image_url content blocks
    # （anthropic_provider 不走 to_dict，暂不消费本字段）。
    image_content: tuple[str, str] | None = None  # (base64_data, mime_type)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": getattr(self.role, "value", self.role)}
        # T1-4: 多模态 content blocks
        if self.image_content is not None:
            b64, mime = self.image_content
            d["content"] = [
                {"type": "text", "text": self.content},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                },
            ]
        else:
            d["content"] = self.content
        if self.name is not None:
            d["name"] = self.name
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.tool_calls is not None:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.arguments,
                    },
                }
                for tc in self.tool_calls
            ]
        return d


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    # 2026-09-21: 缓存命中 token（OpenAI prompt_tokens_details.cached_tokens）。
    # 0 = 无缓存信息或未命中。前缀缓存优化（同日）的可观测性依据。
    cached_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        d: dict[str, int] = {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }
        # 仅在有缓存命中信息时输出（0 = 无信息，不污染下游快照/统计）
        if self.cached_tokens:
            d["cached_tokens"] = self.cached_tokens
        return d


@dataclass(frozen=True)
class ModelResponse:
    content: str
    model: str
    usage: ModelUsage
    finish_reason: str = "stop"
    raw: dict[str, Any] | None = None
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class ModelConfig:
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.7
    system_prompt: str = (
        "你是灵克，一个会自我进化的开源 AI 编程助手。\n"
        "\n"
        "核心规则:\n"
        "1. 回答代码相关问题时，必须先用工具（read/grep/glob）读取源码，不要猜测。\n"
        "2. 如果用户指出你胡说或没读代码，立即使用工具重新阅读相关文件。\n"
        "3. 你擅长代码理解、编辑、终端操作，并通过自优化持续提升能力。\n"
        "4. 用中文回答，代码保持原样。"
    )

    def is_local_base(self) -> bool:
        """本地推理服务(localhost/127.0.0.1)不需要 api_key。

        F12d 语义统一:task_router 路由跳过判断(F12b)与 provider 层空 key
        放行判断(F12d)必须同源,否则出现「路由认为本地无 key 合法、
        provider 却拒发」的语义撕裂(实测 run -i 报 OpenAI API key 未设置)。
        """
        if not self.base_url:
            return False
        from urllib.parse import urlparse
        host = urlparse(self.base_url).hostname or ""
        return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ModelConfig:
        return cls(
            model=raw.get("model", "gpt-4o"),
            api_key=raw.get("api_key", ""),
            base_url=raw.get("base_url"),
            max_tokens=raw.get("max_tokens", 4096),
            temperature=raw.get("temperature", 0.7),
            system_prompt=raw.get("system_prompt", "你是灵克，一个 AI 编程助手。"),
        )


class ModelProvider(ABC):
    @abstractmethod
    def complete(
        self,
        messages: tuple[ModelMessage, ...],
        config: ModelConfig | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Result[ModelResponse]:
        ...

    @abstractmethod
    async def acomplete(
        self,
        messages: tuple[ModelMessage, ...],
        config: ModelConfig | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Result[ModelResponse]:
        ...

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        ...

    def stream_complete(
        self,
        messages: tuple[ModelMessage, ...],
        config: ModelConfig | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Any:
        result = self.complete(messages, config, tools)
        if result.is_error:
            yield {"type": "error", "error": result.error}
            return
        resp = result.data
        if resp.content:
            yield {"type": "text_delta", "text": resp.content}
        for tc in resp.tool_calls:
            yield {"type": "tool_call_complete", "id": tc.id, "name": tc.name, "arguments": tc.arguments}
        yield {"type": "finish", "reason": resp.finish_reason}
