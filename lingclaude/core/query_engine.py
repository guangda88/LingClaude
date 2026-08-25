from __future__ import annotations

import json
import logging
import hashlib
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator
from uuid import uuid4

from lingclaude.core.models import PermissionDenial, UsageSummary
from lingclaude.core.session import Session, SessionManager
from lingclaude.core.mailbox_notify import MailboxNotifier
from lingclaude.core.session_persist import SessionPersister
from lingclaude.core.session_runtime import SessionRuntime
from lingclaude.core.behavior import BehaviorMetrics, Emotion, Intent, detect_emotion, detect_intent, is_tool_intent
from lingclaude.core.intel import IntelCollector, DailyDigest, DailyDigestGenerator, IntelRelay
from lingclaude.core.prior_verifier import PriorVerifier
from lingclaude.core.degradation_detector import DegradationAlert, DegradationDetector, ToolCall, extract_tool_calls_from_text
from lingclaude.core.meta_cognition import MetaCognition, Domain
from lingclaude.core.layered_memory import LayeredMemory, Experience, EmotionIntensity
from lingclaude.model.intelligent_router import IntelligentRouter
from lingclaude.model.task_router import TaskRouter
from lingclaude.engine.tool_router import ToolRouter, create_default_router
from lingclaude.engine import mcp_proxy
from lingclaude.core.context_cache import ContextCache
from lingclaude.core.task_aggregation import TaskAggregator, TaskPriority
from lingclaude.core.token_monitor import TokenMonitor
from lingclaude.core.dementia_detector import DementiaDetector
from lingclaude.core.context_compression import compress_messages, CompressionConfig, CompressionLevel
from lingclaude.core.hooks import HookManager, HookType, HookContext
from lingclaude.core.cognitive_rhythm import CognitiveRhythm, ImbalanceType
from lingclaude.core.task_manager import TaskManager, TaskSnapshot
from lingclaude.core.skill_index import SkillIndex
from lingclaude.core.memory_engine import MemoryStore
from lingclaude.core.role_separation import create_lingclaude_role_separation
from lingclaude.core.l5_conversation_loop import L5ConversationLoop, L5ConversationConfig, L5RoundResult

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
    from lingyuan.l5_orchestrator import L5Orchestrator, L5Context, OrchestratorConfig, R5SignalSourceMock, Z3PredicateMock
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


AGENT_MAX_TOOL_ROUNDS = 10
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


