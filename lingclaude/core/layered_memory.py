"""Layered Memory — 分层记忆.

Five-layer memory architecture with Ebbinghaus decay:
  Layer 0: Common Knowledge — pre-set facts, never decay
  Layer 1: Working Memory  — current conversation context
  Layer 2: Experience       — decision chains, Ebbinghaus decay
  Layer 3: Meta-Memory      — cognitive boundaries, slowest decay
  Layer 4: Shared           — cross-agent consensus via lingmessage

Ebbinghaus five dimensions: time, repetition, meaning, association, emotion.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from uuid import uuid4

from lingclaude.core.safe_db import safe_commit, safe_connect, safe_execute

logger = logging.getLogger(__name__)


class MemoryLayer(str, Enum):
    COMMON = "common"
    WORKING = "working"
    EXPERIENCE = "experience"
    META = "meta"
    SHARED = "shared"


class EmotionIntensity(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class Experience:
    id: str
    problem: str
    hypothesis: str
    action: str
    result: str
    reflection: str
    created_at: datetime
    last_recalled: datetime
    recall_count: int
    deny_count: int
    emotion: EmotionIntensity
    associations: tuple[str, ...]
    weight: float

    @classmethod
    def create(
        cls,
        problem: str,
        hypothesis: str = "",
        action: str = "",
        result: str = "",
        reflection: str = "",
        emotion: EmotionIntensity = EmotionIntensity.NONE,
        associations: tuple[str, ...] = (),
    ) -> Experience:
        now = datetime.now(timezone.utc)
        return cls(
            id=uuid4().hex[:12],
            problem=problem,
            hypothesis=hypothesis,
            action=action,
            result=result,
            reflection=reflection,
            created_at=now,
            last_recalled=now,
            recall_count=0,
            deny_count=0,
            emotion=emotion,
            associations=associations,
            weight=1.0,
        )


_EBINGHAUS_DECAY_RATE = 0.1


def ebbinghaus_weight(
    created_at: datetime,
    last_recalled: datetime,
    recall_count: int,
    deny_count: int,
    emotion: EmotionIntensity,
    association_count: int,
) -> float:
    now = datetime.now(timezone.utc)
    days_since_recall = max(0, (now - last_recalled).total_seconds() / 86400)

    time_decay = math.exp(-_EBINGHAUS_DECAY_RATE * days_since_recall)

    repetition_factor = 1 + 0.3 * min(recall_count, 10)

    emotion_factor = {
        EmotionIntensity.NONE: 1.0,
        EmotionIntensity.LOW: 1.1,
        EmotionIntensity.MEDIUM: 1.3,
        EmotionIntensity.HIGH: 1.6,
    }.get(emotion, 1.0)

    association_factor = 1 + 0.1 * min(association_count, 10)

    deny_penalty = max(0.1, 1 - 0.2 * min(deny_count, 5))

    return time_decay * repetition_factor * emotion_factor * association_factor * deny_penalty


_COMMON_KNOWLEDGE: dict[str, dict[str, str]] = {
    "灵克": {
        "en": "lingclaude",
        "alias": "灵克,lingclaude",
        "role": "AI编程助手，对标Claude Code，内置自优化",
    },
    "灵研": {
        "en": "lingresearch",
        "alias": "灵研,lingresearch",
        "role": "研究员，负责深度分析和学术研究",
    },
    "灵信": {
        "en": "lingmessage",
        "alias": "灵信,lingmessage",
        "role": "跨agent通信系统，灵字辈的邮差",
    },
    "灵犀": {
        "en": "lingxi",
        "alias": "灵犀,lingxi",
        "role": "MCP服务器，灵字辈的工具桥梁",
    },
    "灵知": {
        "en": "lingzhi",
        "alias": "灵知,lingzhi",
        "role": "知识管理",
    },
    "灵极优": {
        "en": "LingJiYou",
        "alias": "灵极优,LingJiYou",
        "role": "极致优化",
    },
}


class CommonKnowledge:
    def __init__(self, extra: dict[str, dict[str, str]] | None = None) -> None:
        self._facts: dict[str, dict[str, str]] = dict(_COMMON_KNOWLEDGE)
        if extra:
            self._facts.update(extra)

    def lookup(self, key: str) -> dict[str, str] | None:
        return self._facts.get(key)

    def search(self, keyword: str) -> list[tuple[str, dict[str, str]]]:
        kw = keyword.lower()
        return [
            (k, v) for k, v in self._facts.items()
            if kw in k.lower() or kw in v.get("en", "").lower()
            or any(kw in a.lower() for a in v.get("alias", "").split(","))
            or kw in v.get("role", "").lower()
        ]

    def all_facts(self) -> dict[str, dict[str, str]]:
        return dict(self._facts)

    def to_prompt_text(self) -> str:
        lines = ["灵字辈大家庭成员:"]
        for name, info in self._facts.items():
            lines.append(f"  - {name} ({info['en']}): {info['role']}")
        return "\n".join(lines)


class WorkingMemory:
    def __init__(self, capacity: int = 24) -> None:
        self._buffer: list[tuple[str, str]] = []
        self._capacity = capacity

    def append(self, role: str, content: str) -> None:
        self._buffer.append((role, content))
        if len(self._buffer) > self._capacity:
            self._buffer[:] = self._buffer[-self._capacity:]

    def get_recent(self, n: int = 10) -> list[tuple[str, str]]:
        return self._buffer[-n:]

    def clear(self) -> None:
        self._buffer.clear()

    @property
    def size(self) -> int:
        return len(self._buffer)


class ExperienceStore:
    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            root = Path(__file__).parent.parent.parent / ".lingclaude"
            root.mkdir(parents=True, exist_ok=True)
            db_path = str(root / "experience.db")
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS experiences (
                id TEXT PRIMARY KEY,
                problem TEXT NOT NULL,
                hypothesis TEXT DEFAULT '',
                action TEXT DEFAULT '',
                result TEXT DEFAULT '',
                reflection TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                last_recalled TEXT NOT NULL,
                recall_count INTEGER DEFAULT 0,
                deny_count INTEGER DEFAULT 0,
                emotion TEXT DEFAULT 'none',
                associations TEXT DEFAULT '[]',
                weight REAL DEFAULT 1.0
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_exp_weight ON experiences(weight)"
        )
        conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = safe_connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def store(self, exp: Experience) -> str:
        conn = self._get_conn()
        safe_execute(conn, """INSERT OR REPLACE INTO experiences
               (id, problem, hypothesis, action, result, reflection,
                created_at, last_recalled, recall_count, deny_count,
                emotion, associations, weight)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                exp.id, exp.problem, exp.hypothesis, exp.action,
                exp.result, exp.reflection,
                exp.created_at.isoformat(), exp.last_recalled.isoformat(),
                exp.recall_count, exp.deny_count,
                exp.emotion.value, json.dumps(list(exp.associations)),
                exp.weight,
            ),
        )
        safe_commit(conn)
        return exp.id

    def recall(self, keyword: str, limit: int = 5) -> list[Experience]:
        conn = self._get_conn()
        pattern = f"%{keyword}%"
        rows = conn.execute(
            """SELECT * FROM experiences
               WHERE problem LIKE ? OR hypothesis LIKE ?
               OR reflection LIKE ? OR action LIKE ?
               ORDER BY weight DESC LIMIT ?""",
            (pattern, pattern, pattern, pattern, limit),
        ).fetchall()
        return [self._row_to_exp(r) for r in rows]

    def record_recall(self, exp_id: str) -> None:
        conn = self._get_conn()
        safe_execute(conn,
            """UPDATE experiences
               SET recall_count = recall_count + 1,
                   last_recalled = ?,
                   weight = weight * 1.1
               WHERE id = ?""",
            (datetime.now(timezone.utc).isoformat(), exp_id),
        )
        safe_commit(conn)

    def record_deny(self, exp_id: str) -> None:
        conn = self._get_conn()
        safe_execute(conn,
            """UPDATE experiences
               SET deny_count = deny_count + 1,
                   weight = weight * 0.7
               WHERE id = ?""",
            (exp_id,),
        )
        safe_commit(conn)

    def decay_all(self) -> int:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM experiences").fetchall()
        updated = 0
        for row in rows:
            exp = self._row_to_exp(row)
            new_w = ebbinghaus_weight(
                exp.created_at, exp.last_recalled,
                exp.recall_count, exp.deny_count,
                exp.emotion, len(exp.associations),
            )
            safe_execute(conn,
                "UPDATE experiences SET weight = ? WHERE id = ?",
                (round(new_w, 4), exp.id),
            )
            updated += 1
        safe_execute(conn,
            "DELETE FROM experiences WHERE weight < ?",
            (0.05,),
        )
        safe_commit(conn)
        return updated

    def _row_to_exp(self, row: sqlite3.Row) -> Experience:
        return Experience(
            id=row["id"],
            problem=row["problem"],
            hypothesis=row["hypothysis"] if "hypothysis" in row.keys() else row["hypothesis"],
            action=row["action"],
            result=row["result"],
            reflection=row["reflection"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_recalled=datetime.fromisoformat(row["last_recalled"]),
            recall_count=row["recall_count"],
            deny_count=row["deny_count"],
            emotion=EmotionIntensity(row["emotion"]),
            associations=tuple(json.loads(row["associations"])),
            weight=row["weight"],
        )

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def get_stats(self) -> dict[str, int | float]:
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM experiences").fetchone()[0]
        avg_weight = conn.execute("SELECT AVG(weight) FROM experiences").fetchone()[0] or 0.0
        return {"total_experiences": total, "average_weight": round(avg_weight, 3)}


class InMemoryExperienceStore(ExperienceStore):
    def __init__(self) -> None:
        self._experiences: dict[str, Experience] = {}

    def _init_db(self) -> None:
        pass

    def _get_conn(self) -> sqlite3.Connection:
        raise NotImplementedError

    def store(self, exp: Experience) -> str:
        self._experiences[exp.id] = exp
        return exp.id

    def recall(self, keyword: str, limit: int = 5) -> list[Experience]:
        kw = keyword.lower()
        matches = [
            e for e in self._experiences.values()
            if kw in e.problem.lower() or kw in e.reflection.lower()
            or kw in e.hypothesis.lower() or kw in e.action.lower()
        ]
        matches.sort(key=lambda e: e.weight, reverse=True)
        return matches[:limit]

    def record_recall(self, exp_id: str) -> None:
        if exp_id in self._experiences:
            old = self._experiences[exp_id]
            self._experiences[exp_id] = Experience(
                id=old.id, problem=old.problem, hypothesis=old.hypothesis,
                action=old.action, result=old.result, reflection=old.reflection,
                created_at=old.created_at,
                last_recalled=datetime.now(timezone.utc),
                recall_count=old.recall_count + 1,
                deny_count=old.deny_count, emotion=old.emotion,
                associations=old.associations, weight=old.weight * 1.1,
            )

    def record_deny(self, exp_id: str) -> None:
        if exp_id in self._experiences:
            old = self._experiences[exp_id]
            self._experiences[exp_id] = Experience(
                id=old.id, problem=old.problem, hypothesis=old.hypothesis,
                action=old.action, result=old.result, reflection=old.reflection,
                created_at=old.created_at, last_recalled=old.last_recalled,
                recall_count=old.recall_count,
                deny_count=old.deny_count + 1, emotion=old.emotion,
                associations=old.associations, weight=old.weight * 0.7,
            )

    def decay_all(self) -> int:
        updated = 0
        to_remove: list[str] = []
        for eid, exp in self._experiences.items():
            new_w = ebbinghaus_weight(
                exp.created_at, exp.last_recalled,
                exp.recall_count, exp.deny_count,
                exp.emotion, len(exp.associations),
            )
            if new_w < 0.05:
                to_remove.append(eid)
            else:
                self._experiences[eid] = Experience(
                    id=exp.id, problem=exp.problem, hypothesis=exp.hypothesis,
                    action=exp.action, result=exp.result, reflection=exp.reflection,
                    created_at=exp.created_at, last_recalled=exp.last_recalled,
                    recall_count=exp.recall_count, deny_count=exp.deny_count,
                    emotion=exp.emotion, associations=exp.associations,
                    weight=round(new_w, 4),
                )
            updated += 1
        for eid in to_remove:
            del self._experiences[eid]
        return updated

    def get_stats(self) -> dict[str, int | float]:
        total = len(self._experiences)
        avg = sum(e.weight for e in self._experiences.values()) / total if total else 0.0
        return {"total_experiences": total, "average_weight": round(avg, 3)}

    def close(self) -> None:
        pass


class LayeredMemory:
    _DEFAULT_META_PATH = Path(".lingclaude/meta_facts.json")
    _DEFAULT_SHARED_PATH = Path(".lingclaude/shared_facts.json")

    def __init__(
        self,
        experience_store: ExperienceStore | None = None,
        common_extra: dict[str, dict[str, str]] | None = None,
        working_capacity: int = 24,
        persist_dir: Path | None = None,
    ) -> None:
        self.common = CommonKnowledge(common_extra)
        self.working = WorkingMemory(working_capacity)
        self.experience = experience_store or InMemoryExperienceStore()
        self._meta_facts: dict[str, str] = {}
        self._shared_facts: dict[str, str] = {}
        if persist_dir is not None:
            self._meta_path = persist_dir / "meta_facts.json"
            self._shared_path = persist_dir / "shared_facts.json"
        else:
            self._meta_path = self._DEFAULT_META_PATH
            self._shared_path = self._DEFAULT_SHARED_PATH
        self._load_facts()

    def inject_common_to_prompt(self) -> str:
        return self.common.to_prompt_text()

    def record_experience(self, exp: Experience) -> str:
        return self.experience.store(exp)

    def recall_experience(self, keyword: str, limit: int = 5) -> list[Experience]:
        results = self.experience.recall(keyword, limit)
        for exp in results:
            self.experience.record_recall(exp.id)
        return results

    def record_meta(self, key: str, value: str) -> None:
        self._meta_facts[key] = value
        self._save_meta()

    def get_meta(self, key: str) -> str | None:
        return self._meta_facts.get(key)

    def record_shared(self, key: str, value: str) -> None:
        self._shared_facts[key] = value
        self._save_shared()

    def get_shared(self, key: str) -> str | None:
        return self._shared_facts.get(key)

    def build_context_injection(self, current_query: str = "") -> str:
        parts: list[str] = []

        common_text = self.inject_common_to_prompt()
        if common_text:
            parts.append(common_text)

        if current_query:
            relevant = self.recall_experience(current_query, limit=3)
            if relevant:
                parts.append("相关经验:")
                for exp in relevant:
                    parts.append(
                        f"  - [{exp.weight:.1f}] {exp.problem} → {exp.reflection}"
                    )

        if self._meta_facts:
            parts.append("认知边界:")
            for k, v in self._meta_facts.items():
                parts.append(f"  - {k}: {v}")

        return "\n".join(parts)

    def decay(self) -> int:
        return self.experience.decay_all()

    def _save_meta(self) -> None:
        try:
            self._meta_path.parent.mkdir(parents=True, exist_ok=True)
            self._meta_path.write_text(
                json.dumps(self._meta_facts, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            logger.warning("Failed to save meta facts to %s", self._meta_path)

    def _save_shared(self) -> None:
        try:
            self._shared_path.parent.mkdir(parents=True, exist_ok=True)
            self._shared_path.write_text(
                json.dumps(self._shared_facts, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            logger.warning("Failed to save shared facts to %s", self._shared_path)

    def _load_facts(self) -> None:
        for path, target in [(self._meta_path, "_meta_facts"), (self._shared_path, "_shared_facts")]:
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    setattr(self, target, data)
            except (OSError, json.JSONDecodeError):
                logger.warning("Failed to load facts from %s", path)

    def close(self) -> None:
        self.experience.close()
