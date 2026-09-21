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
from lingclaude.core.model_call import _resolve_max_tool_rounds

logger = logging.getLogger(__name__)


class SubmissionMixin:
    """提交主循环 + 流式提交 + 中断恢复。"""

    def _log_denial(self, denial: Any) -> None:
        """R2: denial 结构化日志 → flywheel + journal（best-effort 不阻塞）。"""
        try:
            self._log_to_flywheel(
                pattern_type="permission_denial",
                error_message=f"tool={denial.tool_name} reason={denial.reason}",
                tool_name=denial.tool_name,
                context=str(denial.reason)[:200],
            )
        except Exception:
            pass
        try:
            from lingclaude.core.session_journal import SessionJournal
            SessionJournal(
                self.session_id, journal_dir=getattr(self, "_journal_dir", None),
            ).append("permission_denial", {
                "tool_name": denial.tool_name,
                "reason": str(denial.reason)[:200],
            })
        except Exception:
            pass

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

        n_msgs_before = len(self._messages)
        output = self._generate_response(prompt, matched_commands, matched_tools, denied_tools)

        # 2026-09-17 修复 (usage 双计): provider 路径 _finalize_turn 已按真实
        # token add_usage（query_engine_turn_mixin.py），此处 add_turn 再叠一份
        # 词数估算 → usage 虚高、max_budget_tokens 提前熔断。仅无 provider 的
        # 本地 fallback 路径（不走 _finalize_turn）才需要此处估算记账。
        if self._provider is None:
            projected = self._usage.add_turn(prompt, output)
        else:
            projected = self._usage
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

        # H20: 正常轮的 _messages 镜像由 _finalize_turn 写入（与流式统一）。
        # 失败/硬中断轮不走 _finalize_turn，但旧语义（test_provider_error_returns_gracefully
        # 钉住）是失败轮也计一条 turn —— 此处兜底补记，保持失败轮可见性。
        if len(self._messages) == n_msgs_before:
            self._messages.append(prompt)
            self._messages.append(output)
        self._transcript.append(output)
        self._denials.extend(denied_tools)
        # R2: 逐条记录 denial 到 flywheel + journal
        for d in denied_tools:
            self._log_denial(d)
        self._usage = projected
        self._compact_if_needed()
        self._total_messages_sent += 1
        self._check_degradation(prompt, output)
        # L5 治理：should_trigger 命中（高风险关键词）时升级为完整审视
        # （run_l5_audit_full 用真实 tool_call_log 做声明-行为一致性校验）；
        # 未命中则保持轻量 placeholder（零额外 LLM 调用）。
        try:
            if self._l5_loop.should_trigger(prompt):
                output = self.run_l5_audit_full(prompt, output)
            else:
                output = self._apply_l5_audit(prompt, output)
        except Exception as e:  # noqa: BLE001 — L5 失败不阻塞主流程
            logger.warning("L5 audit failed (non-blocking): %s", e)
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
            # R2: 逐条记录 denial 到 flywheel + journal
            for d in denied_tools:
                self._log_denial(d)
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
                # 2026-09-17 修复 (流式 usage/stop_reason 恒空): done 分支此前
                # 只取 content，usage_data/stop_reason 初始化值原样上抛 →
                # 流式 API 永远上报 usage={}、stop_reason=end_turn。
                usage_data = event.get("usage") or {}
                if event.get("finalized"):
                    stop_reason = "end_turn"
                else:
                    stop_reason = "max_turns"
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

    # P1 rewind (2026-09-12): 快照回滚 — engine 级入口，供 CLI /rewind 调用
    def list_checkpoints(self) -> list[dict[str, Any]]:
        return self._session_persister.list_checkpoints()

    def rewind_to(self, tag: str) -> Result[str]:
        """回滚到指定 tag 的 checkpoint。成功返回回滚摘要。"""
        from lingclaude.core.types import Result as _R

        ok = self._session_persister.rewind_to(tag)
        if not ok:
            return _R.fail(f"Checkpoint tag not found: {tag}", code="NO_CHECKPOINT")
        return _R.ok(f"已回滚到 {tag}（当前上下文 {len(self._messages)} 条消息）")

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
        tag: str | None = None,
    ) -> None:
        # P1 (2026-09-20, atomcode inflight 借鉴): turn_start 的 round=-1 inflight
        # 快照必须写主文件 `{session_id}.json`（不带 tag）——主文件是崩溃恢复介质
        # （load_checkpoint 在 _active_checkpoint 缺失时回落读主文件），且 done 时
        # clear_checkpoint 只删 _active_checkpoint 指向的最后一个文件，带 tag 的
        # 版本文件会残留到下轮（test_session_journal::checkpoint_saved_after_tool_round
        # 实证）。turn_start 落主文件 = resume 链路 0 改动即可消费（round_idx=-1 →
        # range(0, ...) 从头重放，无 tool_calls → 走 finalize 分支）。
        if tag is None and round_idx is not None and round_idx >= 0:
            tag = f"round{round_idx}"
        self._session_persister.save_checkpoint(
            messages, round_idx, prompt, used_tools, total_input, total_output,
            tag=tag,
        )

    def _load_checkpoint(self) -> dict[str, Any] | None:
        return self._session_persister.load_checkpoint()

    def resume_interrupted(self) -> Result[str]:
        from lingclaude.core.model_types import ModelMessage, MessageRole, ToolCall

        data = self._load_checkpoint()
        if data is None:
            return Result.fail("No checkpoint found for this session", code="NO_CHECKPOINT")

        # R5 阶段1 副作用幂等: 读取 journal 获取已执行的工具签名。
        # resume 后模型可能重复调用同一工具（name+arguments 相同），
        # 注入提示让模型知道哪些已执行，避免重复写/重复执行副作用。
        journal = self._get_journal()
        executed_sigs = journal.tool_signatures()
        if executed_sigs:
            sig_list = "\n".join(f"  - {name}({args[:80]})" for name, args in sorted(executed_sigs)[:10])
            resume_note = (
                f"[系统] 以下工具调用在中断前已执行（journal 记录），"
                f"如需重复请确认必要性：\n{sig_list}"
            )
            logger.info("resume_interrupted: %d tool signatures from journal", len(executed_sigs))
        else:
            resume_note = ""

        # R5 阶段2: 副作用待确认清单（写/编辑/bash/rm/curl 等非只读工具）。
        # 让 cli 层在重放前做用户确认,避免重复写文件/重复执行副作用。
        from lingclaude.core.permissions import SIDE_EFFECT_TOOLS
        pending_effects = journal.pending_side_effects(SIDE_EFFECT_TOOLS)

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

        # R5 副作用幂等: 注入已执行工具列表作为 system note
        if resume_note:
            messages.append(ModelMessage(role=MessageRole.USER, content=resume_note))

        # R5 阶段2: 副作用待确认清单（写/编辑/bash/rm/curl 等非只读工具）。
        # 在 messages 头部注入"待确认"提示,让模型下一轮询问用户
        # 而不是盲目重放（防 57109 类误杀后副作用被执行两次）。
        if pending_effects:
            eff_list = "\n".join(
                f"  - {p['name']}({str(p['arguments'])[:80]})  [id={p['tool_call_id'][:12]}]"
                for p in pending_effects[:10]
            )
            pending_note = (
                f"[系统] 中断前有 {len(pending_effects)} 个未完成的副作用调用\n"
                f"（journal 记录但无对应 tool_result）:\n{eff_list}\n"
                "请向用户确认是**重放**还是**跳过**这些调用——避免重复写文件/执行命令。"
                "默认建议：跳过（已部分执行可能造成不可预期结果）。"
            )
            messages.append(ModelMessage(role=MessageRole.SYSTEM, content=pending_note))

        response = None

        for ri in range(round_idx + 1, _resolve_max_tool_rounds(self)):
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
                self._transcript.append(final_content)
                self._clear_checkpoint()
                journal.clear()
                self._journal_cache = None  # clear 后重置缓存，下次 _get_journal 重建
                self._append_to_session_history(prompt, final_content)
                self._learn_from_turn(prompt, final_content)
                # result.data 透出 pending_effects 摘要,让 cli 层可在 /recover 输出中提示
                final_content += f"\n\n[恢复摘要] 已处理 {len(pending_effects)} 个待确认副作用调用"
                return Result.ok(final_content)

            used_tools = True
            self._tool_call_executor.process(response.tool_calls, messages, content=response.content)
            self._save_checkpoint(messages, ri, prompt, used_tools, total_input, total_output)

        content = response.content if response and response.content else "[达到最大工具调用轮次]"
        final_content = self._finalize_turn(
            prompt, content, used_tools, total_input, total_output, resolved_config,
        )
        self._transcript.append(final_content)
        self._clear_checkpoint()
        journal.clear()
        self._journal_cache = None
        return Result.ok(final_content)
