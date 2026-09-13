from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from lingclaude.core.types import Result
from lingclaude.core.topic_stack import TopicStack
from lingclaude.core.redact import redact as _redact_message

import logging

logger = logging.getLogger(__name__)


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
            # 原子写（tmp + replace）：2026-09-06 发现 7c643abc.json 被截断为
            # 0 字节——write_text 先 truncate 后写，进程恰在写入中被杀即丢档。
            import os

            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(session.to_dict_redacted(), indent=2, ensure_ascii=False))
            os.replace(tmp, path)
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

    # P0-4: snapshot / rewind
    SNAPSHOT_PREFIX = "snapshot_"

    def stop(self, session_id: str, project_path: str = "") -> Result[Session]:
        """Stop a session: mark it as expired (expires_at = now) and persist."""
        load_result = self.load(session_id, project_path)
        if load_result.is_error:
            return load_result
        session = load_result.data
        stopped_session = Session(
            session_id=session.session_id,
            messages=session.messages,
            input_tokens=session.input_tokens,
            output_tokens=session.output_tokens,
            created_at=session.created_at,
            expires_at=datetime.now().isoformat(),
            project_path=session.project_path,
            project_name=session.project_name,
        )
        save_result = self.save(stopped_session)
        if save_result.is_error:
            return Result.fail(f"Failed to stop session: {save_result.error}", code="STOP_SAVE_ERROR")
        return Result.ok(stopped_session)

    def snapshot(self, session: Session) -> Result[Path]:
        """AtomCode-style session snapshot: persist a named point-in-time copy."""
        try:
            path = self._session_path(session)
            snap_path = path.parent / f"{self.SNAPSHOT_PREFIX}{session.session_id}_{int(time.time())}.json"
            snap_path.write_text(json.dumps(session.to_dict_redacted(), indent=2, ensure_ascii=False))
            return Result.ok(snap_path)
        except Exception as e:
            return Result.fail(f"Snapshot failed: {e}", code="SNAPSHOT_ERROR")

    def rewind(self, session_id: str, snap_path: str | Path, project_path: str = "") -> Result[Session]:
        """AtomCode-style rewind: restore session from a snapshot file."""
        try:
            data = json.loads(Path(snap_path).read_text())
            # 丢弃 snap_path 之后的消息（实现：messages 截断到 snapshot 时刻）
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
            return Result.fail(f"Rewind failed: {e}", code="REWIND_ERROR")

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
            if p.stem.startswith(self.SNAPSHOT_PREFIX):
                continue
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

    def topic_stack_path(self, project_path: str = "") -> Path:
        base = Path(project_path) / ".lingclaude" if project_path else Path.home() / ".lingclaude"
        return base / "topics.json"

    def load_topic_stack(self, project_path: str = "") -> TopicStack:
        path = self.topic_stack_path(project_path)
        stack = TopicStack.load(path)
        return stack

    def prepare_handoff(self, project_path: str = "") -> str:
        stack = self.load_topic_stack(project_path)
        stale = stack.check_stale()
        closed_count = stack.force_close_all(summary="session handoff")
        parts: list[str] = []
        if stale:
            parts.append("### Stale Topics (auto-closed)")
            for t in stale:
                parts.append(f"- ~~{t.name}~~: stale ({t.age_minutes():.0f} min), auto-closed")
            parts.append("")
        if closed_count:
            parts.append(f"Auto-closed {closed_count} open topic(s) before handoff.")
            parts.append("")
        topic_text = stack.to_handover_text()
        if topic_text:
            parts.append(topic_text)
        return "\n".join(parts)
