from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from lingclaude.core.models import PermissionDenial, UsageSummary
from lingclaude.core.session import SessionManager
from lingclaude.core.redact import redact as _redact_text
from lingclaude.core.mailbox_notify import MailboxNotifier
from lingclaude.core.session_persist import SessionPersister
from lingclaude.core.session_runtime import SessionRuntime
from lingclaude.core.behavior import BehaviorMetrics, Emotion, Intent, detect_emotion, detect_intent, is_tool_intent
from lingclaude.core.intel import IntelCollector, DailyDigest, DailyDigestGenerator, IntelRelay
from lingclaude.core.prior_verifier import PriorVerifier
from lingclaude.core.degradation_detector import DegradationAlert, DegradationDetector
from lingclaude.core.meta_cognition import MetaCognition, Domain
from lingclaude.core.layered_memory import LayeredMemory, Experience, EmotionIntensity
from lingclaude.model.intelligent_router import IntelligentRouter
from lingclaude.model.task_router import TaskRouter
from lingclaude.core.context_cache import ContextCache
from lingclaude.core.task_aggregation import TaskAggregator
from lingclaude.core.token_monitor import TokenMonitor
from lingclaude.core.dementia_detector import DementiaDetector
from lingclaude.core.data_flywheel import DataFlywheel
from lingclaude.core.state_store import StateStore
from lingclaude.core.tool_call_executor import ToolCallExecutor
from lingclaude.core.types import is_tool_error
from lingclaude.core.model_call import ModelCallMixin
# 显式 re-export（消费方：test_agent_loop 等）— T3-3 瘦身后 query_engine 仍是对外兼容面
from lingclaude.core.model_call import AGENT_MAX_TOOL_ROUNDS as AGENT_MAX_TOOL_ROUNDS  # noqa: F401
from lingclaude.core.image_content import extract_image_content as extract_image_content  # noqa: F401
from lingclaude.core.image_content import image_tool_text as image_tool_text  # noqa: F401
from lingclaude.core.mcp_tools import McpToolsMixin
from lingclaude.core.submission import SubmissionMixin
from lingclaude.core.hooks import HookManager, HookType, HookContext
from lingclaude.core.cognitive_rhythm import CognitiveRhythm
from lingclaude.core.task_manager import TaskManager
from lingclaude.core.task_manager import TaskSnapshot as TaskSnapshot  # noqa: F401 — re-export（原唯一跨模块消费点，wiring gate 据此判活）
from lingclaude.core.skill_index import SkillIndex
from lingclaude.core.role_separation import create_lingclaude_role_separation
from lingclaude.core.l5_conversation_loop import L5ConversationLoop
from lingclaude.core.wiring import WiringContext, assemble  # P2.b: 装配收敛至 WIRING_MANIFEST
# Q4 (2026-09-14): 厚模块按 循环/模型/生命周期 三职责拆分 —— 方法体机械搬迁至
# mixin（行为零变化，AST 提取原方法体仅调缩进），本文件主干仅保留装配/委托面。
from lingclaude.core.query_engine_turn_mixin import QueryEngineTurnMixin
from lingclaude.core.query_engine_model_mixin import QueryEngineModelMixin
from lingclaude.core.query_engine_lifecycle_mixin import QueryEngineLifecycleMixin

# 灵元测试薄主干 (TestCase 契约)
import sys as _sys
if '/home/ai/lingclaude/lingmemory' not in _sys.path:
    _sys.path.insert(0, '/home/ai/lingclaude/lingmemory')
try:
    from test_engine import TestCase as _TestCase
except ImportError:
    _TestCase = None  # type: ignore[assignment,misc]

# 三方 L5 Orchestrator (PYTHONPATH 旁路)
if '/home/ai/lingminopt' not in _sys.path:
    _sys.path.insert(0, '/home/ai/lingminopt')
if '/home/ai/lingan' not in _sys.path:
    _sys.path.insert(0, '/home/ai/lingan')
try:
    from lingyuan.l5_orchestrator import L5Orchestrator, L5Context, R5SignalSourceMock, Z3PredicateMock
except ImportError:
    L5Orchestrator = None  # type: ignore[assignment,misc]
    L5Context = None
    R5SignalSourceMock = None
    Z3PredicateMock = None
