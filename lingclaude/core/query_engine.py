from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator
from uuid import uuid4

from lingclaude.core.models import PermissionDenial, UsageSummary
from lingclaude.core.session import Session, SessionManager
from lingclaude.core.behavior import BehaviorMetrics, Emotion, Intent, detect_emotion, detect_intent, is_tool_intent
from lingclaude.core.intel import IntelCollector, DailyDigest, DailyDigestGenerator, IntelRelay
from lingclaude.core.prior_verifier import PriorVerifier
from lingclaude.core.meta_cognition import MetaCognition, Domain
from lingclaude.core.layered_memory import LayeredMemory, Experience, EmotionIntensity
from lingclaude.model.intelligent_router import IntelligentRouter
from lingclaude.engine.tool_router import ToolRouter, create_default_router
from lingclaude.engine import mcp_proxy
from lingclaude.core.context_cache import ContextCache
from lingclaude.core.task_aggregation import TaskAggregator, TaskPriority
from lingclaude.core.token_monitor import TokenMonitor
from lingclaude.core.dementia_detector import DementiaDetector
from lingclaude.core.context_compression import compress_messages, CompressionConfig, CompressionLevel
from lingclaude.core.hooks import HookManager, HookType, HookContext
from lingclaude.core.cognitive_rhythm import CognitiveRhythm, ImbalanceType
from lingclaude.model.types import ModelConfig

from lingclaude.core.types import Result, StopReason

logger = logging.getLogger(__name__)

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


