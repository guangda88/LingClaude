"""Topic Stack — 议题栈.

Lightweight session-level topic tracker. Each topic has:
- name: what we're working on
- goal: clear completion condition
- status: open | closed
- summary: one-line conclusion (set on close)

Design principles:
- Completed topics compress to a single line, never expanded again
- Only open topics survive into handover
- Multiple parallel topics allowed
- force_close_all for session cleanup
- Minimal overhead: JSON file, no DB
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

_DEFAULT_STALE_MINUTES = 30

logger = logging.getLogger(__name__)


class TopicError(Exception):
    pass


class TopicStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass
class Topic:
    name: str
    goal: str
    status: TopicStatus = TopicStatus.OPEN
    summary: str = ""
    opened_at: str = ""
    closed_at: str = ""

    def __post_init__(self) -> None:
        if not self.opened_at:
            self.opened_at = datetime.now(timezone.utc).isoformat()

    def age_minutes(self) -> float:
        if not self.opened_at:
            return 0.0
        opened = datetime.fromisoformat(self.opened_at)
        return (datetime.now(timezone.utc) - opened).total_seconds() / 60


@dataclass
class TopicStack:
    _topics: list[Topic] = field(default_factory=list)
    _persist_path: Path | None = None
    _stale_minutes: float = _DEFAULT_STALE_MINUTES

    def push(self, name: str, goal: str) -> Topic:
        topic = Topic(name=name, goal=goal)
        self._topics.append(topic)
        self._persist()
        logger.info("Topic opened: %s — %s", name, goal)
        return topic

    def close(self, name: str, summary: str = "") -> Topic | None:
        if not summary:
            logger.warning("Closing topic '%s' without summary — please provide a conclusion", name)
        for topic in reversed(self._topics):
            if topic.name == name and topic.status == TopicStatus.OPEN:
                topic.status = TopicStatus.CLOSED
                topic.summary = summary or "(closed without summary)"
                topic.closed_at = datetime.now(timezone.utc).isoformat()
                self._persist()
                logger.info("Topic closed: %s — %s", name, summary)
                return topic
        logger.warning("No open topic named '%s' to close", name)
        return None

    def close_current(self, summary: str = "") -> Topic | None:
        current = self.current()
        if current is None:
            return None
        return self.close(current.name, summary)

    def current(self) -> Topic | None:
        for topic in reversed(self._topics):
            if topic.status == TopicStatus.OPEN:
                return topic
        return None

    def open_topics(self) -> list[Topic]:
        return [t for t in self._topics if t.status == TopicStatus.OPEN]

    def closed_topics(self) -> list[Topic]:
        return [t for t in self._topics if t.status == TopicStatus.CLOSED]

    def all_topics(self) -> list[Topic]:
        return list(self._topics)

    def to_handover_text(self) -> str:
        lines: list[str] = []

        open_topics = self.open_topics()
        if open_topics:
            lines.append("## Open Topics")
            for t in open_topics:
                lines.append(f"- **{t.name}**: {t.goal}")
                lines.append(f"  opened: {t.opened_at[:16]}")

        closed_topics = self.closed_topics()
        if closed_topics:
            lines.append("")
            lines.append("## Closed Topics")
            for t in closed_topics:
                summary = t.summary or "(no summary)"
                lines.append(f"- ~~{t.name}~~: {summary}")

        return "\n".join(lines)

    def to_compact_json(self) -> str:
        return json.dumps(
            [
                {
                    "name": t.name,
                    "goal": t.goal,
                    "status": t.status.value,
                    "summary": t.summary,
                    "opened_at": t.opened_at,
                    "closed_at": t.closed_at,
                }
                for t in self._topics
            ],
            ensure_ascii=False,
            indent=2,
        )

    def _persist(self) -> None:
        if self._persist_path is None:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._persist_path.write_text(self.to_compact_json(), encoding="utf-8")
        except OSError:
            logger.warning("Failed to persist topic stack to %s", self._persist_path)

    @classmethod
    def load(cls, path: Path) -> TopicStack:
        if not path.exists():
            return cls(_persist_path=path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            topics = []
            for item in data:
                topics.append(Topic(
                    name=item["name"],
                    goal=item["goal"],
                    status=TopicStatus(item.get("status", "open")),
                    summary=item.get("summary", ""),
                    opened_at=item.get("opened_at", ""),
                    closed_at=item.get("closed_at", ""),
                ))
            return cls(_topics=topics, _persist_path=path)
        except (OSError, json.JSONDecodeError, KeyError):
            logger.warning("Failed to load topic stack from %s", path)
            return cls(_persist_path=path)

    def force_close_all(self, summary: str = "session interrupted") -> int:
        count = 0
        for t in self._topics:
            if t.status == TopicStatus.OPEN:
                t.status = TopicStatus.CLOSED
                t.summary = summary
                t.closed_at = datetime.now(timezone.utc).isoformat()
                count += 1
        if count:
            self._persist()
        return count

    def stats(self) -> dict[str, int]:
        return {
            "total": len(self._topics),
            "open": len(self.open_topics()),
            "closed": len(self.closed_topics()),
        }

    def check_stale(self) -> list[Topic]:
        stale = []
        for t in self.open_topics():
            if t.age_minutes() > self._stale_minutes:
                logger.warning(
                    "Topic '%s' open for %.0f minutes (threshold: %.0f). Consider closing or splitting.",
                    t.name, t.age_minutes(), self._stale_minutes,
                )
                stale.append(t)
        return stale
