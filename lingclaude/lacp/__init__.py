"""LACP (Ling Agent Context Protocol) v0.2.0-ready — reference implementation.

Thin-main + plug-in design (per 灵元1.0):
- 主干: trace schema (飞轮 4 环节共用) + plugin manifest (插片契约)
- 插片: emitter backends, validators, downstream consumers (灵极优 optimizer 等)

Submodules:
- trace: trace emitter + dataclass + validator (本文件)
- manifest: plugin manifest schema (下个 commit)
- emit_jsonl: file/jsonl backend (下个 commit)
"""

from .trace import (
    Trace,
    TraceEmitter,
    TraceBackend,
    InMemoryBackend,
    JsonlFileBackend,
    Phase,
    Outcome,
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
    "emit_trace",
    "validate_trace",
    "SCHEMA_VERSION",
]

__version__ = "0.2.0-ready"