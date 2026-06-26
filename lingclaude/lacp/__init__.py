"""LACP (Ling Agent Context Protocol) v0.5.0 — reference implementation.

v0.5.0 增量 (基于 6 仓库启示 + 灵信 R1 建议):
- Plugin manifest schema (灵族"插片身份证")
- transports 字段 (Agent-Native 借鉴: 6-channel)
- output_recipient 字段 (灵信建议 A)
- dependencies 支持 plugin/config/service (灵信建议 B)
- replaceable 默认 false 安全默认 (灵信建议 C)
- HMAC-SHA256 signature (audit 防篡改)

v0.5.0 整合:
- v0.2.0 trace schema (基础)
- v0.3.0 cost/caller_chain/actor_role/actor_instance_id (灵研/灵极优)
- v0.4.0 human_context/intuitive/unverified (人类思维承载)
- v0.5.0 plugin manifest (插片契约)
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
    SCHEMA_VERSION as TRACE_SCHEMA_VERSION,
)

from .manifest import (
    Plugin,
    Interface,
    Dependency,
    Transport,
    OutputRecipient,
    Replaceable,
    DependencyKind,
    ErrorSeverity,
    validate_manifest,
    sign_manifest,
    load_manifest,
    SCHEMA_VERSION as MANIFEST_SCHEMA_VERSION,
)

__all__ = [
    # trace (v0.2.0 - v0.4.0)
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
    "TRACE_SCHEMA_VERSION",
    "SCHEMA_VERSION",  # backward-compat alias (= TRACE_SCHEMA_VERSION)
    # manifest (v0.5.0)
    "Plugin",
    "Interface",
    "Dependency",
    "Transport",
    "OutputRecipient",
    "Replaceable",
    "DependencyKind",
    "ErrorSeverity",
    "validate_manifest",
    "sign_manifest",
    "load_manifest",
    "MANIFEST_SCHEMA_VERSION",
]

# Backward-compat alias for v0.4.0 callers
SCHEMA_VERSION = TRACE_SCHEMA_VERSION

__version__ = "0.5.0"