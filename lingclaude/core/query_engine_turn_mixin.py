"""Q4 (2026-09-14): QueryEngine 回合循环 + 工具执行职责拆分（行为零变化）。

从 lingclaude/core/query_engine.py 机械搬迁（AST 提取原方法体，仅缩进调整）。
消费方（QueryEngine）以多继承组合本 mixin；对外 API 面不变。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from lingclaude.core.behavior import (
    Emotion,
    Intent,
    detect_emotion,
    detect_intent,
    is_tool_intent,
)
from lingclaude.core.models import PermissionDenial
from lingclaude.core.meta_cognition import Domain
from lingclaude.core.layered_memory import EmotionIntensity, Experience
from lingclaude.core.redact import redact as _redact_text
from lingclaude.core.types import Result, is_tool_error
from lingclaude.core.model_types import ModelMessage, MessageRole
from lingclaude.core.model_call import _estimate_message_tokens, _estimate_tokens

logger = logging.getLogger(__name__)


class QueryEngineTurnMixin:
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
            messages: list[ModelMessage] = []
            system_prompt = self._build_adaptive_system_prompt(current_query=prompt)
            if system_prompt:
                messages.append(ModelMessage(role=MessageRole.SYSTEM, content=system_prompt))
            for role, content in self._conversation:
                # A1b (2026-09-13): 发送前脱敏保险 — 历史/当前会话若残留明文 key
                # （升级前落盘的数据），防止再次回传给模型。幂等，正常文本不受影响。
                messages.append(ModelMessage(role=MessageRole(role), content=_redact_text(content)))
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
            # P17 (2026-09-14): Cross-reference claims —— 从 journal 取本 turn 真实工具证据，
            # 传给 prior_verifier 做「声明 ↔ 工具」语义匹配（有据可查才不算未验证）。
            # fail-soft：journal 不可用/异常 → 空证据 → 保持原语义（不阻断主输出）。
            tool_evidence: tuple[str, ...] = ()
            tool_results: dict[str, bool] = {}
            try:
                journal = self._get_journal()
                tool_evidence = tuple(
                    name for name, _args in journal.tool_signatures()
                )
                # 2026-09-14 新增：从 journal 获取工具执行结果（成功/失败）
                # 用于 prior_verifier 判断工具是否真的成功，避免"调用失败但声称成功"的幻觉
                for sig, results in journal.tool_results_by_signature().items():
                    tool_name = sig[0]
                    # 如果该工具有任何一条结果标记为错误，则视为失败
                    tool_results[tool_name] = not any(
                        r.get("is_error", False) for r in results
                    )
            except Exception:  # noqa: BLE001 — 治理插片故障绝不影响主输出
                logger.debug("P17: journal 读取失败，cross-reference 降级为纯先验", exc_info=True)
            vr = self._prior_verifier.analyze(
                content, used_tools=used_tools, tool_evidence=tool_evidence,
                tool_results=tool_results,  # 新增：传入工具执行结果
            )
            final_content = vr.corrected_text if vr.corrected_text else content
            # A1b (2026-09-13): 输出收口脱敏 — 模型回复唯一出口统一 scrub。
            # 在 append 进 conversation / layered_memory / 返回值（用户可见）之前打码，
            # 从源头切断「key 出现在对话 → 明文落盘 → 全文回传」链路。
            # 幂等：已脱敏文本不受影响；与落盘层（session_store/journal）脱敏互补。
            final_content = _redact_text(final_content)
            self._track_behavior(prompt, final_content, used_tools=used_tools)
            # P0: usage 遥测兜底 — provider 未回传 usage(全 0) 时估算, 保证 journal 非 0
            if total_input == 0 and total_output == 0 and final_content:
                from lingclaude.core.model_call import _estimate_message_tokens, _estimate_tokens
                total_input = max(1, _estimate_tokens(prompt))
                total_output = _estimate_tokens(final_content)
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
            # H20 (2026-09-16): _messages 镜像在此统一写入 — 此前只有非流式
            # submit 在 submission.py 显式 append(str)，stream_call_model 从不写，
            # 流式会话中 _messages 恒空 → 状态栏上下文恒 0%、turn_count 恒 0、
            # 压缩 message_count 触发门禁失效。canonical 源是 _conversation
            # （_build_messages:84 从它构建请求），镜像内容与其对齐（原文）；
            # 非流式 submit 的显式 append 已同步删除，防双写。
            self._messages.append(prompt)
            self._messages.append(final_content)

            # 幻觉治理守卫：输出前强制验证（fail-soft，不阻断主流程）
            # T9 (2026-09-14): should_validate 传真实任务名（prompt）而非空串，
            # 否则 task_name 关键词触发（push/deploy/fix/repair）永远不生效。
            try:
                from lingclaude.core.hallucination_guard import validate_task_result, should_validate
                if should_validate(prompt, final_content):
                    vr = validate_task_result(
                        result_message=final_content,
                        task_context={"prompt": prompt[:200], "session_id": getattr(self, 'session_id', 'unknown')},
                        strict=False,  # 非严格模式，只标记不阻断
                    )
                    if vr["has_hallucination"]:
                        # 在输出前添加幻觉警告标记
                        warning = "\n\n⚠️ [幻觉治理] 检测到未验证断言，请谨慎采信"
                        final_content = final_content + warning
                        logger.warning(
                            "hallucination_guard: 检测到 %d 个问题: %s",
                            len(vr["issues"]), vr["issues"]
                        )
            except Exception as e:
                # fail-soft: 验证器故障绝不影响主输出
                logger.debug("hallucination_guard: 验证失败（已忽略）: %s", e, exc_info=True)

            return final_content

        def _execute_tool_with_retry(self, name: str, arguments_json: str) -> str:
            result = self._execute_tool(name, arguments_json)
            if not is_tool_error(result):
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

        def _compact_if_needed(self) -> None:
            self._tool_executor._compact_if_needed()

        def _execute_tool(self, name: str, arguments_json: str) -> str:
            return self._tool_executor._execute_tool(name, arguments_json)

        def _execute_mcp_tool(self, name: str, kwargs: dict[str, Any]) -> str:
            return self._tool_executor._execute_mcp_tool(name, kwargs)
