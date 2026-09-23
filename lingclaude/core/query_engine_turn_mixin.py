"""Q4 (2026-09-14): QueryEngine 回合循环 + 工具执行职责拆分（行为零变化）。

从 lingclaude/core/query_engine.py 机械搬迁（AST 提取原方法体，仅缩进调整）。
消费方（QueryEngine）以多继承组合本 mixin；对外 API 面不变。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
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
from lingclaude.core.session_token_sink import record_turn_usage  # D3 清偿 (2026-09-24)

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
            # 2026-09-21 (前缀缓存优化 P0-2): system prompt 只含纯静态 _BASE_PROMPT
            # （字节级稳定 → provider 前缀缓存可命中）；动态段（SESSION_CONTEXT/
            # 行为警告/工具计数提示等）拆到 build_dynamic_system_suffix，以独立
            # system 消息 tail-append 在历史之后——每轮只破坏一次尾部缓存。
            system_prompt = self._build_adaptive_system_prompt(current_query=prompt)
            if system_prompt:
                messages.append(ModelMessage(role=MessageRole.SYSTEM, content=system_prompt))
            for role, content in self._conversation:
                # A1b (2026-09-13): 脱敏保险。2026-09-21 (P0-2) 脱敏前移到写入点
                # （_record_user_prompt/_append_assistant 均过 _redact_text），
                # 发送时保留 redact 作为幂等保险网：对已脱敏历史是 no-op
                # （redact 幂等且不误伤普通文本 → 字节级不变，前缀缓存仍命中）；
                # 对升级前落盘的残留明文仍强制 scrub（安全属性不回退）。
                messages.append(ModelMessage(role=MessageRole(role), content=_redact_text(content)))
            dynamic_suffix = self._build_dynamic_suffix(current_query=prompt)
            if dynamic_suffix:
                messages.append(ModelMessage(role=MessageRole.SYSTEM, content=dynamic_suffix))
            messages.append(ModelMessage(role=MessageRole.USER, content=prompt))
            return messages

        def _build_dynamic_suffix(self, current_query: str) -> str:
            """动态上下文尾随块（前缀缓存优化 P0-2 拆出，语义同旧 adaptive extras）。"""
            from lingclaude.core.system_prompt_builder import build_dynamic_system_suffix
            try:
                return build_dynamic_system_suffix(
                    behavior=self._behavior,
                    layered_memory=self._layered_memory,
                    meta_cognition=self._meta_cognition,
                    messages=self._messages,
                    session_cache_hits=self._session_cache_hits,
                    dementia_detector=self._dementia_detector,
                    project_index=self._project_index,
                    tool_call_count=self._tool_call_count,
                    current_query=current_query,
                    model_switch_note=self._model_switch_note,
                )
            except Exception:  # noqa: BLE001 — 动态段失败不影响主输出
                logger.debug("dynamic system suffix 构建失败", exc_info=True)
                return ""

        def _finalize_turn(
            self,
            prompt: str,
            content: str,
            used_tools: bool,
            total_input: int,
            total_output: int,
            resolved_config: Any,
            total_cached: int = 0,
            ctx_input_tokens: int | None = None,
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
                from lingclaude.engine.loop.loop_body import (  # P0-A: core→engine 消费边, M3 行级豁免
                    _estimate_message_tokens,
                    _estimate_tokens,
                )
                total_input = max(1, _estimate_tokens(prompt))
                total_output = _estimate_tokens(final_content)
                # 分子口径哨兵（2026-09-21）：provider 未回传 usage 时 total_input
                # 只是单轮 prompt 粗估（≈len/4），当「整个上下文」喂给 toolbar 会
                # 随轮次线性低估。落哨兵：_last_turn_input<0 → 消费方回退字符估算。
                # total_input 立即归正，下游 monitor/journal 记账语义不变。
                ctx_input_tokens = -total_input
                total_input = -total_input
            self._last_turn_input = total_input
            # ctx 口径优先（2026-09-21 修复 4743k/500k=949% 虚高）：工具循环里
            # total_input 是「turn 内每个请求轮 prefill 之和」（含历史反复计费），
            # 当「当前上下文体量」喂给 toolbar 随轮次线性虚高（95 轮工具的
            # turn 可膨胀百倍）。ctx_input_tokens = 最后一请求轮的真实
            # prompt_tokens = 当前实际发送的上下文（system+history+tools+本轮），
            # 才是「离触发窗口上限还有多远」的正确分子。None（无工具轮/未回传）
            # 时保持 total_input 原语义（含上方负值哨兵）。
            if ctx_input_tokens is not None:
                self._last_turn_input = ctx_input_tokens
            total_input = abs(total_input)
            self._usage = self._usage.add_usage(total_input, total_output, total_cached)
            self._last_turn_cached = total_cached
            # 2026-09-21: 本轮真实 input tokens（toolbar 上下文口径的真实值来源；
            # 负值 = provider 未回传时的估算兜底哨兵，消费方见上注释）
            self._last_model = (
                str(resolved_config.model) if resolved_config else getattr(self, "_last_model", "?")
            )
            # D3 清偿 (2026-09-24): session 维度真实 token 落盘——经 record_turn_usage
            # 组装 metadata 走同一 legacy_sink 链（灵忆镜像 + session_token_sink JSON 落盘），
            # 不在 monitor 外双路写；聚合层口径不变（task_type/total 同旧语义）。
            # round_idx: _finalize_turn 时 _messages 尚未 append，turn_count+1 = 本轮 1-based。
            record_turn_usage(
                self._monitor,
                session_id=str(getattr(self, "session_id", "unknown")),
                round_idx=self.turn_count + 1,
                model=str(resolved_config.model) if resolved_config else "unknown",
                provider=(
                    type(self._provider).__name__.removesuffix("Provider").lower()
                    if self._provider is not None
                    else "unknown"
                ),
                input_tokens=total_input,
                output_tokens=total_output,
                cached_tokens=total_cached,
                task_type="unknown",
            )
            # 2026-09-21 (前缀缓存优化 P0-2): 脱敏前移到写入点——历史一经写入
            # 即为脱敏后的稳定字节，发送时原样透传（见 _build_messages 注释）。
            self._conversation.append(("user", _redact_text(prompt)))
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
                    # P1-3 (2026-09-19): 传入当轮真实证据 —— 与上方 _finalize_turn
                    # 的 P17 cross-reference 同源（journal），使声明-验证闭环在
                    # 流式路径真正闭合：有据声明不再误报，无据声明必须打标。
                    _ev_used_tools = bool(tool_evidence)
                    vr = validate_task_result(
                        result_message=final_content,
                        task_context={"prompt": prompt[:200], "session_id": getattr(self, 'session_id', 'unknown')},
                        strict=False,  # 非严格模式，只标记不阻断
                        used_tools=_ev_used_tools,
                        tool_evidence=tool_evidence,
                        tool_results=tool_results,
                    )
                    if vr["has_hallucination"]:
                        # 在输出前添加幻觉警告标记
                        warning = "\n\n⚠️ [幻觉治理] 检测到未验证断言，请谨慎采信"
                        final_content = final_content + warning
                        logger.warning(
                            "hallucination_guard: 检测到 %d 个问题: %s",
                            len(vr["issues"]), vr["issues"]
                        )
                        # R10 (2026-09-23): 复发埋点——守卫触发即落 error_log
                        # （细粒度 pattern_type，喂活 top_error_patterns 聚合）
                        # + 30min 窗口内的注入记录 recurrence_count+1。
                        # fail-soft：埋点故障绝不影响主输出。
                        try:
                            from lingclaude.core.data_flywheel import DataFlywheel
                            _fact_types = sorted({
                                str(i).split(":", 1)[0].strip()
                                for i in vr.get("issues", []) if str(i).strip()
                            }) or ["unknown"]
                            _fw = DataFlywheel()
                            _fw.record_recurrence(
                                session_id=str(getattr(self, "session_id", "unknown")),
                                fact_types=_fact_types,
                                occurred_at=datetime.now().isoformat(timespec="seconds"),
                            )
                            _fw.close()
                        except Exception:  # pragma: no cover - fail-soft
                            logger.debug("R10 复发埋点异常（已忽略）", exc_info=True)
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
            from lingclaude.engine.loop.loop_body import _estimate_message_tokens  # P0-A: core→engine 消费边, M3 行级豁免
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