try:
    from z3_declaration_consistency import DeclarationConsistencyChecker
except ImportError:
    DeclarationConsistencyChecker = None

# T2/T3: 灵极优 IntentPrecheck + 灵研 R5KBConflict (PYTHONPATH 旁路)
if '/home/ai/lingresearch' not in _sys.path:
    _sys.path.insert(0, '/home/ai/lingresearch')
try:
    from lingyuan.l5_orchestrator import intent_precheck as _intent_precheck
except ImportError:
    _intent_precheck = None  # type: ignore[assignment]
try:
    from experiments.r5_kb_conflict import R5KBConflictSource as _R5KBConflictSource
except ImportError:
    _R5KBConflictSource = None  # type: ignore[assignment]
from lingclaude.model.types import ModelConfig, ModelMessage, MessageRole

from lingclaude.core.types import Result, StopReason

logger = logging.getLogger(__name__)

def _estimate_message_tokens(messages: list[Any]) -> int:
    total_chars = 0
    for m in messages:
        if isinstance(m, str):
            total_chars += len(m)
        elif isinstance(m, dict):
            total_chars += len(m.get("content", "") or m.get("text", "") or "")
        elif hasattr(m, "content"):
            total_chars += len(m.content or "")
        elif m:
            total_chars += len(str(m))
    return total_chars // 4


CONSECUTIVE_FAILURE_LIMIT = 3
CHECKPOINT_DIR = Path.home() / ".lingclaude" / "checkpoints"


@dataclass(frozen=True)
class QueryEngineConfig:
    max_turns: int = 8
    max_budget_tokens: int = 200000
    compact_after_turns: int = 12
    structured_output: bool = False
    structured_retry_limit: int = 2
    consecutive_failure_limit: int = CONSECUTIVE_FAILURE_LIMIT
    max_tool_calls_per_session: int = 500
    # T1-1: LLM 摘要开关 — True 且 provider 可用时压缩走 LLM 摘要（失败降级正则）。
    # 此前 tool_executor 用 getattr(self, "use_llm_summary", False) 读不到本字段 → 恒 False 死接线。
    use_llm_summary: bool = False
    # T1-1: 按模型窗口动态预算 — 压缩摘要预算基数（窗口×4%），None 时回退 max_budget_tokens。
    # 此前 EngineConfig 有字段但 loader 不读、本类无该字段 → tool_executor 恒回退静态 200k。
    context_window_tokens: int | None = None


@dataclass(frozen=True)
class TurnResult:
    prompt: str
    output: str
    matched_commands: tuple[str, ...]
    matched_tools: tuple[str, ...]
    permission_denials: tuple[PermissionDenial, ...]
    usage: UsageSummary
    stop_reason: StopReason


def _get_l5_auditor(engine):
    inst = getattr(engine, "_l5_auditor_inst", None)
    if inst is None:
        from lingclaude.core.l5_audit import L5Auditor
        inst = L5Auditor(engine)
        object.__setattr__(engine, "_l5_auditor_inst", inst)
    return inst


