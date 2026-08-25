"""提交 mixin — submit/stream_submit/resume_interrupted（从 query_engine 拆出，瘦身）。

QueryEngine 通过多继承接入本 mixin；方法内 self 即 QueryEngine 实例，
依赖其 _task_manager/_skill_index/_hooks/_session_persister 等成员。
"""
from __future__ import annotations

import logging
from typing import Any

from lingclaude.core.hooks import HookType, HookContext
from lingclaude.core.models import PermissionDenial
from lingclaude.core.types import Result, StopReason
from lingclaude.core.cognitive_rhythm import ImbalanceType
from lingclaude.core.model_call import AGENT_MAX_TOOL_ROUNDS

logger = logging.getLogger(__name__)


class SubmissionMixin:
    """提交主循环 + 流式提交 + 中断恢复。"""

    def submit(
        self,
        prompt: str,
        matched_commands: tuple[str, ...] = (),
        matched_tools: tuple[str, ...] = (),
        denied_tools: tuple[PermissionDenial, ...] = (),
    ) -> Any:
        if len(self._messages) // 2 >= self.config.max_turns:
            return self._make_turn_result(
                prompt, f"已达最大轮次 ({self.config.max_turns})。",
                matched_commands, matched_tools, denied_tools,
                StopReason.MAX_TURNS_REACHED,
            )

        # T0: 行为校验 (零推理成本) — 在进入主流程前拦截明显问题
        t0_nudge = self._check_behavior(prompt)
        if t0_nudge is not None:
            return self._make_turn_result(
                prompt, t0_nudge,
                matched_commands, matched_tools, denied_tools,
                StopReason.COMPLETED,
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
            return self._make_turn_result(
                prompt, intent_check,
                matched_commands, matched_tools, denied_tools,
                StopReason.COMPLETED,
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

        return self._make_turn_result(
            prompt, output,
            matched_commands, matched_tools, denied_tools,
            stop_reason,
        )

    def _make_turn_result(
        self,
        prompt: str,
        output: str,
        matched_commands: tuple[str, ...],
        matched_tools: tuple[str, ...],
        denied_tools: tuple[PermissionDenial, ...],
        stop_reason: StopReason,
    ) -> Any:
        """构造 TurnResult（submit 多分支共用）。"""
        from lingclaude.core.query_engine import TurnResult
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

    # T3-3 瘦身丢件修复：checkpoint 三方法 + session 持久化两委托
    # （HEAD 旧 query_engine.py:544-557 区域）。拆分时调用方/消费方搬走了，
    # 定义被整组丢弃 → reset()/resume_interrupted 运行时 AttributeError、
    # engine.persist_session()/load_session() 入口消失（全量 17 红，
    # T0+T1 回归零覆盖未拦下）。
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
            self._tool_call_executor.process(response.tool_calls, messages, content=response.content)
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