class QueryEngine:
    def __init__(
        self,
        config: QueryEngineConfig | None = None,
        session_manager: SessionManager | None = None,
        model_provider: Any | None = None,
        runtime: Any | None = None,
    ) -> None:
        self.config = config or QueryEngineConfig()
        self.session_manager = session_manager or SessionManager()
        self.session_id: str = uuid4().hex[:16]
        self._messages: list[str] = []
        self._conversation: list[tuple[str, str]] = []
        self._denials: list[PermissionDenial] = []
        self._usage = UsageSummary()
        self._transcript: list[str] = []
        self._provider = model_provider
        self._runtime = runtime
        self._behavior = BehaviorMetrics()
        self._project_index: dict[str, Any] = {}
        self._model_config: Any = None
        self._model_router: Any = None
        self._intel_collector = IntelCollector()
        self._intel_relay: IntelRelay | None = None
        self._session_history_path: Path = Path("data/session_history.json")
        self._notifier = MailboxNotifier()
        self._session_persister = SessionPersister(self)
        self._session_runtime = SessionRuntime(self)
        self._router = IntelligentRouter()
        self._task_router = TaskRouter()
        self._tool_router: ToolRouter = create_default_router()
        self._mcp_initialized: bool = False
        self._cache = ContextCache(cache_size=100, ttl_hours=24)
        self._aggregator = TaskAggregator(max_group_size=5)
        self._monitor = TokenMonitor()
        self._prior_verifier = PriorVerifier()
        self._meta_cognition = MetaCognition()
        self._layered_memory = LayeredMemory()
        self._active_checkpoint: Path | None = None
        self._session_cache_hits: int = 0
        self._dementia_detector = DementiaDetector()
        self._cognitive_rhythm = CognitiveRhythm()
        self._tool_call_count: int = 0
        self._hooks = HookManager()
        self._total_messages_sent: int = 0
        self._l1_last_triggered_at: int = -1
        self._l1_handover_checksum: str = ""
        self._degradation_detector = DegradationDetector()
        self._degradation_alerts: list[DegradationAlert] = []
        self._task_manager = TaskManager()
        # T0-4: 接入 SkillIndex（skill match 用于 prompt 预处理）
        self._skill_index = SkillIndex()
        # T0-4: 删除 MemoryEngine 死接线（无消费者）
        self._memory_engine = None
        self._role_checker = create_lingclaude_role_separation()
        self._l5_loop = L5ConversationLoop(l5_session_id=self.session_id)
        self._l5_orchestrator: Any = None  # lazy init
        # LINGKERNEL_v1 D3: 拆包模块注入 (dsh spine 对位)
        from lingclaude.core.session_store import SessionStore
        from lingclaude.core.model_adapter import ModelAdapter
        from lingclaude.core.audit_collector import AuditCollector
        from lingclaude.core.model_request_log import ModelRequestLog, Mv1Violation
        self.session_store = SessionStore(self.session_manager, self.session_id)
        self.model_adapter = ModelAdapter(self._provider)
        self.audit_collector = AuditCollector()
        self.model_request_log = ModelRequestLog()
        from lingclaude.core.tool_executor import ToolExecutor
        self._tool_executor = ToolExecutor(self)
        # T1-3 深化: 并行冲突检测 — 写工具序列化锁（防止并发写冲突）
        self._write_lock = threading.Lock()
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
        # D8: 结构化违规记录 (灵信 L-b 按 seq 归因)
        self._mv1_violations: list[Mv1Violation] = []
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

    def submit(
        self,
        prompt: str,
        matched_commands: tuple[str, ...] = (),
        matched_tools: tuple[str, ...] = (),
        denied_tools: tuple[PermissionDenial, ...] = (),
    ) -> TurnResult:
        if len(self._messages) // 2 >= self.config.max_turns:
            return TurnResult(
                prompt=prompt,
                output=f"已达最大轮次 ({self.config.max_turns})。",
                matched_commands=matched_commands,
                matched_tools=matched_tools,
                permission_denials=denied_tools,
                usage=self._usage,
                stop_reason=StopReason.MAX_TURNS_REACHED,
            )

        # T0: 行为校验 (零推理成本) — 在进入主流程前拦截明显问题
        t0_nudge = self._check_behavior(prompt)
        if t0_nudge is not None:
            return TurnResult(
                prompt=prompt,
                output=t0_nudge,
                matched_commands=matched_commands,
                matched_tools=matched_tools,
                permission_denials=denied_tools,
                usage=self._usage,
                stop_reason=StopReason.COMPLETED,
            )

        # TaskManager: 新需求进来 → 挂起当前任务上下文
        self._task_manager.on_new_request(prompt, self)

        # T0-4: SkillIndex 匹配 — 将匹配的 skill 提示注入 prompt
        # （带 SKILL.md 路径 — 索引哲学是"不加载不执行，灵元自己读自己执行"，无路径则读不了）
        if self._skill_index is not None:
            matched_skills = self._skill_index.match(prompt)
            if matched_skills:
                skill_hint = "\n\n[匹配的 skills]\n" + "\n".join(
                    f"- {s['name']}: {s['desc']}（SKILL.md: {s['path']}）" for s in matched_skills[:3]
                )
                prompt = prompt + skill_hint

        pre_ctx = HookContext(
            hook_type=HookType.PRE_TASK,
            session_id=self.session_id,
            prompt=prompt,
            metadata={"matched_commands": list(matched_commands), "matched_tools": list(matched_tools)},
        )
        pre_result = self._hooks.trigger(pre_ctx)
        if pre_result.modified_context:
            prompt = pre_result.modified_context.prompt or prompt

        # T2: 意图确认 (round 0) — 模型复述 vs 用户意图, 不一致则阻塞
        intent_check = self._check_intent(prompt)
        if intent_check is not None:
            return TurnResult(
                prompt=prompt,
                output=intent_check,
                matched_commands=matched_commands,
                matched_tools=matched_tools,
                permission_denials=denied_tools,
                usage=self._usage,
                stop_reason=StopReason.COMPLETED,
            )

        # T1-1: turn 内触发 — 模型请求前预检预算，超预算先压缩再请求（原仅 turn 后触发）
        self._pre_check_compact()

        output = self._generate_response(prompt, matched_commands, matched_tools, denied_tools)

        projected = self._usage.add_turn(prompt, output)
        stop_reason = StopReason.COMPLETED

        diagnosis = self._dementia_detector.diagnose()
        if diagnosis.should_hard_stop:
            stop_reason = StopReason.CONSECUTIVE_FAILURE
            output = f"[硬中断] 认知退化严重（痴呆指数 {diagnosis.dementia_index:.0%}），自动停止。请重新开始对话或简化任务。"
        if "[硬中断]" in output:
            stop_reason = StopReason.CONSECUTIVE_FAILURE
        elif projected.input_tokens + projected.output_tokens > self.config.max_budget_tokens:
            stop_reason = StopReason.MAX_BUDGET_REACHED

        self._cognitive_rhythm.record_thinking(content=prompt)
        self._cognitive_rhythm.record_action(content=output)
        rhythm = self._cognitive_rhythm.diagnose()

        self._messages.append(prompt)
        self._messages.append(output)
        self._transcript.append(output)
        self._denials.extend(denied_tools)
        self._usage = projected
        self._compact_if_needed()
        self._total_messages_sent += 1
        self._check_degradation(prompt, output)
        output = self._apply_l5_audit(prompt, output)
        # T3: 实体冲突检查 (输出后)
        output = self._check_entity_conflict(prompt, output)
        self._check_l1_handover()
        self._check_l2_restart()
        self._append_to_session_history(prompt, output)
        self._learn_from_turn(prompt, output)

        if self.turn_count % 5 == 0 and self.turn_count > 0:
            self._check_optimization_triggers()

        if rhythm.imbalance != ImbalanceType.NONE:
            logger.warning(
                "Cognitive rhythm: %s — %s",
                rhythm.imbalance.value, rhythm.recommendation,
            )

        if stop_reason == StopReason.CONSECUTIVE_FAILURE:
            self.notify_risk("硬中断触发", f"会话 {self.session_id[:8]} 连续失败触发硬中断。Query: {prompt[:80]}")

        self._hooks.trigger(HookContext(
            hook_type=HookType.POST_TASK,
            session_id=self.session_id,
            prompt=prompt,
            output=output,
            metadata={"stop_reason": stop_reason.value},
        ))
        if stop_reason != StopReason.COMPLETED:
            self._hooks.trigger(HookContext(
                hook_type=HookType.ON_STOP,
                session_id=self.session_id,
                stop_reason=stop_reason.value,
                prompt=prompt,
                output=output,
            ))

        # TaskManager: 任务完成 → 恢复上一个任务上下文
        restored = self._task_manager.on_task_complete(self)
        if restored:
            pending = self._task_manager.pending_summary()
            suffix = f"\n\n---\n⏳ 已恢复挂起任务: {restored.user_prompt[:60]}"
            if pending:
                suffix += f"\n📋 还有 {len(self._task_manager.pending)} 个待处理:\n{pending}"
            output = output + suffix

        return TurnResult(
            prompt=prompt,
            output=output,
            matched_commands=matched_commands,
            matched_tools=matched_tools,
            permission_denials=denied_tools,
            usage=self._usage,
            stop_reason=stop_reason,
        )

    def stream_submit(
        self,
        prompt: str,
        matched_commands: tuple[str, ...] = (),
        matched_tools: tuple[str, ...] = (),
        denied_tools: tuple[PermissionDenial, ...] = (),
    ) -> Any:
        yield {"type": "message_start", "session_id": self.session_id, "prompt": prompt}
        if matched_commands:
            yield {"type": "command_match", "commands": matched_commands}
        if matched_tools:
            yield {"type": "tool_match", "tools": matched_tools}
        if denied_tools:
            yield {"type": "permission_denial", "denials": [d.tool_name for d in denied_tools]}
        if self._provider is None:
            result = self.submit(prompt, matched_commands, matched_tools, denied_tools)
            yield {"type": "message_delta", "text": result.output}
            yield {
                "type": "message_stop",
                "usage": result.usage.to_dict(),
                "stop_reason": result.stop_reason.value,
                "transcript_size": len(self._transcript),
            }
            return
        final_content = ""
        usage_data: dict[str, Any] = {}
        stop_reason = "end_turn"
        transcript_size = 0
        for event in self.stream_call_model(prompt):
            if event["type"] == "text_delta":
                yield {"type": "message_delta", "text": event["text"]}
            elif event["type"] == "tool_call_start":
                yield {"type": "tool_call_start", "name": event["name"], "arguments": event["arguments"]}
            elif event["type"] == "tool_call_end":
                yield {"type": "tool_call_end", "name": event["name"], "output_preview": event["output_preview"], "is_error": event["is_error"]}
            elif event["type"] == "status":
                yield {"type": "status", "message": event["message"]}
            elif event["type"] == "hard_interrupt":
                yield {"type": "hard_interrupt", "message": event["message"]}
                return
            elif event["type"] == "error":
                yield {"type": "error", "error": event["error"]}
                return
            elif event["type"] == "done":
                final_content = event.get("content", "")
                transcript_size = len(self._transcript)
        yield {
            "type": "message_stop",
            "usage": usage_data,
            "stop_reason": stop_reason,
            "transcript_size": transcript_size,
            "content": final_content,
        }

    def persist_session(self) -> Result[str]:
        return self._session_persister.persist_session()

    def load_session(self, session_id: str) -> bool:
        return self._session_persister.load_session(session_id)

    def _clear_checkpoint(self) -> None:
        self._session_persister.clear_checkpoint()

    def _save_checkpoint(
        self,
        messages: list[Any],
        round_idx: int,
        prompt: str,
        used_tools: bool,
        total_input: int,
        total_output: int,
    ) -> None:
        self._session_persister.save_checkpoint(messages, round_idx, prompt, used_tools, total_input, total_output)

    def _load_checkpoint(self) -> dict[str, Any] | None:
        return self._session_persister.load_checkpoint()

    def resume_interrupted(self) -> Result[str]:
        from lingclaude.model.types import ModelMessage, MessageRole, ToolCall

        data = self._load_checkpoint()
        if data is None:
            return Result.fail("No checkpoint found for this session", code="NO_CHECKPOINT")

        prompt = data["prompt"]
        round_idx = data["round_idx"]
        used_tools = data["used_tools"]
        total_input = data.get("total_input", 0)
        total_output = data.get("total_output", 0)
        raw_messages = data.get("messages", [])
        saved_conversation = data.get("conversation", [])

        messages: list[ModelMessage] = []
        for rm in raw_messages:
            role = MessageRole(rm.get("role", "user"))
            tool_calls = None
            if rm.get("tool_calls"):
                tool_calls = tuple(
                    ToolCall(
                        id=tc["function"].get("id", ""),
                        name=tc["function"]["name"],
                        arguments=tc["function"]["arguments"],
                    )
                    for tc in rm["tool_calls"]
                    if "function" in tc
                )
            messages.append(ModelMessage(
                role=role,
                content=rm.get("content", ""),
                name=rm.get("name"),
                tool_call_id=rm.get("tool_call_id"),
                tool_calls=tool_calls,
            ))

        if saved_conversation:
            self._conversation = list(saved_conversation)

        tools = self._build_openai_tools()
        resolved_config, _ = self._resolve_model_config(prompt)
        response = None

        for ri in range(round_idx + 1, AGENT_MAX_TOOL_ROUNDS):
            result = self._provider.complete(
                tuple(messages), config=resolved_config, tools=tools,
            )
            if result.is_error:
                self._clear_checkpoint()
                return Result.fail(f"[Resume failed at round {ri}] {result.error}", code="RESUME_ERROR")

            response = result.data
            total_input += response.usage.input_tokens
            total_output += response.usage.output_tokens

            if not response.tool_calls:
                final_content = self._finalize_turn(
                    prompt, response.content, used_tools, total_input, total_output, resolved_config,
                )
                self._messages.append(prompt)
                self._messages.append(final_content)
                self._transcript.append(final_content)
                self._clear_checkpoint()
                self._append_to_session_history(prompt, final_content)
                self._learn_from_turn(prompt, final_content)
                return Result.ok(final_content)

            used_tools = True
            self._process_tool_calls(response.tool_calls, messages, content=response.content)
            self._save_checkpoint(messages, ri, prompt, used_tools, total_input, total_output)

        content = response.content if response and response.content else "[达到最大工具调用轮次]"
        final_content = self._finalize_turn(
            prompt, content, used_tools, total_input, total_output, resolved_config,
        )
        self._messages.append(prompt)
        self._messages.append(final_content)
        self._transcript.append(final_content)
        self._clear_checkpoint()
        return Result.ok(final_content)

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

    def _process_tool_calls(self, tool_calls: tuple, messages: list, content: str = "") -> None:
        from lingclaude.model.types import ModelMessage, MessageRole

        self._behavior = self._behavior.record_tool_calls(count=len(tool_calls))
        messages.append(ModelMessage(
            role=MessageRole.ASSISTANT,
            content=content,
            tool_calls=tool_calls,
        ))

        # T1-3: 并行工具执行 — 全部 is_concurrency_safe 且 >1 个时并行，否则顺序
        if len(tool_calls) > 1 and all(self._is_concurrency_safe(tc.name) for tc in tool_calls):
            self._process_tool_calls_parallel(tool_calls, messages)
            return

        for tc in tool_calls:
            self._dementia_detector.record_tool_call(tc.name, tc.arguments)
            tool_output = self._execute_tool_with_retry(tc.name, tc.arguments)
            if '"error"' in tool_output:
                self._behavior = self._behavior.record_tool_calls(count=0, errors=1)
                self._log_to_flywheel(
                    pattern_type="tool_error",
                    error_message=tool_output[:200],
                    tool_name=tc.name,
                )
            messages.append(ModelMessage(
                role=MessageRole.TOOL,
                content=tool_output,
                name=tc.name,
                tool_call_id=tc.id,
            ))

    def _process_tool_calls_parallel(self, tool_calls: tuple, messages: list) -> None:
        """T1-3: 并行执行一批 concurrency-safe 工具，按原顺序收集结果。

        T1-3 深化: 并行冲突检测 — 写工具不标记 is_concurrency_safe=True，
        此处兜底校验：若误标了写工具，自动降级为顺序执行并记录警告。
        """
        from concurrent.futures import ThreadPoolExecutor
        from lingclaude.model.types import ModelMessage, MessageRole
        from lingclaude.engine.verification_gate import WRITE_SCOPED_TOOLS

        # 检测是否存在并发写冲突
        write_tools = {tc.name for tc in tool_calls if tc.name in WRITE_SCOPED_TOOLS}
        if write_tools:
            logger.warning(
                "T1-3 并行冲突检测: 以下工具不应并发执行（已降级为顺序执行）: %s",
                write_tools,
            )
            # 降级为顺序执行
            for tc in tool_calls:
                self._process_single_tool_call(tc, messages)
            return

        def _run(tc: Any) -> tuple[Any, str]:
            # 写工具加锁序列化
            with self._write_lock:
                self._dementia_detector.record_tool_call(tc.name, tc.arguments)
                return tc, self._execute_tool_with_retry(tc.name, tc.arguments)

        results: list[tuple[Any, str]] = []
        with ThreadPoolExecutor(max_workers=min(len(tool_calls), 4)) as pool:
            futures = [pool.submit(_run, tc) for tc in tool_calls]
            for fut in futures:
                tc, tool_output = fut.result()
                results.append((tc, tool_output))

        for tc, tool_output in results:
            if '"error"' in tool_output:
                self._behavior = self._behavior.record_tool_calls(count=0, errors=1)
                self._log_to_flywheel(
                    pattern_type="tool_error",
                    error_message=tool_output[:200],
                    tool_name=tc.name,
                )
            messages.append(ModelMessage(
                role=MessageRole.TOOL,
                content=tool_output,
                name=tc.name,
                tool_call_id=tc.id,
            ))

    def _process_single_tool_call(self, tc: Any, messages: list) -> None:
        """T1-3: 执行单个工具调用（供降级路径复用）。"""
        from lingclaude.model.types import ModelMessage, MessageRole
        self._dementia_detector.record_tool_call(tc.name, tc.arguments)
        with self._write_lock:
            tool_output = self._execute_tool_with_retry(tc.name, tc.arguments)
        if '"error"' in tool_output:
            self._behavior = self._behavior.record_tool_calls(count=0, errors=1)
            self._log_to_flywheel(
                pattern_type="tool_error",
                error_message=tool_output[:200],
                tool_name=tc.name,
            )
        messages.append(ModelMessage(
            role=MessageRole.TOOL,
            content=tool_output,
            name=tc.name,
            tool_call_id=tc.id,
        ))

    def _call_model(self, prompt: str) -> str:
        decision = self._router.route(prompt)
        messages = self._build_messages(prompt)
        tools = self._build_openai_tools(query=prompt)
        resolved_config, decision = self._resolve_model_config(prompt)
        # LINGKERNEL_v1 D3: MV-1 上线 - 发模型前先落 log, 发完断言可重建
        mv1_seq = self._log_model_request(prompt, messages, tools)
        # D8 双点校验之一: pre-send fail-closed (不可重建则不发)
        if not self._pre_send_check(mv1_seq, messages):
            self._log_to_flywheel("mv1_pre_send_blocked", f"seq={mv1_seq} request blocked", tool_name="model_request_log")
            return "[MV-1 fail-closed] 请求未通过可重建校验，已阻止发送并记录违规。"
        used_tools = False
        response = None
        total_input = 0
        total_output = 0
        consecutive_failures = 0

        for round_idx in range(AGENT_MAX_TOOL_ROUNDS):
            result = self._provider.complete(
                tuple(messages), config=resolved_config, tools=tools,
            )
            # MV-1 断言: 实际发往模型的消息必须可从 log 重建
            self._assert_model_visible(mv1_seq, messages)
            if result.is_error:
                consecutive_failures += 1
                self._track_behavior(prompt, f"[模型调用失败] {result.error}", used_tools=False)
                if resolved_config:
                    pname = self._task_router.get_provider_name(resolved_config.api_key, resolved_config.base_url)
                    if pname:
                        self._task_router.record_error(pname)
                if consecutive_failures >= self.config.consecutive_failure_limit:
                    logger.warning(
                        "硬中断触发: 连续模型调用失败 %d 次，强制停止",
                        consecutive_failures,
                    )
                    self._log_to_flywheel("hard_interrupt", f"连续模型调用失败 {consecutive_failures} 次", tool_name="provider")
                    return f"[硬中断] 连续模型调用失败 {consecutive_failures} 次，自动停止。请检查模型服务状态。"
                continue

            response = result.data
            total_input += response.usage.input_tokens
            total_output += response.usage.output_tokens
            if resolved_config:
                pname = self._task_router.get_provider_name(resolved_config.api_key, resolved_config.base_url)
                if pname:
                    self._task_router.record_success(pname)

            if not response.tool_calls:
                content = response.content
                if self._should_hallucination_correct(prompt, used_tools):
                    content = self._hallucination_correction(messages, content, tools, resolved_config)
                    if content:
                        return self._finalize_turn(prompt, content, used_tools, total_input, total_output, resolved_config)
                return self._finalize_turn(prompt, response.content, used_tools, total_input, total_output, resolved_config)

            used_tools = True
            self._process_tool_calls(response.tool_calls, messages, content=response.content)

            round_error_count = sum(
                1 for tc in response.tool_calls
                if '"error"' in self._get_last_tool_output(messages, tc.id)
            )
            if round_error_count == len(response.tool_calls) and round_error_count > 0:
                consecutive_failures += 1
                if consecutive_failures >= self.config.consecutive_failure_limit:
                    logger.warning(
                        "硬中断触发: 连续工具失败 %d 次，强制停止",
                        consecutive_failures,
                    )
                    self._log_to_flywheel("hard_interrupt", f"连续工具失败 {consecutive_failures} 次", tool_name="tool_loop")
                    content = response.content or ""
                    return self._finalize_turn(
                        prompt,
                        content + f"\n[硬中断] 连续工具调用失败 {consecutive_failures} 次，自动停止。",
                        used_tools, total_input, total_output, resolved_config,
                    )
            else:
                consecutive_failures = 0

        content = response.content if response and response.content else "[达到最大工具调用轮次]"
        return self._finalize_turn(prompt, content, used_tools, total_input, total_output, resolved_config)

    def _log_model_request(self, prompt: str, messages: list, tools: Any) -> int:
        """MV-1 L-a: model-visible means logged. 返回 seq 供事后断言。"""
        from lingclaude.core.model_request_log import check_model_visible_invariant  # noqa: F401
        try:
            tool_names = tuple(
                t.get("function", {}).get("name", "")
                for t in (tools or [])
                if isinstance(t, dict)
            )
            snapshot = [
                m if isinstance(m, dict) else getattr(m, "to_dict", lambda: {"role": str(getattr(m, "role", "user")), "content": str(getattr(m, "content", ""))})()
                for m in messages
            ]
            from datetime import datetime, timezone as _tz
            ev = self.model_request_log.append(
                prompt=prompt,
                messages=snapshot,
                tool_names=tool_names,
                timestamp=datetime.now(_tz.utc).isoformat(),
            )
            return ev.seq
        except Exception as e:
            logger.warning("model_request_log append failed: %s", e)
            return -1

    def _assert_model_visible(self, seq: int, messages: list) -> None:
        """MV-1 断言: derive(log.prefix(seq)) == 实际发送。违反记入告警列表。

        D8: 双点校验的 post-send 检测点 (审计角色) + MV-1b fold 校验。
        """
        from lingclaude.core.model_request_log import (
            check_model_visible_invariant,
            check_provenance_integrity,
            Mv1Violation,
        )
        if seq < 0:
            return
        try:
            snapshot = [
                m if isinstance(m, dict) else getattr(m, "to_dict", lambda: {"role": str(getattr(m, "role", "user")), "content": str(getattr(m, "content", ""))})()
                for m in messages
            ]
            from datetime import datetime, timezone as _tz
            ts = datetime.now(_tz.utc).isoformat()
            ok_a, reason_a = check_model_visible_invariant(self.model_request_log, seq, snapshot)
            if not ok_a:
                v = Mv1Violation(seq=seq, reason=reason_a, timestamp=ts)
                self._mv1_violations.append(v)
                logger.warning("MV-1a violated: %s", reason_a)
            ok_b, reason_b = check_provenance_integrity(self.model_request_log, seq, snapshot)
            if not ok_b:
                v = Mv1Violation(seq=seq, reason=reason_b, timestamp=ts)
                self._mv1_violations.append(v)
                logger.warning("MV-1b violated: %s", reason_b)
        except Exception as e:
            logger.warning("MV-1 assert failed to run: %s", e)

    def _pre_send_check(self, seq: int, messages: list) -> bool:
        """D8 双点校验的 pre-send fail-closed 点 (灵研 spec-review R2 处置)。

        发送前断言可重建; 失败 → 不发该请求 (fail-closed), 返回 False。
        供 _call_model / stream_call_model 在 provider.complete 前调用。
        """
        from lingclaude.core.model_request_log import check_model_visible_invariant, Mv1Violation
        if seq < 0:
            return True  # log append 失败时不阻断主流程 (旧行为兼容)
        try:
            snapshot = [
                m if isinstance(m, dict) else getattr(m, "to_dict", lambda: {"role": str(getattr(m, "role", "user")), "content": str(getattr(m, "content", ""))})()
                for m in messages
            ]
            ok, reason = check_model_visible_invariant(self.model_request_log, seq, snapshot)
            if not ok:
                from datetime import datetime, timezone as _tz
                v = Mv1Violation(
                    seq=seq, reason=f"[pre-send blocked] {reason}",
                    timestamp=datetime.now(_tz.utc).isoformat(),
                )
                self._mv1_violations.append(v)
                logger.warning("MV-1 pre-send blocked: %s", reason)
                return False
            return True
        except Exception as e:
            logger.warning("MV-1 pre-send check failed to run: %s", e)
            return True  # 检查器异常不阻断 (fail-open on checker, 审计已记录)

    @property
    def mv1_violations(self) -> tuple[str, ...]:
        """MV-1 违规记录 (兼容元组接口, 供灵信 invariant 框架 / LACP 验收消费)。"""
        return tuple(v.to_tuple_str() for v in self._mv1_violations)

    @property
    def mv1_violations_structured(self) -> tuple:
        """D8: 结构化违规记录 (seq/reason/timestamp), 灵信 L-b 按 seq 归因用。"""
        return tuple(self._mv1_violations)

    def stream_call_model(self, prompt: str) -> Generator[dict[str, Any], None, None]:
        from lingclaude.model.types import ModelMessage, MessageRole, ModelUsage, ToolCall

        messages = self._build_messages(prompt)
        tools = self._build_openai_tools()
        resolved_config, _ = self._resolve_model_config(prompt)
        used_tools = False
        response_content = ""
        total_input = 0
        total_output = 0
        consecutive_failures = 0

        for round_idx in range(AGENT_MAX_TOOL_ROUNDS):
            round_text_parts: list[str] = []
            round_tool_calls: list[ToolCall] = []
            stream_error: str | None = None

            for event in self._provider.stream_complete(
                tuple(messages), config=resolved_config, tools=tools,
            ):
                if event["type"] == "text_delta":
                    round_text_parts.append(event["text"])
                    yield {"type": "text_delta", "text": event["text"]}
                elif event["type"] == "tool_call_complete":
                    tc = ToolCall(
                        id=event["id"],
                        name=event["name"],
                        arguments=event["arguments"],
                    )
                    round_tool_calls.append(tc)
                elif event["type"] == "finish":
                    total_input += event.get("usage", ModelUsage()).input_tokens
                    total_output += event.get("usage", ModelUsage()).output_tokens
                elif event["type"] == "error":
                    stream_error = event["error"]

            if stream_error is not None:
                consecutive_failures += 1
                self._track_behavior(prompt, f"[模型调用失败] {stream_error}", used_tools=False)
                if consecutive_failures >= self.config.consecutive_failure_limit:
                    logger.warning(
                        "硬中断触发(stream): 连续模型调用失败 %d 次，强制停止",
                        consecutive_failures,
                    )
                    yield {
                        "type": "hard_interrupt",
                        "message": f"连续模型调用失败 {consecutive_failures} 次，自动停止。请检查模型服务状态。",
                    }
                    return
                yield {"type": "error", "error": stream_error}
                return

            round_content = "".join(round_text_parts)

            if not round_tool_calls:
                content = round_content
                if self._should_hallucination_correct(prompt, used_tools):
                    yield {"type": "status", "message": "幻觉闭环修正中..."}
                    corrected = self._hallucination_correction(
                        messages, content, tools, resolved_config,
                    )
                    if corrected:
                        yield {"type": "text_delta", "text": corrected}
                        content = corrected
                final_content = self._finalize_turn(prompt, content, used_tools, total_input, total_output, resolved_config)
                self._append_to_session_history(prompt, final_content)
                self._learn_from_turn(prompt, final_content)
                yield {"type": "done", "content": final_content}
                return

            used_tools = True
            self._behavior = self._behavior.record_tool_calls(count=len(round_tool_calls))
            messages.append(ModelMessage(
                role=MessageRole.ASSISTANT,
                content=round_content,
                tool_calls=tuple(round_tool_calls),
            ))

            round_error_count = 0
            for tc in round_tool_calls:
                yield {"type": "tool_call_start", "name": tc.name, "arguments": tc.arguments}
                tool_output = self._execute_tool_with_retry(tc.name, tc.arguments)
                is_error = '"error"' in tool_output
                if is_error:
                    round_error_count += 1
                    self._behavior = self._behavior.record_tool_calls(count=0, errors=1)
                    self._log_to_flywheel(
                        pattern_type="tool_error",
                        error_message=tool_output[:200],
                        tool_name=tc.name,
                    )
                preview = tool_output[:200] if len(tool_output) > 200 else tool_output
                yield {
                    "type": "tool_call_end",
                    "name": tc.name,
                    "output_preview": preview,
                    "is_error": is_error,
                }
                messages.append(ModelMessage(
                    role=MessageRole.TOOL,
                    content=tool_output,
                    name=tc.name,
                    tool_call_id=tc.id,
                ))

            if round_error_count == len(round_tool_calls) and round_error_count > 0:
                consecutive_failures += 1
                if consecutive_failures >= self.config.consecutive_failure_limit:
                    logger.warning(
                        "硬中断触发(stream): 连续工具失败 %d 次，强制停止",
                        consecutive_failures,
                    )
                    yield {
                        "type": "hard_interrupt",
                        "message": f"连续工具调用失败 {consecutive_failures} 次，自动停止。",
                    }
                    return
            else:
                consecutive_failures = 0

        content = response_content or "[达到最大工具调用轮次]"
        final_content = self._finalize_turn(prompt, content, used_tools, total_input, total_output, resolved_config)
        yield {"type": "done", "content": final_content}

    def _should_hallucination_correct(self, prompt: str, used_tools: bool) -> bool:
        bm = self._behavior
        if bm.hallucination_risk < 0.3:
            return False
        if used_tools:
            return False
        intent = detect_intent(prompt)
        return is_tool_intent(intent)

    def _hallucination_correction(
        self,
        messages: list,
        original_response: str,
        tools: tuple[dict[str, Any], ...] | None,
        config: Any,
        depth: int = 0,
    ) -> str | None:
        MAX_CORRECTION_DEPTH = 2
        if depth >= MAX_CORRECTION_DEPTH:
            logger.warning("幻觉闭环达到最大递归深度，放弃修正")
            return None

        from lingclaude.model.types import ModelMessage, MessageRole

        bm = self._behavior
        logger.info(
            "幻觉闭环触发: risk=%.0f%%, turns=%d, depth=%d",
            bm.hallucination_risk * 100,
            bm.total_turns,
            depth,
        )

        correction_prompt = (
            "⚠ 系统干预: 你的幻觉风险较高，但你刚才没有使用任何工具就直接回答了代码相关问题。"
            "这是不允许的。请立即使用 read/grep/glob 工具读取相关源码，然后基于工具结果重新回答。"
        )
        messages.append(ModelMessage(role=MessageRole.ASSISTANT, content=original_response))
        messages.append(ModelMessage(role=MessageRole.USER, content=correction_prompt))

        if self._provider is None or tools is None:
            return None

        result = self._provider.complete(tuple(messages), config=config, tools=tools)
        if result.is_error:
            return None

        response = result.data
        if response.tool_calls:
            for tc in response.tool_calls:
                tool_output = self._execute_tool_with_retry(tc.name, tc.arguments)
                messages.append(ModelMessage(
                    role=MessageRole.ASSISTANT,
                    content=response.content,
                    tool_calls=response.tool_calls,
                ))
                messages.append(ModelMessage(
                    role=MessageRole.TOOL,
                    content=tool_output,
                    name=tc.name,
                    tool_call_id=tc.id,
                ))
            final = self._provider.complete(tuple(messages), config=config, tools=tools)
            if final.is_ok and final.data.content:
                self._behavior = self._behavior.record_tool_calls(count=len(response.tool_calls))
                return final.data.content
            return None

        return response.content if response.content else None

    def _build_openai_tools(self, query: str = "") -> tuple[dict[str, Any], ...] | None:
        if self._runtime is None:
            return None
        tool_defs = list(self._runtime.registry.list_tools())

        mcp_defs = self._build_mcp_tool_defs()
        if mcp_defs:
            tool_defs.extend(mcp_defs)

        # T0-1: plan 模式下过滤模型可见工具列表（只留读域 + plan_mode 自身）
        # is True 严格判定 — MagicMock runtime 的自动属性是 Mock 而非 bool，不能触发过滤
        plan_mode = getattr(self._runtime, "plan_mode", None)
        if plan_mode is not None and getattr(plan_mode, "is_active", False) is True:
            tool_defs = plan_mode.filter_tools(tool_defs)

        if not tool_defs:
            return None
        if not query or len(tool_defs) <= ToolRouter.MAX_TOOLS_PER_REQUEST:
            return tuple(
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": {
                        "type": "object",
                        "properties": {k: v for k, v in t.parameters.items()},
                        # T0-8: MCP 工具带显式 required 列表时用它；空 = 全部 required（旧行为）
                        "required": (
                            list(t.required_params)
                            if getattr(t, "required_params", ())
                            else list(t.parameters.keys())
                        ),
                    },
                }
                for t in tool_defs
            )
        result = self._tool_router.route(query, tool_defs)
        logger.info(
            "ToolRouter: %d/%d tools selected for query (categories: %s)",
            result.selected_count, result.total_available,
            ", ".join(c.value for c in result.categories),
        )
        return result.tools

    def _build_mcp_tool_defs(self) -> list[Any]:
        from lingclaude.engine.tools import ToolDefinition

        self._ensure_mcp()
        mcp_names = mcp_proxy.list_all_tools()
        if not mcp_names:
            return []

        native_names = {t.name for t in self._runtime.registry.list_tools()}
        server_map: dict[str, str] = {}
        for info in mcp_proxy.list_servers():
            for t in info.tools:
                if t not in server_map:
                    server_map[t] = info.name

        defs: list[ToolDefinition] = []
        for name in mcp_names:
            if name in native_names:
                continue
            server_name = server_map.get(name, "unknown")
            # T0-8: 注入参数 schema — 优先 FastMCP Tool.parameters，其次函数签名推导
            try:
                props, required = mcp_proxy.get_tool_schema(name)
            except Exception:
                props, required = {}, []
            defs.append(ToolDefinition(
                name=name,
                description=f"[MCP:{server_name}] {name}",
                parameters=dict(props),
                required_params=tuple(required),
            ))
        return defs

    def _ensure_mcp(self) -> None:
        if self._mcp_initialized:
            return
        self._mcp_initialized = True
        try:
            mcp_proxy.init_from_lingflow_registry()
        except Exception:
            logger.debug("MCP proxy registry init skipped")
        # T1-5 深化: 批量注册 LACP manifest 中声明 MCP transport 的插件
        try:
            from lingclaude.lacp.manifest import scan_and_register_mcp_plugins
            # 扫描 manifest 目录并注册（不阻塞主流程，失败静默）
            count = scan_and_register_mcp_plugins([])
            if count > 0:
                logger.info("T1-5: registered %d MCP servers from LACP manifests", count)
        except Exception as e:
            logger.debug("LACP MCP manifest scan skipped: %s", e)
        # T1-5 深化: tools/list 发现 — 对 stdio/http 传输连接并发现工具名
        self._discover_mcp_tools()

    def _discover_mcp_tools(self) -> None:
        """T1-5 深化: 对 stdio/http MCP server 执行 tools/list 发现，填充 server.tools。

        仅对有 tools=() 的空 server 执行发现（避免重复调用）。
        发现失败不阻塞主流程，仅记录警告。
        """
        from lingclaude.engine.mcp_client import discover_and_register
        import threading

        empty_servers = [
            s for s in mcp_proxy.list_servers()
            if s.transport in ("stdio", "http") and not s.tools
        ]
        if not empty_servers:
            return

        def _discover_one(server_key: str, command: tuple[str, ...], url: str | None, cwd: str | None) -> None:
            try:
                if command:
                    success, names, schemas = discover_and_register(
                        key=server_key,
                        name=f"mcp-{server_key}",
                        transport="stdio",
                        command=list(command),
                        cwd=cwd,
                    )
                elif url:
                    success, names, schemas = discover_and_register(
                        key=server_key,
                        name=f"mcp-{server_key}",
                        transport="http",
                        url=url,
                    )
                else:
                    return
                if success:
                    # T1-5 深化: 将 inputSchema 写入 server.tool_schemas
                    from lingclaude.engine import mcp_proxy
                    server = mcp_proxy._SERVERS.get(server_key)
                    if server is not None:
                        server.tool_schemas.update(schemas)
                        # 更新 tools 列表
                        server.tools = tuple(names)
                    logger.info("MCP tools/list discovered %d tools for %s", len(names), server_key)
            except Exception as e:
                logger.warning("MCP tools/list discovery failed for %s: %s", server_key, e)

        # 并行发现（limit 3 避免并发过多）
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(3, len(empty_servers))) as pool:
            futures = []
            for s in empty_servers:
                fut = pool.submit(_discover_one, s.key, s.command, s.url, s.working_dir)
                futures.append(fut)
            for fut in concurrent.futures.as_completed(futures, timeout=30):
                try:
                    fut.result()
                except Exception:
                    pass

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
