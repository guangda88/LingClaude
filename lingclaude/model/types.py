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

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role.value, "content": self.content}
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

    def to_dict(self) -> dict[str, int]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


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
