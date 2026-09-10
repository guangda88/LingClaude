"""QueryEngine 装配 manifest — 灵元 1.0 P2.a 地基（V3 §四/§五）。

把 QueryEngine.__init__ 里 55 项硬接线赋值收敛为一张声明式 manifest，
由 assemble() 统一装配。协作者互不依赖、顺序无关，只引用装配上下文三样：
engine / session_manager / provider。

不变式（P2 验收口径）：
- 新增协作者 = manifest 加一行，query_engine.py 主干 diff 为 0
- 装配语义与原 __init__ 逐字对齐（phase/note 记录迁移依据）
- P2.b 已完成：QueryEngine.__init__ 装配段收敛为 assemble(WIRING_MANIFEST)，
  主干仅保留构造三件套（config/session_manager/session_id）+ T0-7 运行时
  监听接线 + _load_session_state()。回归网：tests/test_p2a_wiring_manifest.py

phase 三值语义：
- collaborator  类实例协作者（29 项）——真正的插片候选，P2.c 起逐个转 seam
- state         引擎自有的容器/标志（24 项）——永不下放插片，manifest 仅存档
- parameterized 已由构造参数注入的（2 项：_provider/_runtime）——P2.b 并入 ctx
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from lingclaude.core.lingmemory_bridge import LingMemoryCacheBridge, dualwrite_enabled  # P3.2


@dataclass(frozen=True)
class WiringContext:
    """装配上下文：协作者工厂允许引用的全部外部依赖。

    工厂签名统一为 Callable[[WiringContext], Any]。Allowed closure vars
    限 engine/session_manager/provider —— 若某工厂需要引用其他协作者，
    说明存在装配顺序依赖，必须先消除依赖再入册（保持顺序无关不变式）。

    Attributes:
        engine: 正在装配的 QueryEngine 实例（协作者普遍持有引擎引用）。
        session_manager: 会话管理器（源自 QueryEngine 构造参数）。
        provider: 模型 provider（源自 QueryEngine 构造参数）。
        runtime: 运行时（可选）；T0-7 工具管线监听接线在 __init__ 主干消费它，
            manifest 中 _runtime 条目仅存档注入。
    """

    engine: Any
    session_manager: Any
    provider: Any
    runtime: Any = None


@dataclass(frozen=True)
class WiringSpec:
    """manifest 条目：装到 engine 上的一个属性。"""

    attr: str
    factory: Callable[[WiringContext], Any]
    phase: str = "collaborator"  # collaborator | state | parameterized
    note: str = ""


def _make_session_store(ctx: WiringContext) -> Any:
    # 原位：__init__ 内 D3 注入段（模块级导入，延迟到工厂调用时）
    from lingclaude.core.session_store import SessionStore

    return SessionStore(ctx.engine.session_manager, ctx.engine.session_id)


def _make_model_adapter(ctx: WiringContext) -> Any:
    from lingclaude.core.model_adapter import ModelAdapter

    return ModelAdapter(ctx.provider)


def _make_audit_collector(ctx: WiringContext) -> Any:
    from lingclaude.core.audit_collector import AuditCollector

    return AuditCollector()


def _make_model_request_log(ctx: WiringContext) -> Any:
    from lingclaude.core.model_request_log import ModelRequestLog

    return ModelRequestLog()


def _make_tool_executor(ctx: WiringContext) -> Any:
    from lingclaude.core.tool_executor import ToolExecutor

    return ToolExecutor(ctx.engine)


def _make_tool_call_executor(ctx: WiringContext) -> Any:
    from lingclaude.core.tool_call_executor import ToolCallExecutor

    return ToolCallExecutor(ctx.engine)


def _make_session_persister(ctx: WiringContext) -> Any:
    from lingclaude.core.session_persist import SessionPersister

    return SessionPersister(ctx.engine)


def _make_session_runtime(ctx: WiringContext) -> Any:
    from lingclaude.core.session_runtime import SessionRuntime

    return SessionRuntime(ctx.engine)


def _make_intel_collector(ctx: WiringContext) -> Any:
    from lingclaude.core.intel import IntelCollector

    return IntelCollector()


def _make_behavior(ctx: WiringContext) -> Any:
    from lingclaude.core.behavior import BehaviorMetrics

    return BehaviorMetrics()


def _make_role_checker(ctx: WiringContext) -> Any:
    from lingclaude.core.role_separation import create_lingclaude_role_separation

    return create_lingclaude_role_separation()


def _make_l5_loop(ctx: WiringContext) -> Any:
    from lingclaude.core.l5_conversation_loop import L5ConversationLoop

    return L5ConversationLoop(l5_session_id=ctx.engine.session_id)


def _make_router(ctx: WiringContext) -> Any:
    from lingclaude.model.intelligent_router import IntelligentRouter

    return IntelligentRouter()


def _make_task_router(ctx: WiringContext) -> Any:
    from lingclaude.model.task_router import TaskRouter

    return TaskRouter()


def _make_tool_router(ctx: WiringContext) -> Any:
    # 原：create_default_router()，标注同步来自 engine.tool_router
    from lingclaude.engine.tool_router import create_default_router

    return create_default_router()


def _make_cache(ctx: WiringContext) -> Any:
    from lingclaude.core.context_cache import ContextCache

    # P3.2 双写试点：仅 LINGCLAUDE_MEMORY_DUALWRITE=1 时挂灵忆旁观者
    # （桥接器在模块级 import：其依赖链指向外部 lingmemory 包，无 core 内循环）
    sink = (
        LingMemoryCacheBridge() if dualwrite_enabled() else None
    )
    return ContextCache(cache_size=100, ttl_hours=24, memory_sink=sink)


def _make_aggregator(ctx: WiringContext) -> Any:
    from lingclaude.core.task_aggregation import TaskAggregator

    return TaskAggregator(max_group_size=5)


def _make_monitor(ctx: WiringContext) -> Any:
    from lingclaude.core.token_monitor import TokenMonitor

    return TokenMonitor()


def _make_prior_verifier(ctx: WiringContext) -> Any:
    from lingclaude.core.prior_verifier import PriorVerifier

    return PriorVerifier()


def _make_meta_cognition(ctx: WiringContext) -> Any:
    from lingclaude.core.meta_cognition import MetaCognition

    return MetaCognition()


def _make_layered_memory(ctx: WiringContext) -> Any:
    from lingclaude.core.layered_memory import LayeredMemory
    from lingclaude.core.lingmemory_bridge import dualwrite_enabled
    from lingclaude.core.lingmemory_experience_bridge import (
        LingMemoryExperienceSink,
    )

    # P3.3 双写：仅 LINGCLAUDE_MEMORY_DUALWRITE=1 时挂灵忆旁观者
    # （与 P3.2 context_cache 同一开关、同一纪律；主路默认零依赖）
    sink = LingMemoryExperienceSink() if dualwrite_enabled() else None
    return LayeredMemory(memory_sink=sink)


def _make_dementia_detector(ctx: WiringContext) -> Any:
    from lingclaude.core.dementia_detector import DementiaDetector

    return DementiaDetector()


def _make_cognitive_rhythm(ctx: WiringContext) -> Any:
    from lingclaude.core.cognitive_rhythm import CognitiveRhythm

    return CognitiveRhythm()


def _make_hooks(ctx: WiringContext) -> Any:
    from lingclaude.core.hooks import HookManager

    return HookManager()


def _make_degradation_detector(ctx: WiringContext) -> Any:
    from lingclaude.core.degradation_detector import DegradationDetector

    return DegradationDetector()


def _make_task_manager(ctx: WiringContext) -> Any:
    from lingclaude.core.task_manager import TaskManager

    return TaskManager()


def _make_skill_index(ctx: WiringContext) -> Any:
    from lingclaude.core.skill_index import SkillIndex

    return SkillIndex()


def _make_notifier(ctx: WiringContext) -> Any:
    from lingclaude.core.mailbox_notify import MailboxNotifier

    return MailboxNotifier()


def _make_state_store(ctx: WiringContext) -> Any:
    from lingclaude.core.state_store import StateStore

    return StateStore()


# ---------------------------------------------------------------------------
# WIRING_MANIFEST — 与 QueryEngine.__init__ 逐字对齐（55 项）
# 顺序仅作文档可读性；装配语义与顺序无关（契约测试乱序验证）。
# ---------------------------------------------------------------------------
WIRING_MANIFEST: tuple[WiringSpec, ...] = (
    # -- state：引擎自有容器/标志（24 项，永不下放插片，manifest 存档）--
    WiringSpec("_messages", lambda ctx: [], phase="state", note="对话消息缓冲 list[str]"),
    WiringSpec("_conversation", lambda ctx: [], phase="state", note="轮次记录 list[tuple]"),
    WiringSpec("_denials", lambda ctx: [], phase="state", note="PermissionDenial 累积"),
    WiringSpec("_transcript", lambda ctx: [], phase="state", note="转写缓冲"),
    WiringSpec("_project_index", lambda ctx: {}, phase="state", note="项目索引缓存"),
    WiringSpec("_model_config", lambda ctx: None, phase="state", note="当前模型配置槽"),
    WiringSpec("_journal_dir", lambda ctx: None, phase="state", note="R5 journal 目录覆盖(测试注入点)"),
    WiringSpec("_model_router", lambda ctx: None, phase="state", note="模型路由槽"),
    WiringSpec("_intel_relay", lambda ctx: None, phase="state", note="情报中继槽"),
    WiringSpec("_session_history_path", lambda ctx: Path("data/session_history.json"), phase="state", note="会话历史落点"),
    WiringSpec("_mcp_initialized", lambda ctx: False, phase="state", note="MCP 初始化标志"),
    WiringSpec("_active_checkpoint", lambda ctx: None, phase="state", note="活动 checkpoint 路径"),
    WiringSpec("_session_cache_hits", lambda ctx: 0, phase="state", note="缓存命中计数"),
    WiringSpec("_tool_call_count", lambda ctx: 0, phase="state", note="工具调用计数"),
    WiringSpec("_total_messages_sent", lambda ctx: 0, phase="state", note="消息发送计数"),
    WiringSpec("_l1_last_triggered_at", lambda ctx: -1, phase="state", note="L1 上次触发轮次"),
    WiringSpec("_l1_handover_checksum", lambda ctx: "", phase="state", note="L1 交接校验和"),
    WiringSpec("_degradation_alerts", lambda ctx: [], phase="state", note="退化告警累积"),
    WiringSpec("_memory_engine", lambda ctx: None, phase="state", note="T0-4 死接线已移除，槽位保留"),
    WiringSpec("_l5_orchestrator", lambda ctx: None, phase="state", note="L5 编排器 lazy init 槽"),
    WiringSpec("_pinned_model_config", lambda ctx: None, phase="state", note="钉定模型槽"),
    WiringSpec("_pinned_model_expires", lambda ctx: 0.0, phase="state", note="钉定模型过期时间戳"),
    WiringSpec("_mv1_violations", lambda ctx: [], phase="state", note="D8 MV1 违规记录(灵信 L-b seq 归因)"),
    WiringSpec("_usage", lambda ctx: _make_usage(), phase="state", note="UsageSummary 累积"),
    # -- collaborator：类实例协作者（29 项，插片候选）--
    WiringSpec("_behavior", _make_behavior, note="行为指标"),
    WiringSpec("_intel_collector", _make_intel_collector, note="情报收集"),
    WiringSpec("_session_persister", _make_session_persister, note="会话持久化委托"),
    WiringSpec("_session_runtime", _make_session_runtime, note="会话运行时委托"),
    WiringSpec("_router", _make_router, note="智能路由"),
    WiringSpec("_task_router", _make_task_router, note="任务路由"),
    WiringSpec("_tool_router", _make_tool_router, note="工具路由(create_default_router)"),
    WiringSpec("_cache", _make_cache, note="上下文缓存 100/24h"),
    WiringSpec("_aggregator", _make_aggregator, note="任务聚合 max_group=5"),
    WiringSpec("_monitor", _make_monitor, note="token 监控"),
    WiringSpec("_prior_verifier", _make_prior_verifier, note="先验校验"),
    WiringSpec("_meta_cognition", _make_meta_cognition, note="元认知"),
    WiringSpec("_layered_memory", _make_layered_memory, note="分层记忆"),
    WiringSpec("_dementia_detector", _make_dementia_detector, note="失智检测"),
    WiringSpec("_cognitive_rhythm", _make_cognitive_rhythm, note="认知节律"),
    WiringSpec("_hooks", _make_hooks, note="钩子管理"),
    WiringSpec("_degradation_detector", _make_degradation_detector, note="退化检测"),
    WiringSpec("_task_manager", _make_task_manager, note="任务管理"),
    WiringSpec("_skill_index", _make_skill_index, note="T0-4 skill match 预处理"),
    WiringSpec("_role_checker", _make_role_checker, note="角色分离"),
    WiringSpec("_l5_loop", _make_l5_loop, note="L5 对话循环"),
    WiringSpec("_notifier", _make_notifier, note="邮箱通知"),
    WiringSpec("session_store", _make_session_store, note="D3 拆包注入(公开属性)"),
    WiringSpec("state_store", _make_state_store, note="P3 状态存储接缝(JsonFileBackend/LingYiBackend)"),
    WiringSpec("model_adapter", _make_model_adapter, note="D3 模型调用 seam"),
    WiringSpec("audit_collector", _make_audit_collector, note="D3 审计收集"),
    WiringSpec("model_request_log", _make_model_request_log, note="D3 请求日志"),
    WiringSpec("_tool_executor", _make_tool_executor, note="T3-3 工具执行器"),
    WiringSpec("_tool_call_executor", _make_tool_call_executor, note="T1-3 并行/顺序分流执行"),
    WiringSpec("_write_lock", lambda ctx: __import__("threading").Lock(), phase="state", note="T1-3 写工具序列化锁"),
    # -- parameterized：已由构造参数注入（2 项，P2.b 并入 ctx）--
    WiringSpec("_provider", lambda ctx: ctx.provider, phase="parameterized", note="模型 provider"),
    WiringSpec("_runtime", lambda ctx: ctx.runtime, phase="parameterized", note="运行时"),
)


def _make_usage() -> Any:
    from lingclaude.core.models import UsageSummary

    return UsageSummary()


def assemble(
    ctx: WiringContext,
    manifest: tuple[WiringSpec, ...] = WIRING_MANIFEST,
    overrides: dict[str, Any] | None = None,
) -> list[str]:
    """按 manifest 装配 engine 属性，返回实际装配的属性名列表。

    条目顺序无关（互不依赖）；每次装配无条件覆写目标属性（P2.b 语义：
    __init__ 每次实例化都需全新装配，覆写与逐字赋值版本等价；测试注入
    先行场景请用 __new__ 裸引擎自行调 assemble，见契约测试 _bare_engine）。

    P2.c seam (2026-09-10): overrides 让任意协作者属性可注入替换实例
    （attr -> 实例），命中条目跳过工厂构造 —— 全部协作者（含 state 条目）无需 patch
    内部即可替换，这是装配层暴露给测试/宿主的标准接缝。
    """
    wired: list[str] = []
    for spec in manifest:
        if overrides and spec.attr in overrides:
            setattr(ctx.engine, spec.attr, overrides[spec.attr])
            wired.append(spec.attr)
            continue
        setattr(ctx.engine, spec.attr, spec.factory(ctx))
        wired.append(spec.attr)
    return wired
