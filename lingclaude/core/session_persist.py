"""Session persistence helpers extracted from QueryEngine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lingclaude.core.models import UsageSummary
from lingclaude.core.session import Session
from lingclaude.core.types import Result


class SessionPersister:
    """Encapsulates session persistence logic extracted from QueryEngine."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def persist_session(self) -> Result[str]:
        engine = self._engine
        session = Session(
            session_id=engine.session_id,
            messages=tuple(engine._messages),
            input_tokens=engine._usage.input_tokens,
            output_tokens=engine._usage.output_tokens,
        )
        result = engine.session_manager.save(session)
        if result.is_error:
            return result  # type: ignore[return-value]
        return Result.ok(str(result.data))

    def load_session(self, session_id: str) -> bool:
        engine = self._engine
        result = engine.session_manager.load(session_id)
        if result.is_error:
            return False
        session = result.data
        engine.session_id = session.session_id
        engine._messages = list(session.messages)
        engine._usage = UsageSummary(session.input_tokens, session.output_tokens)
        engine._transcript = list(session.messages)
        engine._conversation.clear()
        for i in range(0, len(session.messages) - 1, 2):
            user_msg = session.messages[i]
            asst_msg = session.messages[i + 1] if i + 1 < len(session.messages) else ""
            engine._conversation.append(("user", user_msg))
            engine._conversation.append(("assistant", asst_msg))
        return True

    def clear_checkpoint(self) -> None:
        engine = self._engine
        engine._sync_session_store()
        engine.session_store.clear_checkpoint()
        engine._active_checkpoint = None

    def save_checkpoint(
        self,
        messages: list[Any],
        round_idx: int,
        prompt: str,
        used_tools: bool,
        total_input: int,
        total_output: int,
    ) -> None:
        engine = self._engine
        engine._sync_session_store()
        cp = engine.session_store.save_checkpoint(
            messages=messages,
            round_idx=round_idx,
            prompt=prompt,
            used_tools=used_tools,
            total_input=total_input,
            total_output=total_output,
            conversation=engine._conversation,
        )
        if cp is not None:
            engine._active_checkpoint = cp

    def load_checkpoint(self) -> dict[str, Any] | None:
        engine = self._engine
        engine._sync_session_store()
        cp_path = Path(engine.session_store._checkpoint_dir) / f"{engine.session_id}.json"
        cd = engine.session_store.load_checkpoint()
        if cd is None:
            return None
        engine._active_checkpoint = cp_path
        return {
            "session_id": cd.session_id,
            "prompt": cd.prompt,
            "round_idx": cd.round_idx,
            "used_tools": cd.used_tools,
            "total_input": cd.total_input,
            "total_output": cd.total_output,
            "messages": cd.raw_messages,
            "conversation": cd.saved_conversation,
        }
