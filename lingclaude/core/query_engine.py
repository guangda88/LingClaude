from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4

from lingclaude.core.models import PermissionDenial, UsageSummary
from lingclaude.core.session import Session, SessionManager


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
    ):
        self.config = config or QueryEngineConfig()
        self.session_manager = session_manager or SessionManager()
        self.session_id: str = uuid4().hex[:16]
        self._messages: list[str] = []
        self._denials: list[PermissionDenial] = []
        self._usage = UsageSummary()
        self._transcript: list[str] = []

    @classmethod
    def from_config_file(cls, config_path: str | None = None) -> QueryEngine:
        from lingclaude.core.config import load_config

        cfg = load_config(config_path and __import__("pathlib").Path(config_path))
        engine_cfg = QueryEngineConfig(
            max_turns=cfg.engine.max_turns,
            max_budget_tokens=cfg.engine.max_budget_tokens,
            compact_after_turns=cfg.engine.compact_after_turns,
            structured_output=cfg.engine.structured_output,
        )
        return cls(config=engine_cfg)

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
                output=f"Max turns ({self.config.max_turns}) reached.",
                matched_commands=matched_commands,
                matched_tools=matched_tools,
                permission_denials=denied_tools,
                usage=self._usage,
                stop_reason=StopReason.MAX_TURNS_REACHED,
            )

        summary_lines = [
            f"Prompt: {prompt}",
            f"Matched commands: {', '.join(matched_commands) or 'none'}",
            f"Matched tools: {', '.join(matched_tools) or 'none'}",
            f"Permission denials: {len(denied_tools)}",
        ]

        output = self._format_output(summary_lines)
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
    ):
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

    def _compact_if_needed(self) -> None:
        if len(self._messages) > self.config.compact_after_turns:
            self._messages[:] = self._messages[-self.config.compact_after_turns:]
        if len(self._transcript) > self.config.compact_after_turns:
            self._transcript[:] = self._transcript[-self.config.compact_after_turns:]

    def _format_output(self, lines: list[str]) -> str:
        if self.config.structured_output:
            return json.dumps({"summary": lines, "session_id": self.session_id}, indent=2, ensure_ascii=False)
        return "\n".join(lines)
