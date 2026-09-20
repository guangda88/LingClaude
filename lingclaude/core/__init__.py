from __future__ import annotations

from lingclaude.core.types import Result
from lingclaude.core.config import lingclaudeConfig, load_config
from lingclaude.core.models import (
    UsageSummary,
)
from lingclaude.core.session import Session, SessionManager
from lingclaude.core.permissions import PermissionContext, READ_ONLY_TOOLS
from lingclaude.core.query_engine import QueryEngine, QueryEngineConfig, TurnResult, StopReason, CHECKPOINT_DIR
from lingclaude.core.intel import (
    IntelCategory,
    IntelPriority,
    IntelItem,
    IntelCollector,
    DailyDigest,
    DailyDigestGenerator,
    IntelRelay,
)
from lingclaude.core.prior_verifier import PriorVerifier, AssertionLevel, Assertion, VerificationResult
# 2026-09-16 启动提速：fact_checker 链（含 fastapi/retrieval ≈600ms）从启动
# 路径移除——其唯一消费点 l5_audit.py:194 本就是 lazy import；__init__ 急切
# 导出改为模块级 __getattr__ 惰性转发，`from lingclaude.core import X` 语义不变。
from lingclaude.core.meta_cognition import MetaCognition, Domain, ConfidenceLevel, CognitiveBoundary, MetaCognitiveSnapshot
from lingclaude.core.layered_memory import (
    LayeredMemory, Experience, EmotionIntensity, MemoryLayer,
    CommonKnowledge, WorkingMemory, ExperienceStore, InMemoryExperienceStore, ebbinghaus_weight,
)

from lingclaude.core.topic_stack import (
    TopicStack, Topic, TopicStatus, TopicError,
)
from lingclaude.core.topic_drift_detector import TopicDriftStatus

# 2026-09-20 P3-7 承接搬移：handover 出 core 入 lingmemory（注册表 checkpoint 类型，
# lingmemory/type_registry.yaml:1516「P3-7 会话检查点（承接 core/handover.py）」）。
# 语义兼容走文件尾 __getattr__ 惰性转发（同 fact_checker 先例），
# `from lingclaude.core import HandoverWriter` 等旧引用不变。

from lingclaude.core.lm_quick import lm_done, lm_block, lm_status

from lingclaude.core.governance import GovernanceGate, GovernanceCheckResult
from lingclaude.core.safe_db import serialized_write
from lingclaude.core.reasoning_chain import (
    ChainStep, ChainStepType, ReasoningChain, ReasoningChainLogger, ReasoningChainLingBusLogger,
)
from lingclaude.core.governance_integration import pre_submit_governance
from lingclaude.core.context_compression import (
    CompressionLevel, CompressionConfig, CompressionResult,
    extract_facts_from_messages, generate_chinese_summary, compress_messages,
    extract_reasoning_from_messages, generate_reasoning_summary,
)
from lingclaude.core.dementia_detector import (
    CognitiveState, DementiaDetector, DementiaDiagnosis, ToolCallFingerprint,
)
from lingclaude.core.behavior_check import (
    check as behavior_check, BehaviorCheckResult,
)
from lingclaude.core.hooks import (
    HookType, HookContext, HookManager, HookResult,
)
from lingclaude.core.cognitive_rhythm import (
    CognitiveRhythm, RhythmPhase, ImbalanceType, RhythmSnapshot,
)
from lingclaude.core.comfort_zone import (
    ComfortZoneDetector, ComfortCheckResult, ConclusionRisk,
)
from lingclaude.core.llm_probe import (
    health_check, probe_llm_completion, probe_port,
    PROBE_TIMEOUT, PROXY_API_KEY, PROXY_URL,
)
from lingclaude.coordination import (
    BusResponder,
    ResponseStats,
    create_responder,
)

from lingclaude.core.state_store import (
    StateStore, StateBackend, JsonFileBackend, LingYiBackend,
)

from lingclaude.core.session_projection import (
    TokenProjection,
    ToolStat,
    ToolProjection,
    RoundProjection,
    project_tokens,
    project_tools,
    project_rounds,
    project_session,
    aggregate_sessions,
)

