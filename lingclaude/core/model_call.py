"""模型调用 mixin — _call_model/stream_call_model/幻觉闭环/MV-1 校验（从 query_engine 拆出，瘦身）。

QueryEngine 通过多继承接入本 mixin；方法内 self 即 QueryEngine 实例，
依赖其 _router/_provider/_build_messages/_finalize_turn 等成员。
"""
from __future__ import annotations

import logging
from typing import Any, Generator

from lingclaude.core.behavior import detect_intent, is_tool_intent

logger = logging.getLogger(__name__)

# 模型调用最大工具轮次（query_engine 模块级常量迁移至此，避免循环导入）
AGENT_MAX_TOOL_ROUNDS = 10


class ModelCallMixin:
    """模型调用 + MV-1 校验 + 幻觉闭环。"""

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
            self._tool_call_executor.process(response.tool_calls, messages, content=response.content)

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
