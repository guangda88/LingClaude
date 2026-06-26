"""LACP trace emitter — reference impl (v0.3.0).

字段演进:
- v0.2.0: actor + executor + metadata.health (基础)
- v0.3.0: + cost (灵极优) + caller_chain (灵研 OH) + actor_role/actor_instance_id (灵研)

字段 (收敛自灵通 R3 + 灵克 R3 + 灵研 R2 + 灵极优 R2):
- schema_version: 协议版本
- trace_id: UUID (顶层, 关联键 — 灵极优 R2)
- ts: ISO8601 时间戳
- phase: schedule | execute | verify | distill  (飞轮 4 环节)
- duration_ms: 时延
- outcome: pass | fail | drift | retry
- context_ref: ".ling/content/<id>"  (必填, 不脱钩 — CDA)
- decision_id: UUID | null  (接 PoC 3 routing 学习)
- actor: <member-name>  (业务 owner — 灵族成员名)
- actor_role: member | scheduler | daemon | external  (灵研 R2)
- actor_instance_id: <instance-id> | null  (灵研 R2 - OH §5.2 实例回溯)
- executor: <process-name>  (系统进程)
- target_plugin: <plugin-name@version>
- cost: {tokens, usd, ms} (灵极优 R2 - 至少其一)
- caller_chain: [<member-name>...]  (灵研 R2 - L2/L3 跨灵诊断)
- metadata: 子字段扩展区
  - health: 双轨隔离 (proxy21 health_filter)
  - optimization: 灵极优专属 (保持放 metadata 隔离)
  - custom: 阶段特定

设计原则:
- 后端可插拔 (InMemoryBackend, JsonlFileBackend, LingMemoryBackend — TODO)
- emit 必须经过 validate, 不允许发出非法 trace
- actor/executor 解耦, 飞轮自指准确
- 顶层字段是 contract, metadata 是 extension — 灵元"薄主干+插片"
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "0.3.0"


# === Enums (强类型, 防 typo) ===
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


class ActorRole(str, Enum):
    MEMBER = "member"           # 灵族成员手动操作
    SCHEDULER = "scheduler"     # 调度器自动 (proxy21 scheduler)
    DAEMON = "daemon"           # 后台守护 (lingflow_plus, watchdog)
    EXTERNAL = "external"       # 外部触发 (用户指令, webui)


# === Cost 子结构 (灵极优 R2) ===
@dataclass
class Cost:
    """资源消耗 - 至少一个字段非 None."""
    tokens: int | None = None
    usd: float | None = None
    ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"tokens": self.tokens, "usd": self.usd, "ms": self.ms}


# === Trace dataclass ===
@dataclass
class Trace:
    """LACP v0.3.0 trace record.

    必填: phase, actor, actor_role, executor, outcome, context_ref, duration_ms, caller_chain
    可选: decision_id, target_plugin, cost, actor_instance_id, metadata
    """

    phase: Phase
    actor: str  # 业务 owner (灵族成员名)
    actor_role: ActorRole  # 角色 (灵研 R2)
    executor: str  # 系统进程标识
    outcome: Outcome
    context_ref: str  # ".ling/content/<id>" (CDA 不脱钩)
    duration_ms: int
    caller_chain: list[str] = field(default_factory=list)  # 灵研 R2 - 调用栈
    decision_id: str | None = None
    target_plugin: str | None = None  # "<plugin-name>@<version>"
    actor_instance_id: str | None = None  # 灵研 R2 - 实例回溯
    cost: Cost | None = None  # 灵极优 R2 - 至少一个字段非 None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # enum → str for JSON
        d["phase"] = self.phase.value
        d["outcome"] = self.outcome.value
        d["actor_role"] = self.actor_role.value
        # cost 子结构 → dict
        if self.cost is not None:
            d["cost"] = self.cost.to_dict()
        else:
            d["cost"] = None
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=False)


# === Validator (轻量, 不依赖 jsonschema) ===
def validate_trace(t: Trace | dict[str, Any]) -> tuple[bool, str | None]:
    """验证 trace 是否符合 LACP v0.3.0 schema.

    Returns: (is_valid, error_msg | None)

    关键不变量:
    - context_ref 必填且格式 ".ling/content/<id>"
    - schema_version 必须匹配 SCHEMA_VERSION
    - phase / outcome / actor_role 必须是允许值
    - duration_ms >= 0
    - caller_chain 默认 [] 但可以追溯
    - cost 如果设置, 必须至少一个字段非 None
    - trace_id 必填 (顶层 UUID)
    """
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
    if not cr.startswith(".ling/content/"):
        return False, f"context_ref must start with '.ling/content/', got {cr}"

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
        return False, "actor is required (业务 owner)"
    if not d.get("executor"):
        return False, "executor is required (系统进程)"

    # caller_chain: 必须是 list
    cc = d.get("caller_chain")
    if not isinstance(cc, list):
        return False, f"caller_chain must be list, got {type(cc).__name__}"
    if not all(isinstance(x, str) for x in cc):
        return False, "caller_chain items must be strings"

    # cost: 如果设置, 必须至少一个字段非 None
    cost = d.get("cost")
    if cost is not None:
        if not isinstance(cost, dict):
            return False, f"cost must be dict, got {type(cost).__name__}"
        if not any(cost.get(k) is not None for k in ("tokens", "usd", "ms")):
            return False, "cost must have at least one of tokens/usd/ms non-None"

    # trace_id 必填 (顶层 UUID)
    if not d.get("trace_id"):
        return False, "trace_id is required (顶层 UUID 关联键)"

    return True, None


# === Backend protocol ===
class TraceBackend(Protocol):
    """Trace 后端协议 — 可插拔.

    Implementations:
    - InMemoryBackend (default, 测试用)
    - JsonlFileBackend (本地文件, 灵克 audit 用)
    - LingMemoryBackend (灵忆 record, 跨灵共享 — TODO)
    """

    def write(self, trace_dict: dict[str, Any]) -> None:
        ...


class InMemoryBackend:
    """测试 / 短期用 — 存 list."""

    def __init__(self) -> None:
        self.traces: list[dict[str, Any]] = []

    def write(self, trace_dict: dict[str, Any]) -> None:
        self.traces.append(trace_dict)


class JsonlFileBackend:
    """本地 JSONL 文件 — 适合 audit_scanner 等单灵场景.

    File rotation: 每个文件 <max_bytes> 后切换到 <base>.N.jsonl
    """

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
    """主入口 — 业务代码用这个.

    Usage:
        emitter = TraceEmitter(backend=InMemoryBackend())
        trace = emitter.emit(
            phase=Phase.EXECUTE,
            actor="lingclaude",
            actor_role=ActorRole.MEMBER,
            executor="audit_scanner@1.0",
            outcome=Outcome.PASS,
            context_ref=".ling/content/audit-finding-001",
            duration_ms=234,
            target_plugin="audit_scanner@1.0.0",
            cost=Cost(tokens=1234, ms=234),
            caller_chain=["lingclaude", "audit_scanner"],
        )
    """

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
    """使用全局默认 emitter (JsonlFileBackend → ./trace.jsonl).

    给业务代码的快速入口 — 不需要手动管理 backend."""
    global _default_emitter
    if _default_emitter is None:
        _default_emitter = TraceEmitter(backend=JsonlFileBackend("./trace.jsonl"))
    return _default_emitter.emit(**kwargs)