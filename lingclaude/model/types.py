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
    system_prompt: str = "你是灵克，一个 AI 编程助手。"

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
