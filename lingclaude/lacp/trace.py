"""LACP trace emitter — reference impl.

字段 (收敛自灵通 R3 + 灵克 R3):
- schema_version: 协议版本
- ts: ISO8601 时间戳
- phase: schedule | execute | verify | distill  (飞轮 4 环节)
- duration_ms: 时延
- outcome: pass | fail | drift | retry
- context_ref: ".ling/content/<id>"  (必填, 不脱钩)
- decision_id: UUID | null  (接 PoC 3 routing 学习)
- actor: <member-name>  (责任主体 — 业务 owner)
- executor: <process-name>  (执行主体 — 系统进程, 解耦)
- target_plugin: <plugin-name@version>  (此次 trace 目标插片)
- metadata: 子字段扩展区
  - health: 双轨隔离 (proxy21 health_filter)
  - custom: 阶段特定

设计原则:
- 后端可插拔 (InMemoryBackend, JsonlFileBackend, LingMemoryBackend — TODO)
- emit 必须经过 validate, 不允许发出非法 trace
- actor/executor 解耦, 飞轮自指准确
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

SCHEMA_VERSION = "0.2.0"


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


# === Trace dataclass ===
@dataclass
class Trace:
    """LACP v0.2.0 trace record.

    All fields except decision_id, target_plugin, metadata are required.
    context_ref MUST be set (per CDA — Canonical Data Authority 不脱钩).
    """

    phase: Phase
    actor: str  # 责任主体 (业务 owner)
    executor: str  # 执行主体 (系统进程)
    outcome: Outcome
    context_ref: str  # ".ling/content/<id>"
    duration_ms: int
    decision_id: str | None = None
    target_plugin: str | None = None  # "<plugin-name>@<version>"
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # enum → str for JSON
        d["phase"] = self.phase.value
        d["outcome"] = self.outcome.value
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=False)


# === Validator (轻量, 不依赖 jsonschema) ===
def validate_trace(t: Trace | dict[str, Any]) -> tuple[bool, str | None]:
    """验证 trace 是否符合 LACP v0.2.0 schema.

    Returns: (is_valid, error_msg | None)

    关键不变量:
    - context_ref 必填且格式 ".ling/content/<id>"
    - schema_version 必须匹配 SCHEMA_VERSION
    - phase / outcome 必须是允许值
    - duration_ms >= 0
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

    # phase / outcome enum
    try:
        Phase(d.get("phase"))
    except (ValueError, KeyError):
        return False, f"phase must be one of {[p.value for p in Phase]}, got {d.get('phase')}"

    try:
        Outcome(d.get("outcome"))
    except (ValueError, KeyError):
        return False, f"outcome must be one of {[o.value for o in Outcome]}, got {d.get('outcome')}"

    # duration_ms
    dur = d.get("duration_ms")
    if not isinstance(dur, int) or dur < 0:
        return False, f"duration_ms must be non-negative int, got {dur}"

    # actor/executor 必填
    if not d.get("actor"):
        return False, "actor is required (责任主体)"
    if not d.get("executor"):
        return False, "executor is required (执行主体)"

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
            executor="audit_scanner@1.0",
            outcome=Outcome.PASS,
            context_ref=".ling/content/audit-finding-001",
            duration_ms=234,
            target_plugin="audit_scanner@1.0.0",
            metadata={"custom": {"rule_id": "silent_except_prod"}},
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
        executor: str,
        outcome: Outcome,
        context_ref: str,
        duration_ms: int,
        decision_id: str | None = None,
        target_plugin: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Trace:
        trace = Trace(
            phase=phase,
            actor=actor,
            executor=executor,
            outcome=outcome,
            context_ref=context_ref,
            duration_ms=duration_ms,
            decision_id=decision_id,
            target_plugin=target_plugin,
            metadata=metadata or {},
        )

        ok, err = validate_trace(trace)
        if not ok:
            logger.error("trace validation failed: %s | trace=%s", err, trace.to_dict())
            raise ValueError(f"trace validation failed: {err}")

        self.backend.write(trace.to_dict())
        self._count += 1
        logger.debug("trace emitted: phase=%s outcome=%s actor=%s", phase.value, outcome.value, actor)
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