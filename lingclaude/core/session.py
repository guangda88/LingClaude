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
    project_path: str = ""
    project_name: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def to_dict_redacted(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "messages": tuple(_redact_message(m) for m in self.messages),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "created_at": self.created_at,
            "project_path": self.project_path,
            "project_name": self.project_name,
        }


def _project_dir_name(project_path: str) -> str:
    if not project_path:
        return "_default"
    p = Path(project_path).resolve()
    return p.name


def _global_sessions_root() -> Path:
    root = Path.home() / ".lingclaude" / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


class SessionManager:
    def __init__(self, save_dir: Path | None = None) -> None:
        if save_dir is not None:
            self.save_dir = save_dir
            self._global_mode = False
        else:
            self.save_dir = _global_sessions_root()
            self._global_mode = True

    def _session_path(self, session: Session) -> Path:
        if not self._global_mode:
            return self.save_dir / f"{session.session_id}.json"
        project = session.project_path or ""
        if project:
            dir_name = _project_dir_name(project)
        else:
            dir_name = "_default"
        project_dir = self.save_dir / dir_name
        project_dir.mkdir(parents=True, exist_ok=True)
        return project_dir / f"{session.session_id}.json"

    def save(self, session: Session) -> Result[Path]:
        try:
            path = self._session_path(session)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(session.to_dict_redacted(), indent=2, ensure_ascii=False))
            return Result.ok(path)
        except Exception as e:
            return Result.fail(f"Failed to save session: {e}", code="SAVE_ERROR")

    def load(self, session_id: str, project_path: str = "") -> Result[Session]:
        candidates: list[Path] = []
        if self._global_mode and project_path:
            dir_name = _project_dir_name(project_path)
            candidates.append(self.save_dir / dir_name / f"{session_id}.json")
        if self._global_mode:
            candidates.append(self.save_dir / "_default" / f"{session_id}.json")
            for d in self.save_dir.iterdir():
                if d.is_dir() and d.name not in ("_default",):
                    candidates.append(d / f"{session_id}.json")
        else:
            candidates.append(self.save_dir / f"{session_id}.json")

        for path in candidates:
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                    session = Session(
                        session_id=data["session_id"],
                        messages=tuple(data.get("messages", ())),
                        input_tokens=data.get("input_tokens", 0),
                        output_tokens=data.get("output_tokens", 0),
                        created_at=data.get("created_at", ""),
                        project_path=data.get("project_path", ""),
                        project_name=data.get("project_name", ""),
                    )
                    return Result.ok(session)
                except Exception as e:
                    return Result.fail(f"Failed to load session: {e}", code="LOAD_ERROR")
        return Result.fail(f"Session not found: {session_id}", code="NOT_FOUND")

    def create(self, messages: tuple[str, ...] = (), input_tokens: int = 0, output_tokens: int = 0, project_path: str = "", project_name: str = "") -> Session:
        return Session(
            session_id=secrets.token_hex(16),
            messages=messages,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            project_path=project_path,
            project_name=project_name or _project_dir_name(project_path),
        )

    def list_sessions(self, project_path: str = "") -> tuple[dict[str, str], ...]:
        results: list[dict[str, str]] = []
        if not self.save_dir.exists():
            return ()
        if self._global_mode:
            if project_path:
                target_dir = self.save_dir / _project_dir_name(project_path)
                if target_dir.exists():
                    results.extend(self._list_sessions_in(target_dir, project_path))
            else:
                for d in sorted(self.save_dir.iterdir()):
                    if d.is_dir():
                        proj = "" if d.name == "_default" else d.name
                        results.extend(self._list_sessions_in(d, proj))
        else:
            results.extend(self._list_sessions_in(self.save_dir, ""))
        return tuple(results)

    def _list_sessions_in(self, directory: Path, project_hint: str) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for p in sorted(directory.glob("*.json")):
            try:
                data = json.loads(p.read_text())
                items.append({
                    "session_id": p.stem,
                    "project_path": data.get("project_path", project_hint),
                    "project_name": data.get("project_name", project_hint or "_default"),
                    "created_at": data.get("created_at", ""),
                })
            except Exception:
                items.append({
                    "session_id": p.stem,
                    "project_path": project_hint,
                    "project_name": project_hint or "_default",
                    "created_at": "",
                })
        return items

    def list_projects(self) -> tuple[str, ...]:
        if not self._global_mode or not self.save_dir.exists():
            return ()
        names: list[str] = []
        for d in sorted(self.save_dir.iterdir()):
            if d.is_dir() and d.name != "_default":
                names.append(d.name)
        return tuple(names)

    def delete(self, session_id: str, project_path: str = "") -> Result[bool]:
        candidates: list[Path] = []
        if self._global_mode and project_path:
            candidates.append(self.save_dir / _project_dir_name(project_path) / f"{session_id}.json")
        if self._global_mode:
            candidates.append(self.save_dir / "_default" / f"{session_id}.json")
            for d in self.save_dir.iterdir():
                if d.is_dir():
                    candidates.append(d / f"{session_id}.json")
        else:
            candidates.append(self.save_dir / f"{session_id}.json")
        for path in candidates:
            if path.exists():
                try:
                    path.unlink()
                    return Result.ok(True)
                except Exception as e:
                    return Result.fail(f"Failed to delete session: {e}", code="DELETE_ERROR")
        return Result.fail(f"Session not found: {session_id}", code="NOT_FOUND")

    def cleanup_expired_sessions(self, max_age_hours: int = 24) -> Result[int]:
        if not self.save_dir.exists():
            return Result.ok(0)
        try:
            count = 0
            cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
            pattern = "**/*.json" if self._global_mode else "*.json"
            for session_file in self.save_dir.glob(pattern):
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