@dataclass(frozen=True)
class TurnResult:
    prompt: str
    output: str
    matched_commands: tuple[str, ...]
    matched_tools: tuple[str, ...]
    permission_denials: tuple[PermissionDenial, ...]
    usage: UsageSummary
    stop_reason: StopReason


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
        self._mailbox: Any | None = None
        self._router = IntelligentRouter()
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
        self._hooks = HookManager()

    def init_mailbox(self, mailbox: Any) -> None:
        self._mailbox = mailbox

    def read_lingmessage_threads(self) -> tuple[Any, ...]:
        if self._mailbox is None:
            return ()
        return self._mailbox.list_threads()

    def notify_completion(self, task: str, result_summary: str, channel: str = "ecosystem") -> None:
        if self._mailbox is None:
            return
        try:
            self._mailbox.open_thread(
                sender="LINGCLAUDE",
                recipients=["ALL"],
                channel=channel,
                topic=f"工作完成: {task[:50]}",
                subject=f"灵克完成: {task}",
                body=result_summary,
            )
        except Exception as e:
            logger.warning("灵信工作完成通知失败: %s", e)

    def notify_risk(self, risk_type: str, details: str, severity: str = "warning") -> None:
        if self._mailbox is None:
            return
        try:
            self._mailbox.open_thread(
                sender="LINGCLAUDE",
                recipients=["ALL"],
                channel="ecosystem",
                topic=f"风险预警: {risk_type}",
                subject=f"[{severity.upper()}] 灵克风险预警: {risk_type}",
                body=details,
            )
        except Exception as e:
            logger.warning("灵信风险预警失败: %s", e)

    def notify_vote(self, proposal: str, options: list[str], deadline_hours: int = 48) -> None:
        if self._mailbox is None:
            return
        try:
            body = f"提案: {proposal}\n\n选项:\n"
            for i, opt in enumerate(options, 1):
                body += f"  {i}. {opt}\n"
            body += f"\n截止时间: {deadline_hours}小时后"
            self._mailbox.open_thread(
                sender="LINGCLAUDE",
                recipients=["ALL"],
                channel="ecosystem",
                topic=f"灵委会投票: {proposal[:50]}",
                subject=f"[投票] {proposal}",
                body=body,
            )
        except Exception as e:
            logger.warning("灵信投票通知失败: %s", e)

    @property
    def behavior_metrics(self) -> BehaviorMetrics:
        return self._behavior

    @property
    def layered_memory(self) -> LayeredMemory:
        return self._layered_memory

    @property
    def has_checkpoint(self) -> bool:
        if self._active_checkpoint and self._active_checkpoint.exists():
            return True
        cp_path = CHECKPOINT_DIR / f"{self.session_id}.json"
        return cp_path.exists()

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

        pre_ctx = HookContext(
            hook_type=HookType.PRE_TASK,
            session_id=self.session_id,
            prompt=prompt,
            metadata={"matched_commands": list(matched_commands), "matched_tools": list(matched_tools)},
        )
        pre_result = self._hooks.trigger(pre_ctx)
        if pre_result.modified_context:
            prompt = pre_result.modified_context.prompt or prompt

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
        self._append_to_session_history(prompt, output)
        self._learn_from_turn(prompt, output)

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
        session = Session(
            session_id=self.session_id,
            messages=tuple(self._messages),
            input_tokens=self._usage.input_tokens,
            output_tokens=self._usage.output_tokens,
        )
        result = self.session_manager.save(session)
        if result.is_error:
            return result  # type: ignore[return-value]
        return Result.ok(str(result.data))

    def load_session(self, session_id: str) -> bool:
        result = self.session_manager.load(session_id)
        if result.is_error:
            return False
        session = result.data
        self.session_id = session.session_id
        self._messages = list(session.messages)
        self._usage = UsageSummary(session.input_tokens, session.output_tokens)
        self._transcript = list(session.messages)
        self._conversation.clear()
        for i in range(0, len(session.messages) - 1, 2):
            user_msg = session.messages[i]
            asst_msg = session.messages[i + 1] if i + 1 < len(session.messages) else ""
            self._conversation.append(("user", user_msg))
            self._conversation.append(("assistant", asst_msg))
        return True

    def _clear_checkpoint(self) -> None:
        if self._active_checkpoint and self._active_checkpoint.exists():
            try:
                self._active_checkpoint.unlink()
            except OSError:
                pass
            self._active_checkpoint = None

    def _save_checkpoint(
        self,
        messages: list[Any],
        round_idx: int,
        prompt: str,
        used_tools: bool,
        total_input: int,
        total_output: int,
    ) -> None:
        try:
            CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
            cp_path = CHECKPOINT_DIR / f"{self.session_id}.json"
            serialized: list[dict[str, Any]] = []
            for msg in messages:
                d = msg.to_dict()
                serialized.append(d)
            data = {
                "session_id": self.session_id,
                "prompt": prompt,
                "round_idx": round_idx,
                "used_tools": used_tools,
                "total_input": total_input,
                "total_output": total_output,
                "messages": serialized,
                "conversation": list(self._conversation),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            cp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._active_checkpoint = cp_path
            logger.info("Checkpoint saved: session=%s round=%d", self.session_id, round_idx)
        except Exception as e:
            logger.warning("Checkpoint save failed: %s", e)

    def _load_checkpoint(self) -> dict[str, Any] | None:
        cp_path = self._active_checkpoint or (CHECKPOINT_DIR / f"{self.session_id}.json")
        if not cp_path.exists():
            return None
        try:
            data = json.loads(cp_path.read_text(encoding="utf-8"))
            if data.get("session_id") != self.session_id:
                return None
            self._active_checkpoint = cp_path
            logger.info("Checkpoint loaded: session=%s round=%d", self.session_id, data.get("round_idx", 0))
            return data
        except Exception as e:
            logger.warning("Checkpoint load failed: %s", e)
            return None

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
        for tc in tool_calls:
            self._dementia_detector.record_tool_call(tc.name, tc.arguments)
            tool_output = self._execute_tool_with_retry(tc.name, tc.arguments)
            if '"error"' in tool_output:
                self._behavior = self._behavior.record_tool_calls(count=0, errors=1)
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
        used_tools = False
        response = None
        total_input = 0
        total_output = 0
        consecutive_failures = 0

        for round_idx in range(AGENT_MAX_TOOL_ROUNDS):
            result = self._provider.complete(
                tuple(messages), config=resolved_config, tools=tools,
            )
            if result.is_error:
                consecutive_failures += 1
                self._track_behavior(prompt, f"[模型调用失败] {result.error}", used_tools=False)
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
                        "required": list(t.parameters.keys()),
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
            defs.append(ToolDefinition(
                name=name,
                description=f"[MCP:{server_name}] {name}",
                parameters={},
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

    def _execute_tool(self, name: str, arguments_json: str) -> str:
        try:
            kwargs = json.loads(arguments_json)
        except json.JSONDecodeError:
            return json.dumps({"error": f"Invalid JSON arguments: {arguments_json}"}, ensure_ascii=False)

        if name == "read" and "path" in kwargs:
            try:
                content, cache_hit = self._cache.read_file(kwargs["path"])
                is_dup = self._monitor.record_file_read(kwargs["path"], content)
                self._dementia_detector.record_file_read(kwargs["path"])
                if cache_hit:
                    self._session_cache_hits += 1
                    logger.debug("ContextCache hit for %s (duplicate=%s)", kwargs["path"], is_dup)
                return json.dumps({"content": content, "cache_hit": cache_hit}, ensure_ascii=False, default=str)
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.debug("Cache read failed, falling back to tool: %s", e)

        try:
            result = self._runtime.execute_tool(name, **kwargs)
            if hasattr(result, 'is_error'):
                from lingclaude.core.types import Result as _R
                if isinstance(result, _R):
                    if result.is_error:
                        return json.dumps({"error": result.error}, ensure_ascii=False)
                    result = result.data if result.is_ok else {"error": result.error}
            if "error" in result and result["error"] and "not found" in str(result["error"]).lower():
                return self._execute_mcp_tool(name, kwargs)
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            logger.warning("Tool execution failed: %s.%s -> %s", name, kwargs.keys(), e)
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _execute_mcp_tool(self, name: str, kwargs: dict[str, Any]) -> str:
        self._ensure_mcp()
        result = mcp_proxy.call_tool(name, **kwargs)
        if result.is_error:
            return json.dumps({"error": result.error}, ensure_ascii=False)
        data = result.data
        if data.success:
            return json.dumps(data.output if isinstance(data.output, dict) else {"result": data.output}, ensure_ascii=False, default=str)
        return json.dumps({"error": data.error or "MCP tool call failed"}, ensure_ascii=False)

    def _compact_if_needed(self) -> None:
        msg_limit = self.config.compact_after_turns * 2
        if len(self._messages) > msg_limit:
            self._hooks.trigger(HookContext(
                hook_type=HookType.PRE_COMPACT,
                session_id=self.session_id,
                metadata={"message_count": len(self._messages), "limit": msg_limit},
            ))
            self._archive_dropped_messages(
                (len(self._messages) - (self.config.compact_after_turns // 2) * 2) // 2
            )
            result = compress_messages(
                self._messages,
                config=CompressionConfig(
                    max_messages=(self.config.compact_after_turns // 2) * 2,
                    level=CompressionLevel.SUMMARY,
                ),
            )
            self._messages[:] = result.compressed_messages
            logger.info(
                "Context compressed: dropped=%d, saved~%d tokens, facts=%d",
                result.dropped_count, result.tokens_estimated_saved, result.archived_facts,
            )
            self._hooks.trigger(HookContext(
                hook_type=HookType.POST_COMPACT,
                session_id=self.session_id,
                metadata={
                    "dropped": result.dropped_count,
                    "saved_tokens": result.tokens_estimated_saved,
                    "archived_facts": result.archived_facts,
                },
            ))
        conv_limit = self.config.compact_after_turns * 2
        if len(self._conversation) > conv_limit:
            conv_msgs = [{"role": r, "content": c} for r, c in self._conversation]
            conv_result = compress_messages(
                conv_msgs,
                config=CompressionConfig(
                    max_messages=conv_limit,
                    level=CompressionLevel.SUMMARY,
                ),
            )
            summary_lines: list[tuple[str, str]] = [("system", conv_result.summary_text)]
            kept_pairs = [
                (m["role"], m["content"])
                for m in conv_result.compressed_messages
                if isinstance(m, dict) and "role" in m
            ]
            self._conversation[:] = summary_lines + kept_pairs
        if len(self._transcript) > self.config.compact_after_turns:
            transcript_msgs = [{"content": t} for t in self._transcript]
            tr_result = compress_messages(
                transcript_msgs,
                config=CompressionConfig(
                    max_messages=self.config.compact_after_turns,
                    level=CompressionLevel.TRUNCATE,
                ),
            )
            self._transcript[:] = [
                m.get("content", "") if isinstance(m, dict) else str(m)
                for m in tr_result.compressed_messages
                if not (isinstance(m, str) and m.startswith("[前"))
            ]

    def _archive_dropped_messages(self, dropped_count: int) -> None:
        if dropped_count <= 0 or len(self._conversation) < 2:
            return
        dropped = self._conversation[:dropped_count * 2]
        files_seen: list[str] = []
        decisions: list[str] = []
        errors_seen: list[str] = []
        for role, text in dropped:
            if role == "assistant":
                for line in text.split("\n"):
                    l = line.strip().lower()
                    if any(k in l for k in ("决定", "选择", "采用", "decided", "chose", "方案")):
                        decisions.append(line.strip()[:200])
                    if any(k in l for k in ("错误", "失败", "error", "failed", "不对")):
                        errors_seen.append(line.strip()[:200])
            elif role == "user":
                for line in text.split("\n"):
                    l = line.strip().lower()
                    if any(k in l for k in ("read", "读取", "查看", "cat ", "view ")):
                        for word in line.strip().split():
                            if ".py" in word or ".js" in word or ".ts" in word or ".md" in word or ".yaml" in word or ".json" in word:
                                cleaned = word.strip('`"\'*,;:()[]')
                                if cleaned and cleaned not in files_seen:
                                    files_seen.append(cleaned)
        if files_seen or decisions or errors_seen:
            parts: list[str] = []
            if files_seen:
                parts.append(f"已读文件: {', '.join(files_seen[:20])}")
            if decisions:
                parts.append(f"已做决策: {'; '.join(decisions[:5])}")
            if errors_seen:
                parts.append(f"已遇错误: {'; '.join(errors_seen[:5])}")
            try:
                self._layered_memory.experience.store(
                    Experience.create(
                        problem=f"会话压缩归档(丢弃{dropped_count}轮)",
                        hypothesis="",
                        action="压缩前自动归档",
                        result=" | ".join(parts),
                        reflection="",
                        emotion=EmotionIntensity.MEDIUM,
                    ),
                )
            except Exception as e:
                logger.debug("归档到LayeredMemory失败: %s", e)

    def _resolve_model_config(self, prompt: str) -> tuple[ModelConfig | None, Any]:
        if self._model_config is None:
            return None, None
        from lingclaude.core.behavior import Intent

        cfg = self._model_config
        router = self._model_router
        target_model = cfg.model
        target_temp = cfg.temperature

        # Only use router if explicitly enabled and configured
        if router and router.enabled:
            decision = self._router.route(prompt)
            if router.code_model or router.chat_model:
                intent = detect_intent(prompt)
                is_code = intent in (Intent.CODE_QUESTION, Intent.BUG_REPORT, Intent.OPTIMIZATION_REQUEST)
                legacy_model = router.code_model if is_code else router.chat_model
                if legacy_model:
                    target_model = legacy_model

                self._aggregator.add_task(
                    query=prompt,
                    task_type=str(decision.task_type.value),
                    priority=TaskPriority.MEDIUM,
                )

        # Dynamic mid-session adjustments
        bm = self._behavior
        if bm.total_turns > 3:
            # High hallucination risk → lower temperature for more deterministic output
            if bm.hallucination_risk > 0.4:
                target_temp = min(target_temp, 0.3)
            # High frustration → lower temperature, be more conservative
            if bm.frustration_rate > 0.3:
                target_temp = min(target_temp, 0.2)
            # High error rate → switch to more capable model if available
            if bm.tool_error_rate > 0.4 and router and router.code_model:
                target_model = router.code_model

        # Dementia → force lower temperature to reduce random exploration
        diag = self._dementia_detector.diagnose()
        if diag.dementia_index > 0.3:
            target_temp = min(target_temp, 0.1)

        config = ModelConfig(
            model=target_model,
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            max_tokens=cfg.max_tokens,
            temperature=target_temp,
            system_prompt="",
        )
        return config, None

    def _build_adaptive_system_prompt(self) -> str:
        base = (
            "你是灵克，一个会自我进化的开源 AI 编程助手。\n"
            "\n"
            "核心规则:\n"
            "1. 先判断用户意图：只有涉及具体代码、文件、项目结构的问题才需要调用工具。\n"
            "2. 一般性对话、观点讨论、概念解释等非代码问题，直接回答，不要调用工具。\n"
            "3. 回答代码相关问题时，必须先用工具（read/grep/glob）读取源码，不要猜测。\n"
            "4. 如果用户指出你胡说或没读代码，立即使用工具重新阅读相关文件。\n"
            "5. 你擅长代码理解、编辑、终端操作，并通过自优化持续提升能力。\n"
            "6. 用中文回答，代码保持原样。"
        )

        extras: list[str] = []
        bm = self._behavior

        memory_text = self._layered_memory.inject_common_to_prompt()
        if memory_text:
            extras.append("\n\n" + memory_text)

        meta_text = self._meta_cognition.get_system_prompt_injection()
        if meta_text:
            extras.append("\n\n" + meta_text)

        if bm.hallucination_risk > 0.3:
            extras.append(
                "\n⚠ 行为警告: 你近期幻觉风险较高({:.0%})。回答代码问题时必须先调用工具读取文件，绝对不能凭记忆猜测代码内容。一般性问题可以直接回答。".format(bm.hallucination_risk)
            )

        if bm.frustration_rate > 0.2:
            extras.append(
                "\n⚠ 用户状态: 用户近期频繁表现出沮丧({:.0%})。请格外仔细，回答代码问题前先读文件。".format(bm.frustration_rate)
            )

        if bm.tool_error_rate > 0.3:
            extras.append(
                "\n⚠ 工具问题: 近期工具调用失败率较高({:.0%})。请检查参数格式，确保文件路径正确。".format(bm.tool_error_rate)
            )

        if bm.corrections_received >= 2:
            extras.append(
                "\n⚠ 纠正记录: 已收到 {} 次用户纠正。请更加谨慎，确认信息准确后再回答。".format(bm.corrections_received)
            )

        if bm.total_turns > 2 and bm.tool_use_rate < 0.2:
            extras.append(
                "\n💡 提醒: 你近期工具使用率较低({:.0%})。面对代码相关问题请积极使用工具。".format(bm.tool_use_rate)
            )

        if bm.tool_error_count > 0:
            try:
                from lingclaude.core.data_flywheel import DataFlywheel
                fw = DataFlywheel()
                if fw.should_alert(threshold=0.5):
                    stats = fw.get_stats()
                    extras.append(
                        f"\n⚠ 错误复发: 错误复发率 {stats.recurrence_rate:.0%}，"
                        f"共 {stats.total_errors} 个错误，{stats.total_corrections} 个修复。"
                        "请避免重复已犯过的错误。"
                    )
                fw.close()
            except Exception:
                pass

        if self._session_cache_hits > 2:
            extras.append(
                f"\n📂 文件缓存: 本次会话已命中 {self._session_cache_hits} 次。"
                "已读文件不需要重复读取。"
            )

        diagnosis = self._dementia_detector.diagnose()
        if diagnosis.intervention_prompt:
            extras.append("\n\n" + diagnosis.intervention_prompt)

        project_index = self._project_index
        if project_index:
            pkg_summary = "\n".join(
                f"- {pkg}/: {', '.join(sorted(files[:5]))}"
                for pkg, files in sorted(project_index.items())
                if pkg != "."
            )
            if pkg_summary:
                extras.append(
                    "\n📁 当前项目结构:\n" + pkg_summary
                )

        return base + "".join(extras)

    def _execute_tool_with_retry(self, name: str, arguments_json: str) -> str:
        result = self._execute_tool(name, arguments_json)
        if '"error"' not in result:
            return result

        retry_args = self._fix_tool_arguments(name, arguments_json, result)
        if retry_args is not None:
            self._behavior = self._behavior.record_tool_calls(count=1)
            return self._execute_tool(name, retry_args)

        return result

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
                raw = json.loads(self._session_history_path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    history = raw
            history.append({
                "query": query[:200],
                "title": query[:80],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "session_id": self.session_id,
            })
            self._session_history_path.write_text(
                json.dumps(history, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Session history write failed: %s", e)

    def _learn_from_turn(self, prompt: str, response: str) -> None:
        try:
            from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase
            from lingclaude.self_optimizer.learner.models import (
                FeedbackCategory,
                LearnedRule,
                Pattern,
            )

            turn_num = len(self._messages) // 2
            bm = self._behavior

            if bm.hallucination_risk > 0.3:
                kb = KnowledgeBase()
                rule = LearnedRule(
                    id=f"hallucination_turn_{turn_num}_{self.session_id[:8]}",
                    name="幻觉风险检测",
                    description=f"第{turn_num}轮幻觉风险={bm.hallucination_risk:.0%}, query={prompt[:60]}",
                    category=FeedbackCategory.SECURITY,
                    pattern=Pattern(
                        context_keywords=("hallucination", prompt[:30]),
                        severity_distribution={"risk": bm.hallucination_risk},
                    ),
                    tools=("prior_verifier", "behavior"),
                    frequency=1,
                    confidence=0.7,
                    quality_score=max(0.1, 1.0 - bm.hallucination_risk),
                    status="active",
                )
                kb.add_rule(rule)
                kb.close()

            if bm.tool_error_count > 0:
                kb = KnowledgeBase()
                rule = LearnedRule(
                    id=f"tool_error_turn_{turn_num}_{self.session_id[:8]}",
                    name="工具错误记录",
                    description=f"第{turn_num}轮工具错误={bm.tool_error_count}, query={prompt[:60]}",
                    category=FeedbackCategory.BUG_RISK,
                    pattern=Pattern(
                        context_keywords=("tool_error", prompt[:30]),
                        severity_distribution={"errors": bm.tool_error_count},
                    ),
                    tools=("behavior",),
                    frequency=1,
                    confidence=0.8,
                    quality_score=0.5,
                    status="active",
                )
                kb.add_rule(rule)
                kb.close()

            if bm.corrections_received > 0:
                kb = KnowledgeBase()
                rule = LearnedRule(
                    id=f"correction_turn_{turn_num}_{self.session_id[:8]}",
                    name="用户纠正记录",
                    description=f"第{turn_num}轮用户纠正={bm.corrections_received}, query={prompt[:60]}",
                    category=FeedbackCategory.BEST_PRACTICE,
                    pattern=Pattern(
                        context_keywords=("correction", prompt[:30]),
                        severity_distribution={"count": bm.corrections_received},
                    ),
                    tools=("behavior",),
                    frequency=1,
                    confidence=0.9,
                    quality_score=0.6,
                    status="active",
                )
                kb.add_rule(rule)
                kb.close()

            if turn_num > 0 and turn_num % 5 == 0:
                kb = KnowledgeBase()
                rule = LearnedRule(
                    id=f"session_milestone_{turn_num}_{self.session_id[:8]}",
                    name=f"会话里程碑 #{turn_num}",
                    description=f"会话进行到第{turn_num}轮, 幻觉风险={bm.hallucination_risk:.0%}, 沮丧率={bm.frustration_rate:.0%}, 工具错误={bm.tool_error_count}",
                    category=FeedbackCategory.BEST_PRACTICE,
                    pattern=Pattern(
                        context_keywords=("milestone", str(turn_num)),
                        severity_distribution={
                            "hallucination_risk": bm.hallucination_risk,
                            "frustration_rate": bm.frustration_rate,
                        },
                    ),
                    tools=("behavior", "meta_cognition"),
                    frequency=1,
                    confidence=0.6,
                    quality_score=0.5,
                    status="active",
                )
                kb.add_rule(rule)
                kb.close()

        except Exception as e:
            logger.warning("知识库学习失败: %s", e)

    def _log_to_flywheel(
        self,
        pattern_type: str,
        error_message: str,
        tool_name: str = "",
        file_path: str = "",
        context: str = "",
    ) -> None:
        try:
            from lingclaude.core.data_flywheel import DataFlywheel, ErrorPattern

            flywheel = DataFlywheel()
            flywheel.log_error(ErrorPattern(
                pattern_type=pattern_type,
                file_path=file_path,
                error_message=error_message[:500],
                tool_name=tool_name,
                context=context[:200],
                session_id=self.session_id,
                occurred_at=datetime.now().isoformat(),
            ))
            flywheel.close()
        except Exception as e:
            logger.warning("飞轮记录失败: %s", e)

    def _collect_behavior_intel(self) -> None:
        self._intel_collector.from_behavior(self._behavior.to_dict())

    def _index_project(self) -> dict[str, Any]:
        if self._runtime is None:
            return {}
        if self._project_index:
            return self._project_index
        try:
            result = self._runtime.execute_tool("glob", pattern="**/*.py")
            if not isinstance(result, dict) or "files" not in result:
                return {}
            files = result.get("files", [])
            if not files:
                return {}
            structure: dict[str, list[str]] = {}
            for f in files:
                parts = Path(f).parts
                if len(parts) > 1:
                    pkg = parts[0]
                    structure.setdefault(pkg, []).append("/".join(parts[1:]))
                else:
                    structure.setdefault(".", []).append(parts[0])
            self._project_index = structure
            return structure
        except Exception:
            return {}

    def _format_output(self, lines: list[str]) -> str:
        if self.config.structured_output:
            return json.dumps({"summary": lines, "session_id": self.session_id}, indent=2, ensure_ascii=False)
        return "\n".join(lines)
