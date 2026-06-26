"""Handover V2 — 会话状态快照.

Design principles (from .audit/handoff_sdth_调研报告_20260517.md):
1. Checkpoint model: each task = user_raw + ai_interpretation + user_confirmed
2. Source tagging: user_directed / self_generated / lingbus_originated
3. Instant write: each step updates immediately, kill-safe
4. Incomplete discussions tracked as 'pending', never silently dropped
5. Triple output: YAML (primary, for AI) + JSON (backward compat) + Markdown (for AI)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import yaml
from enum import Enum
from pathlib import Path

from lingclaude.core.types import Result

logger = logging.getLogger(__name__)

HANDOVER_VERSION = "2.0"


class TaskSource(str, Enum):
    USER = "user_directed"
    SELF = "self_generated"
    LINGBUS = "lingbus_originated"


class TaskStatus(str, Enum):
    IN_DISCUSSION = "in_discussion"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


@dataclass
class Checkpoint:
    task_id: str
    description: str
    source: TaskSource = TaskSource.USER
    status: TaskStatus = TaskStatus.IN_DISCUSSION
    user_raw: str = ""
    ai_interpretation: str = ""
    user_confirmed: bool = False
    output: str = ""
    blocker_reason: str = ""
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now


@dataclass
class InfrastructureEntry:
    service: str
    port: int
    status: str = "unknown"


@dataclass
class HandoverV2:
    member_id: str
    user_tasks: list[Checkpoint] = field(default_factory=list)
    pending_discussions: list[Checkpoint] = field(default_factory=list)
    recently_completed: list[Checkpoint] = field(default_factory=list)
    blockers: list[Checkpoint] = field(default_factory=list)
    infrastructure: list[InfrastructureEntry] = field(default_factory=list)
    notes: str = ""
    version: str = HANDOVER_VERSION
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def add_task(self, task: Checkpoint) -> None:
        self.user_tasks.append(task)
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def add_discussion(self, topic: str, context: str = "") -> None:
        cp = Checkpoint(
            task_id=f"disc-{len(self.pending_discussions) + 1}",
            description=topic,
            source=TaskSource.USER,
            status=TaskStatus.IN_DISCUSSION,
            user_raw=topic,
            ai_interpretation=context,
        )
        self.pending_discussions.append(cp)
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def complete_task(self, task_id: str, output: str = "") -> bool:
        for i, t in enumerate(self.user_tasks):
            if t.task_id == task_id:
                t.status = TaskStatus.COMPLETED
                t.output = output
                t.updated_at = datetime.now(timezone.utc).isoformat()
                self.recently_completed.append(t)
                self.user_tasks.pop(i)
                self.timestamp = datetime.now(timezone.utc).isoformat()
                return True
        return False

    def block_task(self, task_id: str, reason: str = "") -> bool:
        for i, t in enumerate(self.user_tasks):
            if t.task_id == task_id:
                t.status = TaskStatus.BLOCKED
                t.blocker_reason = reason
                t.updated_at = datetime.now(timezone.utc).isoformat()
                self.blockers.append(t)
                self.user_tasks.pop(i)
                self.timestamp = datetime.now(timezone.utc).isoformat()
                return True
        return False

    def to_json(self) -> str:
        return json.dumps(self._to_dict(), indent=2, ensure_ascii=False)

    def to_yaml(self) -> str:
        return yaml.dump(self._to_dict(), allow_unicode=True, default_flow_style=False, sort_keys=False)

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append(f"# {self.member_id} Handover")
        lines.append("")
        lines.append(f"**version**: {self.version}  ")
        lines.append(f"**timestamp**: {self.timestamp[:16]}  ")
        lines.append("")

        if self.user_tasks:
            lines.append("## 进行中的用户任务")
            lines.append("")
            for t in self.user_tasks:
                confirmed = "Y" if t.user_confirmed else "N"
                lines.append(f"- **{t.task_id}** [{t.status.value}] (confirmed={confirmed})")
                lines.append(f"  - user_raw: {t.user_raw}")
                lines.append(f"  - ai_interpretation: {t.ai_interpretation}")
                if t.output:
                    lines.append(f"  - output: {t.output}")
            lines.append("")

        if self.pending_discussions:
            lines.append("## 待继续")
            lines.append("")
            for t in self.pending_discussions:
                lines.append(f"- **{t.description}** [{t.status.value}]")
                if t.ai_interpretation:
                    lines.append(f"  - context: {t.ai_interpretation}")
            lines.append("")

        if self.recently_completed:
            lines.append("## 已完成")
            lines.append("")
            lines.append("| task | output |")
            lines.append("|------|--------|")
            for t in self.recently_completed:
                lines.append(f"| {t.task_id} | {t.output or t.description} |")
            lines.append("")

        if self.blockers:
            lines.append("## 阻塞项")
            lines.append("")
            for t in self.blockers:
                lines.append(f"- **{t.task_id}**: {t.blocker_reason or t.description}")
            lines.append("")

        if self.infrastructure:
            lines.append("## 基础设施")
            lines.append("")
            lines.append("| service | port | status |")
            lines.append("|---------|------|--------|")
            for s in self.infrastructure:
                lines.append(f"| {s.service} | {s.port} | {s.status} |")
            lines.append("")

        if self.notes:
            lines.append("## 备注")
            lines.append("")
            lines.append(self.notes)
            lines.append("")

        return "\n".join(lines)

    def _to_dict(self) -> dict:
        d = {
            "version": self.version,
            "member_id": self.member_id,
            "timestamp": self.timestamp,
            "user_tasks": [_cp_to_dict(t) for t in self.user_tasks],
            "pending_discussions": [_cp_to_dict(t) for t in self.pending_discussions],
            "recently_completed": [_cp_to_dict(t) for t in self.recently_completed],
            "blockers": [_cp_to_dict(t) for t in self.blockers],
            "infrastructure": [{"service": s.service, "port": s.port, "status": s.status} for s in self.infrastructure],
            "notes": self.notes,
        }
        return d

    @classmethod
    def from_dict(cls, data: dict) -> HandoverV2:
        def _parse_cp(d: dict) -> Checkpoint:
            return Checkpoint(
                task_id=d.get("task_id", ""),
                description=d.get("description", ""),
                source=TaskSource(d.get("source", "user_directed")),
                status=TaskStatus(d.get("status", "in_discussion")),
                user_raw=d.get("user_raw", ""),
                ai_interpretation=d.get("ai_interpretation", ""),
                user_confirmed=d.get("user_confirmed", False),
                output=d.get("output", ""),
                blocker_reason=d.get("blocker_reason", ""),
                created_at=d.get("created_at", ""),
                updated_at=d.get("updated_at", ""),
            )

        infra = []
        for s in data.get("infrastructure", []):
            infra.append(InfrastructureEntry(
                service=s.get("service", ""),
                port=s.get("port", 0),
                status=s.get("status", "unknown"),
            ))

        return cls(
            version=data.get("version", HANDOVER_VERSION),
            member_id=data.get("member_id", ""),
            timestamp=data.get("timestamp", ""),
            user_tasks=[_parse_cp(t) for t in data.get("user_tasks", [])],
            pending_discussions=[_parse_cp(t) for t in data.get("pending_discussions", [])],
            recently_completed=[_parse_cp(t) for t in data.get("recently_completed", [])],
            blockers=[_parse_cp(t) for t in data.get("blockers", [])],
            infrastructure=infra,
            notes=data.get("notes", ""),
        )


def _cp_to_dict(cp: Checkpoint) -> dict:
    return {
        "task_id": cp.task_id,
        "description": cp.description,
        "source": cp.source.value,
        "status": cp.status.value,
        "user_raw": cp.user_raw,
        "ai_interpretation": cp.ai_interpretation,
        "user_confirmed": cp.user_confirmed,
        "output": cp.output,
        "blocker_reason": cp.blocker_reason,
        "created_at": cp.created_at,
        "updated_at": cp.updated_at,
    }


class HandoverWriter:
    def __init__(self, handover_dir: Path, member_id: str) -> None:
        self.handover_dir = handover_dir
        self.member_id = member_id
        self.json_path = handover_dir / "handover.json"
        self.yaml_path = handover_dir / "handover.yaml"
        self.md_path = handover_dir / "handover.md"
        self._handover: HandoverV2 | None = None

    def load_or_create(self) -> HandoverV2:
        # Prefer yaml over json (全族标准迁移)
        for path, loader in [(self.yaml_path, self._load_yaml), (self.json_path, self._load_json)]:
            if path.exists():
                try:
                    data = loader(path)
                    self._handover = HandoverV2.from_dict(data)
                    return self._handover
                except (ValueError, KeyError, yaml.YAMLError) as e:
                    logger.warning(f"Failed to parse {path}: {e}")
        self._handover = HandoverV2(member_id=self.member_id)
        return self._handover

    @staticmethod
    def _load_json(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _load_yaml(path: Path) -> dict:
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    @property
    def handover(self) -> HandoverV2:
        if self._handover is None:
            return self.load_or_create()
        return self._handover

    def write(self) -> Result[Path]:
        if self._handover is None:
            return Result.fail("No handover data to write", code="NO_DATA")
        try:
            self.handover_dir.mkdir(parents=True, exist_ok=True)
            # Write yaml (primary) and json (backward compat)
            self.yaml_path.write_text(self._handover.to_yaml(), encoding="utf-8")
            self.json_path.write_text(self._handover.to_json(), encoding="utf-8")
            self.md_path.write_text(self._handover.to_markdown(), encoding="utf-8")
            return Result.ok(self.yaml_path)
        except OSError as e:
            return Result.fail(f"Failed to write handover: {e}", code="WRITE_ERROR")

    def add_task(self, task_id: str, description: str, user_raw: str = "",
                ai_interpretation: str = "", source: TaskSource = TaskSource.USER) -> Checkpoint:
        cp = Checkpoint(
            task_id=task_id,
            description=description,
            source=source,
            status=TaskStatus.IN_PROGRESS,
            user_raw=user_raw,
            ai_interpretation=ai_interpretation,
            user_confirmed=False,
        )
        self.handover.add_task(cp)
        self.write()
        return cp

    def confirm_task(self, task_id: str) -> bool:
        for t in self.handover.user_tasks:
            if t.task_id == task_id:
                t.user_confirmed = True
                t.updated_at = datetime.now(timezone.utc).isoformat()
                self.write()
                return True
        return False

    def complete_task(self, task_id: str, output: str = "") -> bool:
        result = self.handover.complete_task(task_id, output)
        if result:
            self.write()
        return result

    def block_task(self, task_id: str, reason: str = "") -> bool:
        result = self.handover.block_task(task_id, reason)
        if result:
            self.write()
        return result

    def add_discussion(self, topic: str, context: str = "") -> None:
        self.handover.add_discussion(topic, context)
        self.write()


class HandoverReader:
    def __init__(self, handover_dir: Path) -> None:
        self.handover_dir = handover_dir
        self.json_path = handover_dir / "handover.json"

    def read(self) -> Result[HandoverV2]:
        if not self.json_path.exists():
            return Result.fail("No handover.json found", code="NOT_FOUND")
        try:
            data = json.loads(self.json_path.read_text(encoding="utf-8"))
            return Result.ok(HandoverV2.from_dict(data))
        except (json.JSONDecodeError, KeyError) as e:
            return Result.fail(f"Failed to parse handover.json: {e}", code="PARSE_ERROR")

    def has_pending_tasks(self) -> bool:
        result = self.read()
        if result.is_error:
            return False
        h = result.data
        return bool(h.user_tasks) or bool(h.pending_discussions)

    def user_tasks_to_resume(self) -> list[Checkpoint]:
        result = self.read()
        if result.is_error:
            return []
        h = result.data
        return [t for t in h.user_tasks if t.source == TaskSource.USER]

    def incomplete_discussions(self) -> list[Checkpoint]:
        result = self.read()
        if result.is_error:
            return []
        h = result.data
        return [t for t in h.pending_discussions if t.status == TaskStatus.IN_DISCUSSION]
