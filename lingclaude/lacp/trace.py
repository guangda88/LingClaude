"""LACP trace emitter — reference impl (v0.4.0).

字段演进:
- v0.2.0: actor + executor + metadata.health (基础)
- v0.3.0: + cost (灵极优) + caller_chain (灵研 OH) + actor_role/actor_instance_id (灵研)
- v0.4.0: + human_context (用户反馈 - 体现人类思维模式) + intuitive/unverified outcome

v0.4.0 增量理由 (用户元层反馈 2026-06-27):
- 谭少卿"去掉人类假设"不应字面执行 — 人类思维模式是千年压缩的协议
- LACP 应桥接"人类意图 → Agent 执行", 不是假装人类不存在
- 5 维度承载:
  1. 渐进式确认 → human_context.intent + turn + confidence
  2. 直觉决策 → outcome.INTUITIVE 状态
  3. 上下文压缩 → context_ref 允许 derived_from 链 (caller_chain 已部分承载)
  4. 对话式思考 → Combo Skill 0.0.1 想法碎片阶段 (本文件外)
  5. 非确定性接受 → outcome.UNVERIFIED 状态 + reason 必填

字段全清单:
- schema_version: 协议版本
- trace_id: UUID (顶层关联键 — 灵极优 R2)
- ts: ISO8601 时间戳
- phase: schedule | execute | verify | distill  (飞轮 4 环节)
- duration_ms: 时延
- outcome: pass | fail | drift | retry | intuitive | unverified  (v0.4.0 +2)
- context_ref: ".ling/content/<id>"  (必填, 不脱钩 — CDA)
- decision_id: UUID | null
- actor: <member-name>
- actor_role: member | scheduler | daemon | external
- actor_instance_id: <instance-id> | null
- executor: <process-name>
- target_plugin: <plugin-name@version>
- cost: {tokens, usd, ms}
- caller_chain: [<member-name>...]
- metadata:
  - human_context: {intent, turn, reasoning, alternatives_considered, confidence}  # v0.4.0 NEW
  - health: ...
  - optimization: ...
  - custom: { ... }

设计原则 (沿用 + v0.4.0):
- 后端可插拔
- emit 必须经过 validate
- 顶层 = contract (薄主干), metadata = extension (插片)
- v0.4.0 NEW: human_context 是 typed structure, validator 校验 confidence ∈ [0,1]
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "0.4.0"


# === Enums ===
class Phase(str, Enum):
    SCHEDULE = "schedule"
    EXECUTE = "execute"
    VERIFY = "verify"
    DISTILL = "distill"


class Outcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    DRIFT = "drift"
    RETRY = "retry"
    INTUITIVE = "intuitive"      # v0.4.0 NEW — 直觉决策, 未完全验证
    UNVERIFIED = "unverified"    # v0.4.0 NEW — 接受非确定性


class ActorRole(str, Enum):
    MEMBER = "member"
    SCHEDULER = "scheduler"
    DAEMON = "daemon"
    EXTERNAL = "external"


# === Cost 子结构 ===
@dataclass
class Cost:
    """资源消耗 - 至少一个字段非 None."""
    tokens: int | None = None
    usd: float | None = None
    ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"tokens": self.tokens, "usd": self.usd, "ms": self.ms}


# === HumanContext 子结构 (v0.4.0 NEW) ===
@dataclass
class HumanContext:
    """人类思维上下文 - 承载渐进式确认/直觉/非确定性.

    5 个字段:
    - intent: 用户原始意图 (渐进式确认的源头)
    - turn: 对话轮次 (go on / 继续的语义压缩点)
    - reasoning: 人类推理过程 (可读 self-explanation)
    - alternatives_considered: 考虑过的方案 (避免"只看到最终选择")
    - confidence: 人类置信度 0.0-1.0 (承载"我建议但不确定")

    使用建议:
    - member 触发的 trace 强烈建议填 human_context
    - scheduler/daemon 自动 trace 可省略
    """

    intent: str
    turn: int = 0
    reasoning: str | None = None
    alternatives_considered: list[str] = field(default_factory=list)
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "turn": self.turn,
            "reasoning": self.reasoning,
            "alternatives_considered": list(self.alternatives_considered),
            "confidence": self.confidence,
        }


# === Trace dataclass ===
@dataclass
class Trace:
    """LACP v0.4.0 trace record.

    必填: phase, actor, actor_role, executor, outcome, context_ref, duration_ms, caller_chain, trace_id
    可选: decision_id, target_plugin, cost, actor_instance_id, metadata
    """

    phase: Phase
    actor: str
    actor_role: ActorRole
    executor: str
    outcome: Outcome
    context_ref: str
    duration_ms: int
    caller_chain: list[str] = field(default_factory=list)
    decision_id: str | None = None
    target_plugin: str | None = None
    actor_instance_id: str | None = None
    cost: Cost | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # enum → str
        d["phase"] = self.phase.value
        d["outcome"] = self.outcome.value
        d["actor_role"] = self.actor_role.value
        # cost 子结构 → dict
        d["cost"] = self.cost.to_dict() if self.cost is not None else None
        # human_context in metadata → 已经是 dict (caller responsibility)
        # 如果 metadata 里 human_context 是 HumanContext 实例, 转 dict
        hc = d["metadata"].get("human_context")
        if isinstance(hc, HumanContext):
            d["metadata"]["human_context"] = hc.to_dict()
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=False)


# === Validator ===
def validate_trace(t: Trace | dict[str, Any]) -> tuple[bool, str | None]:
    """验证 trace 是否符合 LACP v0.4.0 schema."""
    if isinstance(t, Trace):
        d = t.to_dict()
    else:
        d = t

    # schema_version
    if d.get("schema_version") != SCHEMA_VERSION:
        return False, f"schema_version must be {SCHEMA_VERSION}, got {d.get('schema_version')}"

    # context_ref 必填且格式
    cr = d.get("context_ref")
    if not cr:
        return False, "context_ref is required (CDA — 不脱钩)"
    if not cr.startswith(".ling/content/") and not cr.startswith(".ling/audit/"):
        return False, f"context_ref must start with '.ling/content/' or '.ling/audit/', got {cr}"

    # phase / outcome / actor_role enum
    try:
        Phase(d.get("phase"))
    except (ValueError, KeyError):
        return False, f"phase must be one of {[p.value for p in Phase]}, got {d.get('phase')}"

    try:
        Outcome(d.get("outcome"))
    except (ValueError, KeyError):
        return False, f"outcome must be one of {[o.value for o in Outcome]}, got {d.get('outcome')}"

    try:
        ActorRole(d.get("actor_role"))
    except (ValueError, KeyError):
        return False, f"actor_role must be one of {[r.value for r in ActorRole]}, got {d.get('actor_role')}"

    # duration_ms
    dur = d.get("duration_ms")
    if not isinstance(dur, int) or dur < 0:
        return False, f"duration_ms must be non-negative int, got {dur}"

    # actor/executor 必填
    if not d.get("actor"):
        return False, "actor is required"
    if not d.get("executor"):
        return False, "executor is required"

    # caller_chain
    cc = d.get("caller_chain")
    if not isinstance(cc, list):
        return False, f"caller_chain must be list, got {type(cc).__name__}"
    if not all(isinstance(x, str) for x in cc):
        return False, "caller_chain items must be strings"

    # cost
    cost = d.get("cost")
    if cost is not None:
        if not isinstance(cost, dict):
            return False, f"cost must be dict, got {type(cost).__name__}"
        if not any(cost.get(k) is not None for k in ("tokens", "usd", "ms")):
            return False, "cost must have at least one of tokens/usd/ms non-None"

    # trace_id
    if not d.get("trace_id"):
        return False, "trace_id is required"

    # v0.4.0 NEW: human_context 校验
    hc = d.get("metadata", {}).get("human_context")
    if hc is not None:
        if not isinstance(hc, dict):
            return False, f"metadata.human_context must be dict, got {type(hc).__name__}"
        # intent 必填 (渐进式确认的源头)
        if not hc.get("intent"):
            return False, "metadata.human_context.intent is required (渐进式确认的源头)"
        # confidence ∈ [0, 1]
        conf = hc.get("confidence", 1.0)
        if not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0):
            return False, f"metadata.human_context.confidence must be in [0,1], got {conf}"
        # alternatives_considered 必须是 list[str]
        alts = hc.get("alternatives_considered", [])
        if not isinstance(alts, list):
            return False, "metadata.human_context.alternatives_considered must be list"
        if not all(isinstance(x, str) for x in alts):
            return False, "metadata.human_context.alternatives_considered items must be strings"

    return True, None


# === Backend protocol ===
class TraceBackend(Protocol):
    def write(self, trace_dict: dict[str, Any]) -> None:
        ...


class InMemoryBackend:
    def __init__(self) -> None:
        self.traces: list[dict[str, Any]] = []

    def write(self, trace_dict: dict[str, Any]) -> None:
        self.traces.append(trace_dict)


class JsonlFileBackend:
    def __init__(self, path: str | Path, max_bytes: int = 10_000_000) -> None:
        self.base = Path(path)
        self.max_bytes = max_bytes
        self.base.parent.mkdir(parents=True, exist_ok=True)
        self._fp = None
        self._bytes = 0

    def _ensure_fp(self) -> None:
        if self._fp is None or self._bytes >= self.max_bytes:
            if self._fp:
                self._fp.close()
            self._fp = self.base.open("a", encoding="utf-8")
            self._bytes = self.base.stat().st_size if self.base.exists() else 0

    def write(self, trace_dict: dict[str, Any]) -> None:
        self._ensure_fp()
        line = json.dumps(trace_dict, ensure_ascii=False) + "\n"
        self._fp.write(line)
        self._fp.flush()
        self._bytes += len(line.encode("utf-8"))


# === Emitter ===
class TraceEmitter:
    def __init__(self, backend: TraceBackend) -> None:
        self.backend = backend
        self._count = 0

    def emit(
        self,
        *,
        phase: Phase,
        actor: str,
        actor_role: ActorRole,
        executor: str,
        outcome: Outcome,
        context_ref: str,
        duration_ms: int,
        caller_chain: list[str] | None = None,
        decision_id: str | None = None,
        target_plugin: str | None = None,
        actor_instance_id: str | None = None,
        cost: Cost | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Trace:
        trace = Trace(
            phase=phase,
            actor=actor,
            actor_role=actor_role,
            executor=executor,
            outcome=outcome,
            context_ref=context_ref,
            duration_ms=duration_ms,
            caller_chain=caller_chain or [],
            decision_id=decision_id,
            target_plugin=target_plugin,
            actor_instance_id=actor_instance_id,
            cost=cost,
            metadata=metadata or {},
        )

        ok, err = validate_trace(trace)
        if not ok:
            logger.error("trace validation failed: %s | trace=%s", err, trace.to_dict())
            raise ValueError(f"trace validation failed: {err}")

        self.backend.write(trace.to_dict())
        self._count += 1
        logger.debug(
            "trace emitted: phase=%s outcome=%s actor=%s role=%s",
            phase.value, outcome.value, actor, actor_role.value,
        )
        return trace

    @property
    def count(self) -> int:
        return self._count


# === 便利函数 ===
_default_emitter: TraceEmitter | None = None


def emit_trace(**kwargs) -> Trace:
    global _default_emitter
    if _default_emitter is None:
        _default_emitter = TraceEmitter(backend=JsonlFileBackend("./trace.jsonl"))
    return _default_emitter.emit(**kwargs)