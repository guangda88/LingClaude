from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from lingclaude.core.models import PermissionDenial, UsageSummary
from lingclaude.core.session import Session, SessionManager
from lingclaude.core.behavior import BehaviorMetrics, Emotion, Intent, detect_emotion, detect_intent, is_tool_intent

logger = logging.getLogger(__name__)

AGENT_MAX_TOOL_ROUNDS = 10


class StopReason(str, Enum):
    COMPLETED = "completed"
    MAX_TURNS_REACHED = "max_turns_reached"
    MAX_BUDGET_REACHED = "max_budget_reached"
    USER_CANCELLED = "user_cancelled"
    ERROR = "error"


@dataclass(frozen=True)
class QueryEngineConfig:
    max_turns: int = 8
    max_budget_tokens: int = 200000
    compact_after_turns: int = 12
    structured_output: bool = False
    structured_retry_limit: int = 2


@dataclass(frozen=True)
class TurnResult:
    prompt: str
    output: str
    matched_commands: tuple[str, ...]
    matched_tools: tuple[str, ...]
    permission_denials: tuple[PermissionDenial, ...]
    usage: UsageSummary
    stop_reason: StopReason


class QueryEngine:
    def __init__(
        self,
        config: QueryEngineConfig | None = None,
        session_manager: SessionManager | None = None,
        model_provider: Any | None = None,
        runtime: Any | None = None,
    ) -> None:
        self.config = config or QueryEngineConfig()
        self.session_manager = session_manager or SessionManager()
        self.session_id: str = uuid4().hex[:16]
        self._messages: list[str] = []
        self._denials: list[PermissionDenial] = []
        self._usage = UsageSummary()
        self._transcript: list[str] = []
        self._provider = model_provider
        self._runtime = runtime
        self._behavior = BehaviorMetrics()

    @property
    def behavior_metrics(self) -> BehaviorMetrics:
        return self._behavior

    @classmethod
    def from_config_file(cls, config_path: str | None = None) -> QueryEngine:
        from lingclaude.core.config import load_config
        from lingclaude.model.factory import create_provider
        from lingclaude.model.types import ModelConfig
        from pathlib import Path as _Path

        cfg = load_config(config_path and _Path(config_path))
        engine_cfg = QueryEngineConfig(
            max_turns=cfg.engine.max_turns,
            max_budget_tokens=cfg.engine.max_budget_tokens,
            compact_after_turns=cfg.engine.compact_after_turns,
            structured_output=cfg.engine.structured_output,
        )

        provider = None
        mc = cfg.model
        model_cfg = ModelConfig(
            model=mc.model,
            api_key=mc.api_key,
            base_url=mc.base_url,
            max_tokens=mc.max_tokens,
            temperature=mc.temperature,
            system_prompt=mc.system_prompt,
        )
        provider_result = create_provider(config=model_cfg, provider_name=mc.provider)
        if provider_result.is_ok:
            provider = provider_result.data

        return cls(config=engine_cfg, model_provider=provider, runtime=None)

    def set_runtime(self, runtime: Any) -> None:
        self._runtime = runtime

    def submit(
        self,
        prompt: str,
        matched_commands: tuple[str, ...] = (),
        matched_tools: tuple[str, ...] = (),
        denied_tools: tuple[PermissionDenial, ...] = (),
    ) -> TurnResult:
        if len(self._messages) >= self.config.max_turns:
            return TurnResult(
                prompt=prompt,
                output=f"已达最大轮次 ({self.config.max_turns})。",
                matched_commands=matched_commands,
                matched_tools=matched_tools,
                permission_denials=denied_tools,
                usage=self._usage,
                stop_reason=StopReason.MAX_TURNS_REACHED,
            )

        output = self._generate_response(prompt, matched_commands, matched_tools, denied_tools)

        self._track_behavior(prompt, output)

        projected = self._usage.add_turn(prompt, output)
        stop_reason = StopReason.COMPLETED
        if projected.input_tokens + projected.output_tokens > self.config.max_budget_tokens:
            stop_reason = StopReason.MAX_BUDGET_REACHED

        self._messages.append(prompt)
        self._transcript.append(prompt)
        self._denials.extend(denied_tools)
        self._usage = projected
        self._compact_if_needed()

        return TurnResult(
            prompt=prompt,
            output=output,
            matched_commands=matched_commands,
            matched_tools=matched_tools,
            permission_denials=denied_tools,
            usage=self._usage,
            stop_reason=stop_reason,
        )

    def stream_submit(
        self,
        prompt: str,
        matched_commands: tuple[str, ...] = (),
        matched_tools: tuple[str, ...] = (),
        denied_tools: tuple[PermissionDenial, ...] = (),
    ) -> Any:
        yield {"type": "message_start", "session_id": self.session_id, "prompt": prompt}
        if matched_commands:
            yield {"type": "command_match", "commands": matched_commands}
        if matched_tools:
            yield {"type": "tool_match", "tools": matched_tools}
        if denied_tools:
            yield {"type": "permission_denial", "denials": [d.tool_name for d in denied_tools]}
        result = self.submit(prompt, matched_commands, matched_tools, denied_tools)
        yield {"type": "message_delta", "text": result.output}
        yield {
            "type": "message_stop",
            "usage": result.usage.to_dict(),
            "stop_reason": result.stop_reason.value,
            "transcript_size": len(self._transcript),
        }

    def persist_session(self) -> str:
        session = Session(
            session_id=self.session_id,
            messages=tuple(self._messages),
            input_tokens=self._usage.input_tokens,
            output_tokens=self._usage.output_tokens,
        )
        path = self.session_manager.save(session)
        return str(path)

    def load_session(self, session_id: str) -> bool:
        session = self.session_manager.load(session_id)
        if session is None:
            return False
        self.session_id = session.session_id
        self._messages = list(session.messages)
        self._usage = UsageSummary(session.input_tokens, session.output_tokens)
        self._transcript = list(session.messages)
        return True

    def reset(self) -> None:
        self.session_id = uuid4().hex[:16]
        self._messages.clear()
        self._denials.clear()
        self._usage = UsageSummary()
        self._transcript.clear()

    @property
    def turn_count(self) -> int:
        return len(self._messages)

    @property
    def usage(self) -> UsageSummary:
        return self._usage

    def get_stats(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "turns": len(self._messages),
            "usage": self._usage.to_dict(),
            "denials": len(self._denials),
            "transcript_size": len(self._transcript),
        }

    def _generate_response(
        self,
        prompt: str,
        matched_commands: tuple[str, ...],
        matched_tools: tuple[str, ...],
        denied_tools: tuple[PermissionDenial, ...],
    ) -> str:
        if self._provider is not None:
            return self._call_model(prompt)

        context_parts = [f"Prompt: {prompt}"]
        if matched_commands:
            context_parts.append(f"已匹配命令: {', '.join(matched_commands)}")
        if matched_tools:
            context_parts.append(f"已匹配工具: {', '.join(matched_tools)}")
        if denied_tools:
            context_parts.append(f"权限拒绝: {len(denied_tools)} 个工具被拒绝")
        return self._format_output(context_parts)

    def _track_behavior(self, prompt: str, output: str) -> None:
        emotion = detect_emotion(prompt)
        intent = detect_intent(prompt)

        self._behavior.total_turns += 1
        self._behavior.emotions_detected.append(emotion)

        if emotion == Emotion.FRUSTRATED:
            self._behavior.frustration_count += 1

        if intent == Intent.CORRECTION:
            self._behavior.corrections_received += 1

    def _track_tool_usage(self, used_tools: bool, prompt: str) -> None:
        intent = detect_intent(prompt)
        if used_tools:
            self._behavior.turns_with_tools += 1
        elif is_tool_intent(intent):
            self._behavior.turns_without_tools_but_needed += 1

    def _call_model(self, prompt: str) -> str:
        from lingclaude.model.types import ModelMessage, MessageRole

        messages: list[ModelMessage] = []
        for prev in self._messages:
            messages.append(ModelMessage(role=MessageRole.USER, content=prev))
        messages.append(ModelMessage(role=MessageRole.USER, content=prompt))

        tools = self._build_openai_tools()
        used_tools = False
        response = None

        for round_idx in range(AGENT_MAX_TOOL_ROUNDS):
            result = self._provider.complete(tuple(messages), tools=tools)
            if result.is_error:
                self._track_tool_usage(False, prompt)
                return f"[模型调用失败] {result.error}"

            response = result.data

            if not response.tool_calls:
                self._track_tool_usage(used_tools, prompt)
                return response.content

            used_tools = True
            self._behavior.tool_call_count += len(response.tool_calls)

            messages.append(ModelMessage(
                role=MessageRole.ASSISTANT,
                content=response.content,
                tool_calls=response.tool_calls,
            ))

            for tc in response.tool_calls:
                tool_output = self._execute_tool(tc.name, tc.arguments)
                if '"error"' in tool_output:
                    self._behavior.tool_error_count += 1
                messages.append(ModelMessage(
                    role=MessageRole.TOOL,
                    content=tool_output,
                    name=tc.name,
                    tool_call_id=tc.id,
                ))

        self._track_tool_usage(used_tools, prompt)
        return response.content if response and response.content else "[达到最大工具调用轮次]"

    def _build_openai_tools(self) -> tuple[dict[str, Any], ...] | None:
        if self._runtime is None:
            return None
        tool_defs = self._runtime.registry.list_tools()
        if not tool_defs:
            return None
        return tuple(
            {
                "name": t.name,
                "description": t.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        k: v for k, v in t.parameters.items()
                    },
                    "required": list(t.parameters.keys()),
                },
            }
            for t in tool_defs
        )

    def _execute_tool(self, name: str, arguments_json: str) -> str:
        try:
            kwargs = json.loads(arguments_json)
        except json.JSONDecodeError:
            return json.dumps({"error": f"Invalid JSON arguments: {arguments_json}"}, ensure_ascii=False)
        try:
            result = self._runtime.execute_tool(name, **kwargs)
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            logger.warning("Tool execution failed: %s.%s -> %s", name, kwargs.keys(), e)
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _compact_if_needed(self) -> None:
        if len(self._messages) > self.config.compact_after_turns:
            self._messages[:] = self._messages[-self.config.compact_after_turns:]
        if len(self._transcript) > self.config.compact_after_turns:
            self._transcript[:] = self._transcript[-self.config.compact_after_turns:]

    def _format_output(self, lines: list[str]) -> str:
        if self.config.structured_output:
            return json.dumps({"summary": lines, "session_id": self.session_id}, indent=2, ensure_ascii=False)
        return "\n".join(lines)
