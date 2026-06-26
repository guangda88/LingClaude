"""LACP (Ling Agent Context Protocol) v0.3.0 — reference implementation.

Thin-main + plug-in design (per 灵元1.0):
- 主干: trace schema (飞轮 4 环节共用) + plugin manifest (插片契约)
- 插片: emitter backends, validators, downstream consumers (灵极优 optimizer 等)

Submodules:
- trace: trace emitter + dataclass + validator (本文件)
- manifest: plugin manifest schema (下个 commit)
- emit_jsonl: file/jsonl backend (下个 commit)

v0.3.0 字段增量 (来自灵研 R2 + 灵极优 R2):
- + cost (灵极优): 资源消耗 tokens/usd/ms
- + caller_chain (灵研): 跨灵诊断栈
- + actor_role (灵研): member/scheduler/daemon/external
- + actor_instance_id (灵研): 实例回溯 (OH §5.2)
- + trace_id 顶层 (灵极优): 关联键
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
    "emit_trace",
    "validate_trace",
    "SCHEMA_VERSION",
]

__version__ = "0.3.0"