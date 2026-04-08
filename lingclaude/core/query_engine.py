from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Generator
from uuid import uuid4

from lingclaude.core.models import PermissionDenial, UsageSummary
from lingclaude.core.session import Session, SessionManager
from lingclaude.core.behavior import BehaviorMetrics, Emotion, Intent, detect_emotion, detect_intent, is_tool_intent
from lingclaude.core.intel import IntelCollector, DailyDigest, DailyDigestGenerator, IntelRelay
from lingclaude.model.intelligent_router import IntelligentRouter
from lingclaude.core.context_cache import ContextCache
from lingclaude.core.task_aggregation import TaskAggregator, TaskPriority
from lingclaude.core.token_monitor import TokenMonitor
from lingclaude.model.types import ModelConfig

from lingclaude.core.types import Result

logger = logging.getLogger(__name__)

AGENT_MAX_TOOL_ROUNDS = 10


class StopReason(str, Enum):
    COMPLETED = "completed"
    MAX_TURNS_REACHED = "max_turns_reached"
    MAX_BUDGET_REACHED = "max_budget_reached"
    ERROR = "error"


@dataclass(frozen=True)
class QueryEngineConfig:
    max_turns: int = 8
    max_budget_tokens: int = 200000
    compact_after_turns: int = 12
    structured_output: bool = False
    structured_retry_limit: int = 2


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
        self._cache = ContextCache(cache_size=100, ttl_hours=24)
        self._aggregator = TaskAggregator(max_group_size=5)
        self._monitor = TokenMonitor()

    def init_mailbox(self, mailbox: Any) -> None:
        self._mailbox = mailbox

    def read_lingmessage_threads(self) -> tuple[Any, ...]:
        if self._mailbox is None:
            return ()
        return self._mailbox.list_threads()

    @property
    def behavior_metrics(self) -> BehaviorMetrics:
        return self._behavior

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

        output = self._generate_response(prompt, matched_commands, matched_tools, denied_tools)

        projected = self._usage.add_turn(prompt, output)
        stop_reason = StopReason.COMPLETED
        if projected.input_tokens + projected.output_tokens > self.config.max_budget_tokens:
            stop_reason = StopReason.MAX_BUDGET_REACHED

        self._messages.append(prompt)
        self._messages.append(output)
        self._transcript.append(output)
        self._denials.extend(denied_tools)
        self._usage = projected
        self._compact_if_needed()
        self._append_to_session_history(prompt, output)

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
        result = self.submit(prompt, matched_commands, matched_tools, denied_tools)
        yield {"type": "message_delta", "text": result.output}
        yield {
            "type": "message_stop",
            "usage": result.usage.to_dict(),
            "stop_reason": result.stop_reason.value,
            "transcript_size": len(self._transcript),
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

    def reset(self) -> None:
        self.session_id = uuid4().hex[:16]
        self._messages.clear()
        self._conversation.clear()
        self._denials.clear()
        self._usage = UsageSummary()
        self._transcript.clear()

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

    def _call_model(self, prompt: str) -> str:
        from lingclaude.model.types import ModelMessage, MessageRole

        # Get routing decision
        decision = self._router.route(prompt)

        messages: list[ModelMessage] = []

        system_prompt = self._build_adaptive_system_prompt()
        if system_prompt:
            messages.append(ModelMessage(role=MessageRole.SYSTEM, content=system_prompt))

        for role, content in self._conversation:
            messages.append(ModelMessage(role=MessageRole(role), content=content))
        messages.append(ModelMessage(role=MessageRole.USER, content=prompt))

        tools = self._build_openai_tools()
        resolved_config, decision = self._resolve_model_config(prompt)
        used_tools = False
        response = None
        total_input = 0
        total_output = 0

        for round_idx in range(AGENT_MAX_TOOL_ROUNDS):
            result = self._provider.complete(
                tuple(messages), config=resolved_config, tools=tools,
            )
            if result.is_error:
                self._track_behavior(prompt, f"[模型调用失败] {result.error}", used_tools=False)
                return f"[模型调用失败] {result.error}"

            response = result.data
            total_input += response.usage.input_tokens
            total_output += response.usage.output_tokens

            if not response.tool_calls:
                content = response.content
                if self._should_hallucination_correct(prompt, used_tools):
                    content = self._hallucination_correction(messages, content, tools, resolved_config)
                    if content:
                        self._track_behavior(prompt, content, used_tools=used_tools)
                        self._conversation.append(("user", prompt))
                        self._conversation.append(("assistant", content))
                        return content
                self._track_behavior(prompt, response.content, used_tools=used_tools)
                self._usage = self._usage.add_usage(response.usage.input_tokens, response.usage.output_tokens)
                self._monitor.record_usage(
                    model=str(resolved_config.model) if resolved_config else "unknown",
                    task_type="unknown",
                    total_tokens=response.usage.input_tokens + response.usage.output_tokens,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                )
                self._conversation.append(("user", prompt))
                self._conversation.append(("assistant", response.content))
                return response.content

            used_tools = True
            self._behavior = self._behavior.record_tool_calls(count=len(response.tool_calls))

            messages.append(ModelMessage(
                role=MessageRole.ASSISTANT,
                content=response.content,
                tool_calls=response.tool_calls,
            ))

            for tc in response.tool_calls:
                tool_output = self._execute_tool_with_retry(tc.name, tc.arguments)
                if '"error"' in tool_output:
                    self._behavior = self._behavior.record_tool_calls(count=0, errors=1)
                messages.append(ModelMessage(
                    role=MessageRole.TOOL,
                    content=tool_output,
                    name=tc.name,
                    tool_call_id=tc.id,
                ))

        content = response.content if response and response.content else "[达到最大工具调用轮次]"
        self._track_behavior(prompt, content, used_tools=used_tools)
        if response:
            self._usage = self._usage.add_usage(total_input, total_output)
            self._monitor.record_usage(
                model=str(resolved_config.model) if resolved_config else "unknown",
                task_type="unknown",
                total_tokens=total_input + total_output,
                input_tokens=total_input,
                output_tokens=total_output,
            )
        self._conversation.append(("user", prompt))
        self._conversation.append(("assistant", content))
        return content

    def stream_call_model(self, prompt: str) -> Generator[dict[str, Any], None, None]:
        from lingclaude.model.types import ModelMessage, MessageRole, ModelUsage, ToolCall

        messages: list[ModelMessage] = []

        system_prompt = self._build_adaptive_system_prompt()
        if system_prompt:
            messages.append(ModelMessage(role=MessageRole.SYSTEM, content=system_prompt))

        for role, content in self._conversation:
            messages.append(ModelMessage(role=MessageRole(role), content=content))
        messages.append(ModelMessage(role=MessageRole.USER, content=prompt))

        tools = self._build_openai_tools()
        resolved_config, _ = self._resolve_model_config(prompt)
        used_tools = False
        response_content = ""
        total_input = 0
        total_output = 0

        for round_idx in range(AGENT_MAX_TOOL_ROUNDS):
            round_text_parts: list[str] = []
            round_tool_calls: list[ToolCall] = []

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
                    self._track_behavior(prompt, f"[模型调用失败] {event['error']}", used_tools=False)
                    yield {"type": "error", "error": event["error"]}
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
                self._track_behavior(prompt, content, used_tools=used_tools)
                self._usage = self._usage.add_usage(total_input, total_output)
                response_content = content
                self._conversation.append(("user", prompt))
                self._conversation.append(("assistant", content))
                yield {"type": "done", "content": content}
                return

            used_tools = True
            self._behavior = self._behavior.record_tool_calls(count=len(round_tool_calls))

            messages.append(ModelMessage(
                role=MessageRole.ASSISTANT,
                content=round_content,
                tool_calls=tuple(round_tool_calls),
            ))

            for tc in round_tool_calls:
                yield {"type": "tool_call_start", "name": tc.name, "arguments": tc.arguments}
                tool_output = self._execute_tool_with_retry(tc.name, tc.arguments)
                is_error = '"error"' in tool_output
                if is_error:
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

        content = response_content or "[达到最大工具调用轮次]"
        self._track_behavior(prompt, content, used_tools=used_tools)
        self._usage = self._usage.add_usage(total_input, total_output)
        self._conversation.append(("user", prompt))
        self._conversation.append(("assistant", content))
        yield {"type": "done", "content": content}

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

    def _build_openai_tools(self) -> tuple[dict[str, Any], ...] | None:
        if self._runtime is None:
            return None
        tool_defs = self._runtime.registry.list_tools()
        if not tool_defs:
            return None
        return tuple(
            {
                "name": t.name,
                "description": t.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        k: v for k, v in t.parameters.items()
                    },
                    "required": list(t.parameters.keys()),
                },
            }
            for t in tool_defs
        )

    def _execute_tool(self, name: str, arguments_json: str) -> str:
        try:
            kwargs = json.loads(arguments_json)
        except json.JSONDecodeError:
            return json.dumps({"error": f"Invalid JSON arguments: {arguments_json}"}, ensure_ascii=False)
        try:
            result = self._runtime.execute_tool(name, **kwargs)
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            logger.warning("Tool execution failed: %s.%s -> %s", name, kwargs.keys(), e)
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _compact_if_needed(self) -> None:
        if len(self._messages) > self.config.compact_after_turns * 2:
            half = (self.config.compact_after_turns // 2) * 2
            kept = self._messages[-half:]
            summary = f"[前 {(len(self._messages) - half) // 2} 轮对话已压缩]"
            self._messages[:] = [summary] + kept
        conv_limit = self.config.compact_after_turns * 2
        if len(self._conversation) > conv_limit:
            kept = self._conversation[-conv_limit:]
            self._conversation[:] = kept
        if len(self._transcript) > self.config.compact_after_turns:
            self._transcript[:] = self._transcript[-self.config.compact_after_turns:]

    def _resolve_model_config(self, prompt: str) -> tuple[ModelConfig | None, Any]:
        if self._model_config is None:
            return None, None
        from lingclaude.core.behavior import Intent

        cfg = self._model_config
        router = self._model_router
        target_model = cfg.model

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

        config = ModelConfig(
            model=target_model,
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            max_tokens=cfg.max_tokens,
            temperature=cfg.temperature,
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
