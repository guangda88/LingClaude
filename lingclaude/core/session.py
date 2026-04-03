from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True)
class Session:
    session_id: str
    messages: tuple[str, ...]
    input_tokens: int
    output_tokens: int
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class SessionManager:
    def __init__(self, save_dir: Path | None = None):
        self.save_dir = save_dir or Path(".lingclaude/sessions")

    def save(self, session: Session) -> Path:
        self.save_dir.mkdir(parents=True, exist_ok=True)
        path = self.save_dir / f"{session.session_id}.json"
        path.write_text(json.dumps(session.to_dict(), indent=2, ensure_ascii=False))
        return path

    def load(self, session_id: str) -> Session | None:
        path = self.save_dir / f"{session_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return Session(
            session_id=data["session_id"],
            messages=tuple(data["messages"]),
            input_tokens=data["input_tokens"],
            output_tokens=data["output_tokens"],
            created_at=data.get("created_at", ""),
        )

    def create(self, messages: tuple[str, ...] = (), input_tokens: int = 0, output_tokens: int = 0) -> Session:
        return Session(
            session_id=uuid4().hex[:16],
            messages=messages,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def list_sessions(self) -> tuple[str, ...]:
        if not self.save_dir.exists():
            return ()
        return tuple(p.stem for p in self.save_dir.glob("*.json"))

    def delete(self, session_id: str) -> bool:
        path = self.save_dir / f"{session_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False
