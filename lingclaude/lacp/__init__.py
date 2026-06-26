"""LACP (Ling Agent Context Protocol) v0.4.0 — reference implementation.

Thin-main + plug-in design (per 灵元1.0):
- 主干: trace schema (飞轮 4 环节共用) + plugin manifest (插片契约)
- 插片: emitter backends, validators, downstream consumers (灵极优 optimizer 等)

v0.4.0 增量 (来自用户元层反馈 2026-06-27 - 体现人类思维模式):
- human_context metadata 子结构: intent/turn/reasoning/alternatives/confidence
- outcome 加 INTUITIVE 和 UNVERIFIED 状态
- 设计原则修订: "去掉人类假设"不字面执行, 桥接人类意图 → Agent 执行

字段演进:
- v0.2.0: actor + executor + metadata.health (基础)
- v0.3.0: + cost + caller_chain + actor_role + actor_instance_id (灵研 + 灵极优)
- v0.4.0: + human_context + intuitive/unverified outcome (人类思维承载)
"""

from .trace import (
    Trace,
    TraceEmitter,
    TraceBackend,
    InMemoryBackend,
    JsonlFileBackend,
    Phase,
    Outcome,
    ActorRole,
    Cost,
    HumanContext,
    emit_trace,
    validate_trace,
    SCHEMA_VERSION,
)

__all__ = [
    "Trace",
    "TraceEmitter",
    "TraceBackend",
    "InMemoryBackend",
    "JsonlFileBackend",
    "Phase",
    "Outcome",
    "ActorRole",
    "Cost",
    "HumanContext",
    "emit_trace",
    "validate_trace",
    "SCHEMA_VERSION",
]

__version__ = "0.4.0"