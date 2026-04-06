from __future__ import annotations

import json
import re
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from lingclaude.core.types import Result

_SENSITIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(api[_-]?key|apikey|token|secret|password|auth[_-]?header)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"sk-ant-[a-zA-Z0-9]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
)


def _redact_message(msg: str) -> str:
    for pattern in _SENSITIVE_PATTERNS:
        msg = pattern.sub("[REDACTED]", msg)
    return msg


@dataclass(frozen=True)
class Session:
    session_id: str
    messages: tuple[str, ...]
    input_tokens: int
    output_tokens: int
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    expires_at: str = field(default_factory=lambda: (datetime.now() + timedelta(hours=24)).isoformat())

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def to_dict_redacted(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "messages": tuple(_redact_message(m) for m in self.messages),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "created_at": self.created_at,
        }


class SessionManager:
    def __init__(self, save_dir: Path | None = None) -> None:
        self.save_dir = save_dir or Path(".lingclaude/sessions")

    def save(self, session: Session) -> Result[Path]:
        try:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            path = self.save_dir / f"{session.session_id}.json"
            path.write_text(json.dumps(session.to_dict_redacted(), indent=2, ensure_ascii=False))
            return Result.ok(path)
        except Exception as e:
            return Result.fail(f"Failed to save session: {e}", code="SAVE_ERROR")

    def load(self, session_id: str) -> Result[Session]:
        path = self.save_dir / f"{session_id}.json"
        if not path.exists():
            return Result.fail(f"Session not found: {session_id}", code="NOT_FOUND")
        try:
            data = json.loads(path.read_text())
            session = Session(
                session_id=data["session_id"],
                messages=tuple(data.get("messages", ())),
                input_tokens=data.get("input_tokens", 0),
                output_tokens=data.get("output_tokens", 0),
                created_at=data.get("created_at", ""),
            )
            return Result.ok(session)
        except Exception as e:
            return Result.fail(f"Failed to load session: {e}", code="LOAD_ERROR")

    def create(self, messages: tuple[str, ...] = (), input_tokens: int = 0, output_tokens: int = 0) -> Session:
        return Session(
            session_id=secrets.token_hex(16),
            messages=messages,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def list_sessions(self) -> tuple[str, ...]:
        if not self.save_dir.exists():
            return ()
        return tuple(p.stem for p in self.save_dir.glob("*.json"))

    def delete(self, session_id: str) -> Result[bool]:
        path = self.save_dir / f"{session_id}.json"
        if not path.exists():
            return Result.fail(f"Session not found: {session_id}", code="NOT_FOUND")
        try:
            path.unlink()
            return Result.ok(True)
        except Exception as e:
            return Result.fail(f"Failed to delete session: {e}", code="DELETE_ERROR")

    def cleanup_expired_sessions(self, max_age_hours: int = 24) -> Result[int]:
        """清理过期的会话"""
        if not self.save_dir.exists():
            return Result.ok(0)

        try:
            count = 0
            cutoff_time = datetime.now() - timedelta(hours=max_age_hours)

            for session_file in self.save_dir.glob("*.json"):
                try:
                    data = json.loads(session_file.read_text())
                    expires_at_str = data.get("expires_at", "")

                    if expires_at_str:
                        expires_at = datetime.fromisoformat(expires_at_str)
                        if expires_at < cutoff_time:
                            session_file.unlink()
                            count += 1
                except Exception:
                    continue

            return Result.ok(count)
        except Exception as e:
            return Result.fail(f"Failed to cleanup sessions: {e}", code="CLEANUP_ERROR")
