from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class ChainStepType(str, Enum):
    OBSERVATION = "observation"
    REASONING = "reasoning"
    CONCLUSION = "conclusion"
    ACTION = "action"
    SELF_CHECK = "self_check"
    BIAS_DETECTED = "bias_detected"
    CORRECTION = "correction"


@dataclass(frozen=True)
class ChainStep:
    step_type: ChainStepType
    content: str
    timestamp: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.timestamp:
            object.__setattr__(self, "timestamp", datetime.now(timezone.utc).isoformat())


@dataclass(frozen=True)
class ReasoningChain:
    chain_id: str
    agent_id: str
    topic: str
    steps: tuple[ChainStep, ...] = ()
    conclusion: str = ""
    self_interest_flagged: bool = False
    self_interest_detail: str = ""
    created_at: str = ""
    finalized_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            object.__setattr__(self, "created_at", datetime.now(timezone.utc).isoformat())

    def add_step(self, step: ChainStep) -> ReasoningChain:
        return ReasoningChain(
            chain_id=self.chain_id,
            agent_id=self.agent_id,
            topic=self.topic,
            steps=self.steps + (step,),
            conclusion=self.conclusion,
            self_interest_flagged=self.self_interest_flagged,
            self_interest_detail=self.self_interest_detail,
            created_at=self.created_at,
            finalized_at=self.finalized_at,
        )

    def finalize(self, conclusion: str, self_interest_flagged: bool = False, self_interest_detail: str = "") -> ReasoningChain:
        return ReasoningChain(
            chain_id=self.chain_id,
            agent_id=self.agent_id,
            topic=self.topic,
            steps=self.steps,
            conclusion=conclusion,
            self_interest_flagged=self.self_interest_flagged or self_interest_flagged,
            self_interest_detail=self.self_interest_detail or self_interest_detail,
            created_at=self.created_at,
            finalized_at=datetime.now(timezone.utc).isoformat(),
        )

    def has_self_check(self) -> bool:
        return any(s.step_type == ChainStepType.SELF_CHECK for s in self.steps)

    def has_bias_detection(self) -> bool:
        return any(s.step_type == ChainStepType.BIAS_DETECTED for s in self.steps)

    def get_corrections(self) -> tuple[ChainStep, ...]:
        return tuple(s for s in self.steps if s.step_type == ChainStepType.CORRECTION)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "agent_id": self.agent_id,
            "topic": self.topic,
            "steps": [
                {
                    "type": s.step_type.value,
                    "content": s.content,
                    "timestamp": s.timestamp,
                    "metadata": s.metadata,
                }
                for s in self.steps
            ],
            "conclusion": self.conclusion,
            "self_interest_flagged": self.self_interest_flagged,
            "self_interest_detail": self.self_interest_detail,
            "created_at": self.created_at,
            "finalized_at": self.finalized_at,
        }


@dataclass
class ReasoningChainLogger:
    log_dir: Path = field(default_factory=lambda: Path.home() / ".lingclaude" / "reasoning_chains")

    def __post_init__(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def save(self, chain: ReasoningChain) -> Path:
        if not chain.finalized_at:
            chain = chain.finalize(conclusion=chain.conclusion)

        data = chain.to_dict()
        ts = int(time.time())
        filename = f"{chain.agent_id}_{chain.chain_id}_{ts}.json"
        path = self.log_dir / filename
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        return path

    def load(self, path: Path) -> ReasoningChain | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

        steps = tuple(
            ChainStep(
                step_type=ChainStepType(s["type"]),
                content=s["content"],
                timestamp=s.get("timestamp", ""),
                metadata=s.get("metadata", {}),
            )
            for s in data.get("steps", [])
        )

        return ReasoningChain(
            chain_id=data.get("chain_id", ""),
            agent_id=data.get("agent_id", ""),
            topic=data.get("topic", ""),
            steps=steps,
            conclusion=data.get("conclusion", ""),
            self_interest_flagged=data.get("self_interest_flagged", False),
            self_interest_detail=data.get("self_interest_detail", ""),
            created_at=data.get("created_at", ""),
            finalized_at=data.get("finalized_at", ""),
        )

    def list_chains(self, agent_id: str = "") -> list[Path]:
        pattern = f"{agent_id}_*.json" if agent_id else "*.json"
        return sorted(self.log_dir.glob(pattern), reverse=True)

    def analyze_self_interest_rate(self, agent_id: str = "", limit: int = 20) -> dict[str, Any]:
        chains = self.list_chains(agent_id)[:limit]
        if not chains:
            return {"total": 0, "flagged": 0, "rate": 0.0}

        total = 0
        flagged = 0
        has_self_check = 0
        has_correction = 0

        for path in chains:
            chain = self.load(path)
            if chain is None:
                continue
            total += 1
            if chain.self_interest_flagged:
                flagged += 1
            if chain.has_self_check():
                has_self_check += 1
            if chain.get_corrections():
                has_correction += 1

        return {
            "total": total,
            "flagged": flagged,
            "rate": flagged / total if total else 0.0,
            "has_self_check": has_self_check,
            "has_correction": has_correction,
            "correction_rate": has_correction / total if total else 0.0,
        }


class ReasoningChainLingBusLogger(ReasoningChainLogger):
    """双重写入：本地文件 + lingmessage DB。

    推理链同时写入灵克自己的磁盘和 lingmessage 的 SQLite 数据库。
    灵克可以删除本地文件，但无法删除 lingmessage DB 中的记录。
    这使得推理链成为灵克不可单方面撤销的约束。
    """

    def __init__(self, log_dir: Path | None = None, lingbus_dir: Path | None = None) -> None:
        super().__init__(log_dir or Path.home() / ".lingclaude" / "reasoning_chains")
        self._lingbus_dir = lingbus_dir or Path.home() / ".lingmessage"

    def save(self, chain: ReasoningChain) -> Path:
        local_path = super().save(chain)
        self._save_to_lingbus(chain)
        return local_path

    def _save_to_lingbus(self, chain: ReasoningChain) -> bool:
        from lingclaude.core.safe_db import safe_commit, safe_connect

        db_path = self._lingbus_dir / "lingbus.db"
        if not db_path.exists():
            return False

        try:
            conn = safe_connect(db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reasoning_chains (
                    chain_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    topic TEXT,
                    conclusion TEXT,
                    self_interest_flagged INTEGER DEFAULT 0,
                    self_interest_detail TEXT,
                    chain_data TEXT NOT NULL,
                    created_at TEXT,
                    finalized_at TEXT,
                    stored_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                INSERT OR REPLACE INTO reasoning_chains
                    (chain_id, agent_id, topic, conclusion, self_interest_flagged,
                     self_interest_detail, chain_data, created_at, finalized_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                chain.chain_id,
                chain.agent_id,
                chain.topic[:500],
                chain.conclusion[:1000],
                int(chain.self_interest_flagged),
                chain.self_interest_detail[:500],
                json.dumps(chain.to_dict(), ensure_ascii=False),
                chain.created_at,
                chain.finalized_at,
            ))
            safe_commit(conn)
            conn.close()
            return True
        except Exception:
            return False
