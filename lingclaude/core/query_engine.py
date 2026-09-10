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
from lingclaude.core.tool_call_executor import ToolCallExecutor
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
from lingclaude.model.types import ModelConfig

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


class QueryEngine(ModelCallMixin, McpToolsMixin, SubmissionMixin):
    def __init__(
        self,
        config: QueryEngineConfig | None = None,
        session_manager: SessionManager | None = None,
        model_provider: Any | None = None,
        runtime: Any | None = None,
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
        assemble(ctx)
        # T0-7: 工具错误 → ON_ERROR hook（ToolPipeline error listener 接线，原先定义无触发点）
        if self._runtime is not None:
            _pipeline = getattr(self._runtime, "tool_pipeline", None)
            if _pipeline is not None and hasattr(_pipeline, "add_error_listener"):
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
        from lingclaude.model.types import ModelConfig
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

    def _generate_response(
        self,
        prompt: str,
        matched_commands: tuple[str, ...],
        matched_tools: tuple[str, ...],
        denied_tools: tuple[PermissionDenial, ...],
    ) -> str:
        if self._provider is not None:
            return self._call_model(prompt)

        context_parts = [f"Prompt: {prompt}"]
        if matched_commands:
            context_parts.append(f"已匹配命令: {', '.join(matched_commands)}")
        if matched_tools:
            context_parts.append(f"已匹配工具: {', '.join(matched_tools)}")
        if denied_tools:
            context_parts.append(f"权限拒绝: {len(denied_tools)} 个工具被拒绝")
        return self._format_output(context_parts)

    def _track_behavior(self, prompt: str, output: str, used_tools: bool = False) -> None:
        emotion = detect_emotion(prompt)
        intent = detect_intent(prompt)
        self._behavior = self._behavior.record_turn(
            emotion=emotion,
            is_correction=intent == Intent.CORRECTION,
            is_frustrated=emotion == Emotion.FRUSTRATED,
            used_tools=used_tools,
            needed_tools=not used_tools and is_tool_intent(intent),
        )
        self._collect_behavior_intel()

        if intent == Intent.CORRECTION:
            self._meta_cognition.record_failure(
                Domain.CODE_UNDERSTANDING, error_description=prompt[:100],
            )
            exp = Experience.create(
                problem=prompt[:200],
                reflection=f"用户纠正: {output[:100]}",
                emotion=EmotionIntensity.MEDIUM,
                associations=("correction", "user_feedback"),
            )
            self._layered_memory.record_experience(exp)
        elif used_tools:
            self._meta_cognition.record_success(Domain.CODE_UNDERSTANDING)
        else:
            self._meta_cognition.record_success(Domain.GENERAL_KNOWLEDGE)

    def _build_messages(self, prompt: str) -> list:
        from lingclaude.model.types import ModelMessage, MessageRole

        messages: list[ModelMessage] = []
        system_prompt = self._build_adaptive_system_prompt()
        if system_prompt:
            messages.append(ModelMessage(role=MessageRole.SYSTEM, content=system_prompt))
        for role, content in self._conversation:
            messages.append(ModelMessage(role=MessageRole(role), content=content))
        messages.append(ModelMessage(role=MessageRole.USER, content=prompt))
        return messages

    def _finalize_turn(
        self,
        prompt: str,
        content: str,
        used_tools: bool,
        total_input: int,
        total_output: int,
        resolved_config: Any,
    ) -> str:
        vr = self._prior_verifier.analyze(content, used_tools=used_tools)
        final_content = vr.corrected_text if vr.corrected_text else content
        self._track_behavior(prompt, final_content, used_tools=used_tools)
        self._usage = self._usage.add_usage(total_input, total_output)
        self._monitor.record_usage(
            model=str(resolved_config.model) if resolved_config else "unknown",
            task_type="unknown",
            total_tokens=total_input + total_output,
            input_tokens=total_input,
            output_tokens=total_output,
        )
        self._conversation.append(("user", prompt))
        self._conversation.append(("assistant", final_content))
        self._layered_memory.working.append("user", prompt)
        self._layered_memory.working.append("assistant", final_content)
        return final_content

    def _execute_tool(self, name: str, arguments_json: str) -> str:
        return self._tool_executor._execute_tool(name, arguments_json)

    def _execute_mcp_tool(self, name: str, kwargs: dict[str, Any]) -> str:
        return self._tool_executor._execute_mcp_tool(name, kwargs)

    def _compact_if_needed(self) -> None:
        self._tool_executor._compact_if_needed()

    def _pre_check_compact(self) -> None:
        """T1-1: turn 内触发 — 模型请求前预算预检，超阈值先压缩。

        与 turn 后 _compact_if_needed 的区别：请求前触发，避免把超预算上下文
        发给模型（原实现仅在 turn 结束后才压缩，超预算请求已经发出）。
        压缩条件由 tool_executor._compact_if_needed 内部判定，此处幂等调用。
        """
        msg_tokens = _estimate_message_tokens(self._messages)
        threshold = self.config.max_budget_tokens * 0.8
        if msg_tokens > threshold:
            logger.info(
                "T1-1 pre-compact before model request: %d tokens > %.0f threshold",
                msg_tokens, threshold,
            )
            self._tool_executor._compact_if_needed()

    def _archive_dropped_messages(self, dropped_count: int) -> None:
        self._tool_executor._archive_dropped_messages(dropped_count)

    def switch_model(self, model_name: str) -> Result[str]:
        """P1-4: 会话中途切换模型（保留上下文，重建 provider）。

        Args:
            model_name: 目标模型名（如 gpt-4o / claude-3-5-sonnet / deepseek-chat）。

        Returns:
            Result.ok(新模型名)；失败返回错误。
        """
        from lingclaude.model.factory import create_provider
        from lingclaude.model.types import ModelConfig

        if not model_name or not model_name.strip():
            return Result.fail("model name is required", code="BAD_MODEL_NAME")

        target = model_name.strip()
        base = self._model_config or ModelConfig()
        # F12h:优先从 TaskRouter 按模型名反查 provider — 连 base_url/key 一起换。
        # 旧行为只换模型名、沿用旧端点:用户 /model minimax-m3 后实际拿 minimax
        # 名字请求 zhipu 端点,必 404(假切换)。
        _pname, _pinfo = self._task_router.find_provider_by_model(target)
        if _pinfo is not None:
            new_cfg = ModelConfig(
                model=target,
                api_key=_pinfo.api_key,
                base_url=_pinfo.base_url,
                max_tokens=base.max_tokens,
                temperature=base.temperature,
                system_prompt=base.system_prompt,
            )
        else:
            new_cfg = ModelConfig(
                model=target,
                api_key=base.api_key,
                base_url=base.base_url,
                max_tokens=base.max_tokens,
                temperature=base.temperature,
                system_prompt=base.system_prompt,
            )
        provider_result = create_provider(config=new_cfg)
        if provider_result.is_error:
            return provider_result
        self._provider = provider_result.data
        self._model_config = new_cfg
        # 同步 config.model（供 /model 显示与 tool_executor 读取）
        try:
            if hasattr(self.config, "model"):
                self.config = self.config.__class__(
                    **{**self.config.__dict__, "model": new_cfg.model}
                )
        except Exception:  # noqa: BLE001 — config 不可变时仅更新 _model_config
            pass
        logger.info("Switched model to %s", new_cfg.model)
        return Result.ok(new_cfg.model)

    def pin_model(self, model_name: str, ttl_seconds: int = 0) -> Result[str]:
        """Pin a model to bypass TaskRouter for subsequent requests.

        Args:
            model_name: 目标模型名（如 deepseek-v4-flash / ark-code-latest）
            ttl_seconds: 存活秒数，0 或负数表示会话级永久钉住

        Returns:
            Result.ok(钉住的模型名)；失败返回错误。
        """
        import time
        from lingclaude.model.factory import create_provider
        from lingclaude.model.types import ModelConfig

        if not model_name or not model_name.strip():
            return Result.fail("model name is required", code="BAD_MODEL_NAME")

        target = model_name.strip()
        base = self._model_config or ModelConfig()
        _pname, _pinfo = self._task_router.find_provider_by_model(target)
        if _pinfo is not None:
            pinned_cfg = ModelConfig(
                model=target,
                api_key=_pinfo.api_key,
                base_url=_pinfo.base_url,
                max_tokens=base.max_tokens,
                temperature=base.temperature,
                system_prompt=base.system_prompt,
            )
        else:
            # 允许钉住未知模型（直接用当前配置的 key/url），由后续调用验证
            pinned_cfg = ModelConfig(
                model=target,
                api_key=base.api_key,
                base_url=base.base_url,
                max_tokens=base.max_tokens,
                temperature=base.temperature,
                system_prompt=base.system_prompt,
            )

        # 验证 provider 可用性
        provider_result = create_provider(config=pinned_cfg)
        if provider_result.is_error:
            return Result.fail(f"provider creation failed: {provider_result.error}", code="PROVIDER_CREATE_FAILED")

        self._pinned_model_config = pinned_cfg
        self._pinned_model_expires = time.time() + ttl_seconds if ttl_seconds > 0 else float('inf')
        logger.info("Pinned model to %s (ttl=%ss)", pinned_cfg.model, ttl_seconds if ttl_seconds > 0 else "session")
        return Result.ok(pinned_cfg.model)

    def unpin_model(self) -> Result[str]:
        """Remove pinned model, restore TaskRouter-based resolution."""
        if self._pinned_model_config is None:
            return Result.fail("no model pinned", code="NOT_PINNED")
        old = self._pinned_model_config.model
        self._pinned_model_config = None
        self._pinned_model_expires = 0.0
        logger.info("Unpinned model (was %s)", old)
        return Result.ok(old)

    def is_model_pinned(self) -> bool:
        import time
        if self._pinned_model_config is None:
            return False
        if time.time() > self._pinned_model_expires:
            # TTL 过期自动解除
            self._pinned_model_config = None
            self._pinned_model_expires = 0.0
            return False
        return True

    def get_pinned_model_name(self) -> str | None:
        if self.is_model_pinned():
            return self._pinned_model_config.model
        return None

    def _resolve_model_config(self, prompt: str) -> tuple[ModelConfig | None, Any]:
        return self._tool_executor._resolve_model_config(prompt)

    def _build_adaptive_system_prompt(self) -> str:
        from lingclaude.core.system_prompt_builder import build_adaptive_system_prompt
        return build_adaptive_system_prompt(
            behavior=self._behavior,
            layered_memory=self._layered_memory,
            meta_cognition=self._meta_cognition,
            messages=self._messages,
            session_cache_hits=self._session_cache_hits,
            dementia_detector=self._dementia_detector,
            project_index=self._project_index,
            tool_call_count=self._tool_call_count,  # R8: 触发 sub_agent 推荐提示
        )

    def _execute_tool_with_retry(self, name: str, arguments_json: str) -> str:
        result = self._execute_tool(name, arguments_json)
        if '"error"' not in result:
            return result

        retry_args = self._fix_tool_arguments(name, arguments_json, result)
        if retry_args is not None:
            self._behavior = self._behavior.record_tool_calls(count=1)
            return self._execute_tool(name, retry_args)

        return result

    def _is_concurrency_safe(self, name: str) -> bool:
        """T1-3: 查询工具是否可并行执行（is_concurrency_safe 字段落地）。"""
        if self._runtime is None:
            return False
        tool = self._runtime.registry.get(name)
        return tool.is_ok and getattr(tool.data, "is_concurrency_safe", False)

    def _fix_tool_arguments(self, name: str, args_json: str, error_result: str) -> str | None:
        try:
            kwargs = json.loads(args_json)
        except json.JSONDecodeError:
            return None

        if name == "read" and "path" in kwargs:
            path = kwargs["path"]
            if not Path(path).exists():
                for candidate in Path(".").rglob(Path(path).name):
                    kwargs["path"] = str(candidate)
                    return json.dumps(kwargs, ensure_ascii=False)

        if name in ("grep", "glob") and "pattern" in kwargs:
            if "*" not in kwargs["pattern"] and name == "glob":
                kwargs["pattern"] = f"**/{kwargs['pattern']}"
                return json.dumps(kwargs, ensure_ascii=False)

        return None

    def _get_last_tool_output(self, messages: list, tool_call_id: str) -> str:
        for msg in reversed(messages):
            if (
                hasattr(msg, "tool_call_id")
                and msg.tool_call_id == tool_call_id
            ):
                return msg.content or ""
        return ""

    def collect_daily_digest(self, report_date: str | None = None) -> Result[DailyDigest]:
        items = self._intel_collector.collect_all()
        digest = DailyDigestGenerator.generate(items, report_date)
        if self._intel_relay is not None:
            relay_result = self._intel_relay.relay(digest)
            if relay_result.is_error:
                return relay_result  # type: ignore[return-value]
        self._intel_collector.clear()
        return Result.ok(digest)

    def set_session_history_path(self, path: Path) -> None:
        self._session_history_path = path

    def _append_to_session_history(self, query: str, response: str) -> None:
        try:
            self._session_history_path.parent.mkdir(parents=True, exist_ok=True)
            history: list[dict[str, str]] = []
            if self._session_history_path.exists():
                try:
                    raw = json.loads(self._session_history_path.read_text(encoding="utf-8"))
                    if isinstance(raw, list):
                        history = raw
                except (json.JSONDecodeError, ValueError):
                    logger.warning("Session history corrupted, starting fresh")
                    history = []
            history.append({
                "query": query[:200],
                "title": query[:80],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "session_id": self.session_id,
            })
            # 原子写入：先写临时文件，再rename，防止进程中断导致损坏
            import tempfile
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._session_history_path.parent),
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(history, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, str(self._session_history_path))
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            logger.warning("Session history write failed: %s", e)

    def _learn_from_turn(self, prompt: str, response: str) -> None:
        from lingclaude.core.turn_learner import record_turn_learnings
        record_turn_learnings(
            prompt=prompt,
            behavior=self._behavior,
            messages=self._messages,
            session_id=self.session_id,
        )

    def _log_to_flywheel(
        self,
        pattern_type: str,
        error_message: str,
        tool_name: str = "",
        file_path: str = "",
        context: str = "",
    ) -> None:
        from lingclaude.core.data_flywheel import DataFlywheel  # noqa: F401 — 接线验证
        self._session_runtime.log_to_flywheel(pattern_type, error_message, tool_name, file_path, context)

    def _session_state_path(self) -> Path:
        return self._session_runtime.session_state_path()

    def _save_session_state(self) -> None:
        self._session_runtime.save_session_state()

    def _load_session_state(self) -> None:
        self._session_runtime.load_session_state()

    def _check_optimization_triggers(self) -> None:
        self._session_runtime.check_optimization_triggers()

    def _collect_behavior_intel(self) -> None:
        self._session_runtime.collect_behavior_intel()

    def _index_project(self) -> dict[str, Any]:
        return self._session_runtime.index_project()

    def _format_output(self, lines: list[str]) -> str:
        return self._session_runtime.format_output(lines)