class QueryEngine(
    ModelCallMixin,
    McpToolsMixin,
    SubmissionMixin,
    QueryEngineTurnMixin,
    QueryEngineModelMixin,
    QueryEngineLifecycleMixin,
):
    def __init__(
        self,
        config: QueryEngineConfig | None = None,
        session_manager: SessionManager | None = None,
        model_provider: Any | None = None,
        runtime: Any | None = None,
        wiring_overrides: dict[str, Any] | None = None,  # P2.c seam: 协作者注入接缝
    ) -> None:
        # P2.b: 主干三件套之外的全部装配（55 项）收敛至 WIRING_MANIFEST（core/wiring.py）。
        # 不变式：新增协作者 = manifest 加一行，本文件 diff 为 0；
        # 装配语义与原逐字赋值版本逐项对齐（回归网：tests/test_p2a_wiring_manifest.py）。
        self.config = config or QueryEngineConfig()
        self.session_manager = session_manager or SessionManager()
        self.session_id: str = uuid4().hex[:16]
        ctx = WiringContext(
            engine=self,
            session_manager=self.session_manager,
            provider=model_provider,
            runtime=runtime,
        )
        assemble(ctx, overrides=wiring_overrides)
        # T0-7: 工具错误 → ON_ERROR hook（ToolPipeline error listener 接线，原先定义无触发点）
        if self._runtime is not None:
            _pipeline = getattr(self._runtime, "tool_pipeline", None)
            # 类模块判定(避免 core→engine 循环导入与新增懒 import):
            # 不向 MagicMock/伪造 runtime 注册无效 listener
            if type(_pipeline).__module__ == "lingclaude.engine.tool_pipeline" \
                    and type(_pipeline).__name__ == "ToolPipeline":
                def _on_tool_error(tool_name: str, error_msg: str) -> None:
                    self._hooks.trigger(HookContext(
                        hook_type=HookType.ON_ERROR,
                        session_id=self.session_id,
                        tool_name=tool_name,
                        error_message=error_msg,
                    ))
                _pipeline.add_error_listener(_on_tool_error)
        self._load_session_state()

    def init_mailbox(self, mailbox: Any) -> None:
        self._notifier.mailbox = mailbox

    def read_lingmessage_threads(self) -> tuple[Any, ...]:
        return self._notifier.read_lingmessage_threads()

    def notify_completion(self, task: str, result_summary: str, channel: str = "ecosystem") -> None:
        self._notifier.notify_completion(task, result_summary, channel)

    def notify_risk(self, risk_type: str, details: str, severity: str = "warning") -> None:
        self._notifier.notify_risk(risk_type, details, severity)

    def notify_vote(self, proposal: str, options: list[str], deadline_hours: int = 48) -> None:
        self._notifier.notify_vote(proposal, options, deadline_hours)

    @property
    def behavior_metrics(self) -> BehaviorMetrics:
        return self._behavior

    @property
    def layered_memory(self) -> LayeredMemory:
        return self._layered_memory

    def _sync_session_store(self) -> None:
        """LINGKERNEL_v1 D5 修复: 同步 session_store 与 engine 当前 session_id / CHECKPOINT_DIR.

        测试用 monkeypatch.setattr("lingclaude.core.query_engine.CHECKPOINT_DIR", ...)
        或直接 engine.session_id = "..." 改变运行时状态; SessionStore 在 __init__ 固化
        两者会导致 has_checkpoint/save/load/clear 走错路径. 现场同步保持旧语义.
        """
        self.session_store.session_id = self.session_id
        self.session_store._checkpoint_dir = CHECKPOINT_DIR

    @property
    def has_checkpoint(self) -> bool:
        # LINGKERNEL_v1 D5: 委托 session_store
        self._sync_session_store()
        return self.session_store.has_checkpoint

    @property
    def meta_cognition(self) -> MetaCognition:
        return self._meta_cognition

    @property
    def prior_verifier(self) -> PriorVerifier:
        return self._prior_verifier

    @classmethod
    def from_config_file(cls, config_path: str | None = None) -> Result[QueryEngine]:
        from lingclaude.core.config import load_config, find_config_path
        from lingclaude.model.factory import create_provider
        from pathlib import Path as _Path

        try:
            cfg = load_config(config_path and _Path(config_path))

            if config_path:
                project_root = _Path(config_path).resolve().parent
            else:
                found = find_config_path()
                project_root = found.resolve().parent if found else Path.cwd()

            engine_cfg = QueryEngineConfig(
                max_turns=cfg.engine.max_turns,
                max_budget_tokens=cfg.engine.max_budget_tokens,
                compact_after_turns=cfg.engine.compact_after_turns,
                structured_output=cfg.engine.structured_output,
                # T1-1: 此前构造点不透传 → from_config 主路径下两开关均不可达（死接线）
                use_llm_summary=cfg.engine.use_llm_summary,
                context_window_tokens=cfg.engine.context_window_tokens,
                # T1-2: verification.max_tool_calls_per_session 此前漏透传
                # → yaml 配置静默失效，引擎恒用默认值 500（死接线）
                max_tool_calls_per_session=cfg.verification.max_tool_calls_per_session,
            )

            provider = None
            mc = cfg.model
            model_cfg = ModelConfig(
                model=mc.model,
                api_key=mc.api_key,
                base_url=mc.base_url,
                max_tokens=mc.max_tokens,
                temperature=mc.temperature,
                system_prompt=mc.system_prompt,
            )
            provider_result = create_provider(config=model_cfg, provider_name=mc.provider)
            if provider_result.is_ok:
                provider = provider_result.data
                if not model_cfg.api_key and provider._config.api_key:
                    model_cfg = ModelConfig(
                        model=model_cfg.model,
                        api_key=provider._config.api_key,
                        base_url=model_cfg.base_url,
                        max_tokens=model_cfg.max_tokens,
                        temperature=model_cfg.temperature,
                        system_prompt=model_cfg.system_prompt,
                    )

            engine = cls(config=engine_cfg, model_provider=provider, runtime=None)
            engine._model_config = model_cfg
            engine._model_router = cfg.model_router
            engine._session_history_path = project_root / cfg.intel.session_history_path
            engine.init_intel(output_dir=project_root / cfg.intel.output_dir)
            return Result.ok(engine)
        except Exception as e:
            return Result.fail(f"Failed to create engine from config: {e}", code="CONFIG_ERROR")

    def set_runtime(self, runtime: Any) -> None:
        self._runtime = runtime

    def init_intel(self, output_dir: Path | None = None) -> None:
        self._intel_relay = IntelRelay(output_dir=output_dir or Path(".lingclaude/intel"))

    def reset(self) -> None:
        self._save_session_state()
        self._clear_checkpoint()
        self.session_id = uuid4().hex[:16]
        self._messages.clear()
        self._conversation.clear()
        self._denials.clear()
        self._usage = UsageSummary()
        self._transcript.clear()
        self._layered_memory.working.clear()

    @property
    def turn_count(self) -> int:
        return len(self._messages) // 2

    L1_MESSAGE_THRESHOLD: int = 50
    L2_MESSAGE_THRESHOLD: int = 100

    def _check_degradation(self, prompt: str, output: str) -> None:
        return _get_l5_auditor(self).check_degradation(prompt, output)

    def get_degradation_alerts(self) -> list[DegradationAlert]:
        return _get_l5_auditor(self).get_degradation_alerts()

    def get_degradation_health(self) -> dict[str, object]:
        return _get_l5_auditor(self).get_degradation_health()

    def _apply_l5_audit(self, prompt: str, output: str) -> str:
        return _get_l5_auditor(self).apply_l5_audit(prompt, output)

    def _ensure_l5_orchestrator(self):
        return _get_l5_auditor(self).ensure_l5_orchestrator()

    def run_l5_audit_full(self, prompt, output, rules=None, tool_log=None) -> str:
        return _get_l5_auditor(self).run_l5_audit_full(prompt, output, rules, tool_log)

    def _run_l5_orchestrator(self, prompt, output, rules=None, tool_log=None) -> str:
        return _get_l5_auditor(self)._run_l5_orchestrator(prompt, output, rules, tool_log)

    def _collect_relevant_rules(self) -> list[str]:
        return _get_l5_auditor(self).collect_relevant_rules()

    def _collect_tool_call_log(self) -> list[str]:
        return _get_l5_auditor(self).collect_tool_call_log()

    def _check_behavior(self, prompt: str) -> str | None:
        return _get_l5_auditor(self).check_behavior(prompt)

    def get_l5_audit_history(self) -> list:
        return _get_l5_auditor(self).get_l5_audit_history()

    def _check_intent(self, prompt: str) -> str | None:
        return _get_l5_auditor(self).check_intent(prompt)

    def _check_entity_conflict(self, prompt: str, output: str) -> str:
        return _get_l5_auditor(self).check_entity_conflict(prompt, output)

    def _check_l1_handover(self) -> None:
        return _get_l5_auditor(self).check_l1_handover()

    def _check_l2_restart(self) -> bool:
        return _get_l5_auditor(self).check_l2_restart()

    @property
    def usage(self) -> UsageSummary:
        return self._usage

    def get_stats(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "turns": len(self._messages) // 2,
            "usage": self._usage.to_dict(),
            "denials": len(self._denials),
            "transcript_size": len(self._transcript),
        }