__all__ = [
    "Result",
    "lingclaudeConfig",
    "load_config",
    "UsageSummary",
    "Session",
    "SessionManager",
    "PermissionContext",
    "READ_ONLY_TOOLS",
    "QueryEngine",
    "QueryEngineConfig",
    "TurnResult",
    "StopReason",
    "IntelCategory",
    "IntelPriority",
    "IntelItem",
    "IntelCollector",
    "DailyDigest",
    "DailyDigestGenerator",
    "IntelRelay",
    "PriorVerifier",
    "AssertionLevel",
    "Assertion",
    "VerificationResult",
    "ClaimExtractor",
    "KGFactChecker",
    "FactCheckResult",
    "audit_response",
    "Claim",
    "get_db_pool",
    "close_db_pool",
    "reset_db_pool",
    "MetaCognition",
    "Domain",
    "ConfidenceLevel",
    "CognitiveBoundary",
    "MetaCognitiveSnapshot",
    "LayeredMemory",
    "Experience",
    "EmotionIntensity",
    "MemoryLayer",
    "CommonKnowledge",
    "WorkingMemory",
    "ExperienceStore",
    "InMemoryExperienceStore",
    "ebbinghaus_weight",
    "GovernanceGate",
    "GovernanceCheckResult",
    "ChainStep",
    "ChainStepType",
    "ReasoningChain",
    "ReasoningChainLogger",
    "ReasoningChainLingBusLogger",
    "pre_submit_governance",
    "CompressionLevel",
    "CompressionConfig",
    "CompressionResult",
    "extract_facts_from_messages",
    "generate_chinese_summary",
    "compress_messages",
    "CognitiveState",
    "DementiaDetector",
    "behavior_check",
    "BehaviorCheckResult",
    "DementiaDiagnosis",
    "ToolCallFingerprint",
    "HookType",
    "HookContext",
    "HookManager",
    "HookResult",
    "CognitiveRhythm",
    "RhythmPhase",
    "ImbalanceType",
    "RhythmSnapshot",
    "ComfortZoneDetector",
    "ComfortCheckResult",
    "ConclusionRisk",
    "TopicStack",
    "Topic",
    "TopicStatus",
    "TopicError",
    "HANDOVER_VERSION",
    "TaskSource",
    "HandoverTaskStatus",
    "Checkpoint",
    "InfrastructureEntry",
    "HandoverV2",
    "HandoverWriter",
    "HandoverReader",
    "BehaviorMetrics",
    "ContextCache",
    "TokenMonitor",
    "DataFlywheel",
    "StateStore",
    "StateBackend",
    "JsonFileBackend",
    "LingYiBackend",
    "BusResponder",
    "ResponseStats",
    "create_responder",
    "CHECKPOINT_DIR",
    "serialized_write",
    "TopicDriftStatus",
    "health_check",
    "probe_llm_completion",
    "probe_port",
    "PROBE_TIMEOUT",
    "PROXY_API_KEY",
    "PROXY_URL",
    "TokenProjection",
    "ToolStat",
    "ToolProjection",
    "RoundProjection",
    "project_tokens",
    "project_tools",
    "project_rounds",
    "project_session",
    "aggregate_sessions",
]

# 2026-09-16 fact_checker 惰性转发：保持 `from lingclaude.core import X` 语义
def __getattr__(name: str):
    if name in ("ClaimExtractor", "KGFactChecker", "FactCheckResult", "audit_response", "Claim",
                "get_db_pool", "close_db_pool", "reset_db_pool"):
        import importlib
        _fc = importlib.import_module("lingclaude.core.fact_checker")
        return getattr(_fc, name)
    # 2026-09-20 P3-7 承接搬移：handover 符号惰性转发至 lingmemory.handover
    if name in ("HANDOVER_VERSION", "TaskSource", "Checkpoint", "InfrastructureEntry",
                "HandoverV2", "HandoverWriter", "HandoverReader", "HandoverTaskStatus"):
        import importlib
        _ho = importlib.import_module("lingmemory.handover")
        return getattr(_ho, "TaskStatus") if name == "HandoverTaskStatus" else getattr(_ho, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
