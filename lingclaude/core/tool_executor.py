from __future__ import annotations

import json
import logging
import time
from typing import Any

from lingclaude.core.config import lingclaudeConfig
from lingclaude.model.types import ModelConfig
from lingclaude.core.hooks import HookContext, HookType
from lingclaude.core.context_compression import compress_messages, CompressionConfig, CompressionLevel
from lingclaude.core.task_aggregation import TaskPriority
from lingclaude.core.behavior import detect_intent
from lingclaude.core.layered_memory import Experience, EmotionIntensity
from lingclaude.engine import mcp_proxy

logger = logging.getLogger(__name__)


def _estimate_message_tokens(messages: list[Any]) -> int:
    total_chars = 0
    for msg in messages:
        if isinstance(msg, dict):
            total_chars += len(str(msg.get("content", "")))
            if msg.get("tool_calls"):
                total_chars += sum(
                    len(str(tc.get("function", {}).get("arguments", "")))
                    for tc in msg["tool_calls"]
                )
        else:
            total_chars += len(str(msg))
    return total_chars // 4


class ToolExecutor:
    def __init__(self, engine) -> None:
        self._engine = engine

    def _execute_tool(self, name: str, arguments_json: str) -> str:
        self._engine._tool_call_count += 1
        limit = self._engine.config.max_tool_calls_per_session
        if limit > 0 and self._engine._tool_call_count > limit:
            return json.dumps({"error": f"[安全限制] 会话工具调用次数已达上限 ({limit})"}, ensure_ascii=False)

        try:
            kwargs = json.loads(arguments_json)
        except json.JSONDecodeError:
            return json.dumps({"error": f"Invalid JSON arguments: {arguments_json}"}, ensure_ascii=False)

        if name == "read" and "path" in kwargs:
            try:
                content, cache_hit = self._engine._cache.read_file(kwargs["path"])
                is_dup = self._engine._monitor.record_file_read(kwargs["path"], content)
                self._engine._dementia_detector.record_file_read(kwargs["path"])
                if cache_hit:
                    self._engine._session_cache_hits += 1
                    logger.debug("ContextCache hit for %s (duplicate=%s)", kwargs["path"], is_dup)
                return json.dumps({"content": content, "cache_hit": cache_hit}, ensure_ascii=False, default=str)
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.debug("Cache read failed, falling back to tool: %s", e)

        try:
            result = self._engine._runtime.execute_tool(name, **kwargs)
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
        self._engine._ensure_mcp()
        result = mcp_proxy.call_tool(name, **kwargs)
        if result.is_error:
            return json.dumps({"error": result.error}, ensure_ascii=False)
        data = result.data
        if data.success:
            return json.dumps(data.output if isinstance(data.output, dict) else {"result": data.output}, ensure_ascii=False, default=str)
        return json.dumps({"error": data.error or "MCP tool call failed"}, ensure_ascii=False)

    def _compact_if_needed(self) -> None:
        msg_limit = self._engine.config.compact_after_turns * 2
        token_budget_threshold = self._engine.config.max_budget_tokens * 0.8
        msg_tokens = _estimate_message_tokens(self._engine._messages)
        if len(self._engine._messages) > msg_limit or msg_tokens > token_budget_threshold:
            trigger_reason = (
                "message_count" if len(self._engine._messages) > msg_limit else "token_budget"
            )
            if trigger_reason == "token_budget":
                target_max = max((len(self._engine._messages) + 1) // 2, 4)
            else:
                target_max = (self._engine.config.compact_after_turns // 2) * 2
            self._engine._hooks.trigger(HookContext(
                hook_type=HookType.PRE_COMPACT,
                session_id=self._engine.session_id,
                metadata={
                    "message_count": len(self._engine._messages),
                    "limit": msg_limit,
                    "estimated_tokens": msg_tokens,
                    "token_threshold": token_budget_threshold,
                    "trigger_reason": trigger_reason,
                },
            ))
            self._archive_dropped_messages(
                (len(self._engine._messages) - target_max) // 2
            )
            result = compress_messages(
                self._engine._messages,
                config=CompressionConfig(
                    max_messages=target_max,
                    level=CompressionLevel.SUMMARY,
                    # T1-1: 动态预算 + LLM 摘要通道（provider 来自 engine）
                    model_window_tokens=getattr(self._engine.config, "max_budget_tokens", None),
                    use_llm_summary=getattr(self._engine.config, "use_llm_summary", False),
                    provider=getattr(self._engine, "_provider", None),
                ),
            )
            self._engine._messages[:] = result.compressed_messages
            logger.info(
                "Context compressed: dropped=%d, saved~%d tokens, facts=%d",
                result.dropped_count, result.tokens_estimated_saved, result.archived_facts,
            )
            self._engine._hooks.trigger(HookContext(
                hook_type=HookType.POST_COMPACT,
                session_id=self._engine.session_id,
                metadata={
                    "dropped": result.dropped_count,
                    "saved_tokens": result.tokens_estimated_saved,
                    "archived_facts": result.archived_facts,
                },
            ))
        conv_limit = self._engine.config.compact_after_turns * 2
        if len(self._engine._conversation) > conv_limit:
            conv_msgs = [{"role": r, "content": c} for r, c in self._engine._conversation]
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
            self._engine._conversation[:] = summary_lines + kept_pairs
        if len(self._engine._transcript) > self._engine.config.compact_after_turns:
            transcript_msgs = [{"content": t} for t in self._engine._transcript]
            tr_result = compress_messages(
                transcript_msgs,
                config=CompressionConfig(
                    max_messages=self._engine.config.compact_after_turns,
                    level=CompressionLevel.TRUNCATE,
                ),
            )
            self._engine._transcript[:] = [
                m.get("content", "") if isinstance(m, dict) else str(m)
                for m in tr_result.compressed_messages
                if not (isinstance(m, str) and m.startswith("[前"))
            ]

    def _archive_dropped_messages(self, dropped_count: int) -> None:
        if dropped_count <= 0 or len(self._engine._conversation) < 2:
            return
        dropped = self._engine._conversation[:dropped_count * 2]
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

            # T1-1 深化: LLM 摘要归档 — 开启 use_llm_summary 且 provider 可用时，
            # 用 LLM 生成结构化摘要作为 result 主体（替代纯正则提取）
            llm_summary = self._try_archive_llm_summary(dropped)
            if llm_summary:
                parts.append(f"LLM 摘要: {llm_summary}")

            try:
                self._engine._layered_memory.experience.store(
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

    def _try_archive_llm_summary(self, dropped: list[tuple[str, str]]) -> str | None:
        """T1-1 深化: 用 LLM 生成归档摘要（失败降级返回 None，正则提取仍生效）。"""
        if not getattr(self._engine.config, "use_llm_summary", False):
            return None
        provider = getattr(self._engine, "_provider", None)
        if provider is None:
            return None
        try:
            from lingclaude.core.model_adapter import ModelAdapter

            transcript = "\n".join(f"{role}: {text[:200]}" for role, text in dropped[-20:])
            adapter = ModelAdapter(provider)
            result = adapter.call(
                messages=(("user", (
                    "你是一个对话归档器。把以下历史对话压缩成结构化中文摘要，"
                    "保留：关键决策、已排除方案、遇到的错误、任务进展。控制在 300 字符内。\n\n"
                    + transcript
                )),),
                max_tokens=256,
            )
            if result.is_error:
                logger.warning("LLM 归档摘要失败: %s", result.error)
                return None
            content = result.data.content
            return content.strip() if content and content.strip() else None
        except Exception as e:  # noqa: BLE001 — LLM 归档失败静默降级
            logger.debug("LLM 归档摘要降级: %s", e)
            return None

    def _resolve_model_config(self, prompt: str) -> tuple[ModelConfig | None, Any]:
        if self._engine._model_config is None:
            return None, None
        from lingclaude.core.behavior import Intent

        cfg = self._engine._model_config
        router = self._engine._model_router

        routed_config, route_key = self._engine._task_router.resolve(prompt)
        target_model = routed_config.model
        target_temp = cfg.temperature
        target_api_key = routed_config.api_key
        target_base_url = routed_config.base_url
        default_provider = self._engine._task_router.get_default_provider_name()
        routed_provider = self._engine._task_router.get_provider_name(
            routed_config.api_key, routed_config.base_url
        )

        if router and router.enabled and (router.code_model or router.chat_model):
            decision = self._engine._router.route(prompt)
            intent = detect_intent(prompt)
            is_code = intent in (Intent.CODE_QUESTION, Intent.BUG_REPORT, Intent.OPTIMIZATION_REQUEST)
            legacy_model = router.code_model if is_code else router.chat_model
            if legacy_model:
                if routed_provider is None or routed_provider == default_provider:
                    target_model = legacy_model
                    target_api_key = cfg.api_key
                    target_base_url = cfg.base_url

            self._engine._aggregator.add_task(
                query=prompt,
                task_type=str(decision.task_type.value),
                priority=TaskPriority.MEDIUM,
            )

        bm = self._engine._behavior
        if bm.total_turns > 3:
            if bm.hallucination_risk > 0.4:
                target_temp = min(target_temp, 0.3)
            if bm.frustration_rate > 0.3:
                target_temp = min(target_temp, 0.2)
            if bm.tool_error_rate > 0.4 and router and router.code_model:
                if routed_provider is None or routed_provider == default_provider:
                    target_model = router.code_model

        diag = self._engine._dementia_detector.diagnose()
        if diag.dementia_index > 0.3:
            target_temp = min(target_temp, 0.1)

        config = ModelConfig(
            model=target_model,
            api_key=target_api_key,
            base_url=target_base_url,
            max_tokens=cfg.max_tokens,
            temperature=target_temp,
            system_prompt="",
        )
        return config, route_key
