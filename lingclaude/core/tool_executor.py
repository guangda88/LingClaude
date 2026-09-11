from __future__ import annotations

import json
import logging
from typing import Any

from lingclaude.model.types import ModelConfig
from lingclaude.core.hooks import HookContext, HookType
from lingclaude.core.context_compression import compress_messages, CompressionConfig, CompressionLevel
from lingclaude.core.task_aggregation import TaskPriority
from lingclaude.core.behavior import detect_intent
from lingclaude.core.layered_memory import Experience, EmotionIntensity
from lingclaude.core.types import (
    ToolErrorCode,
    ToolResult,
    parse_tool_result,
)

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
    # 任务5: 工具结果 pruner 阈值 — 超 8KB 字符 (~2k token) 自动 stub 化
    # 与 DSH compaction-tool-result-pruner / AtomCode Tier1 对齐
    DEFAULT_PRUNE_THRESHOLD_BYTES = 8 * 1024
    DEFAULT_PRUNE_KEEP_BYTES = 2 * 1024  # 保留前 2KB + 后 2KB
    DEFAULT_PRUNE_KEEP_TAIL_BYTES = 2 * 1024

    def __init__(self, engine) -> None:
        self._engine = engine

    @staticmethod
    def _prune_output(output: str, threshold_bytes: int = DEFAULT_PRUNE_THRESHOLD_BYTES,
                      keep_bytes: int = DEFAULT_PRUNE_KEEP_BYTES,
                      keep_tail_bytes: int = DEFAULT_PRUNE_KEEP_TAIL_BYTES) -> str:
        """工具结果 pruner — 超阈值返回 stub 占位符 + 头尾保留。

        对标 DSH compaction-tool-result-pruner / AtomCode Tier1 stub 化:
        超阈值时原文本替换为 "[truncated: N bytes total, head + tail kept]"，
        保留 head + tail 让模型仍能抓住上下文。prefix cache 友好
        （截断边界稳定,前缀不变）。
        """
        raw = output.encode("utf-8", errors="replace")
        if len(raw) <= threshold_bytes:
            return output
        # head + ellipsis + tail
        head = raw[:keep_bytes].decode("utf-8", errors="replace")
        tail = raw[-keep_tail_bytes:].decode("utf-8", errors="replace") if keep_tail_bytes else ""
        return (
            f"{head}\n\n"
            f"[... truncated: {len(raw)} bytes total, "
            f"head {keep_bytes} + tail {keep_tail_bytes} bytes kept ...]\n\n"
            f"{tail}"
        )

    def _execute_tool(self, name: str, arguments_json: str) -> str:
        """执行工具，返回模型可见的 JSON 字符串（序列化边界）。"""
        tr = self._execute_tool_typed(name, arguments_json)
        return json.dumps(tr.to_dict(), ensure_ascii=False, default=str)

    def _execute_tool_typed(self, name: str, arguments_json: str) -> ToolResult[Any]:
        """强类型版本：返回 ToolResult（错误语义用 error.code，非字符串匹配）。

        这是唯一工具执行入口的强类型面；_execute_tool 仅做序列化。
        """
        self._engine._tool_call_count += 1
        limit = self._engine.config.max_tool_calls_per_session
        if limit > 0 and self._engine._tool_call_count > limit:
            return ToolResult.err(
                f"[安全限制] 会话工具调用次数已达上限 ({limit})",
                code=ToolErrorCode.TOOL_LIMIT_REACHED,
                tool_name=name,
            )

        try:
            kwargs = json.loads(arguments_json)
        except json.JSONDecodeError:
            return ToolResult.err(
                f"Invalid JSON arguments: {arguments_json}",
                code=ToolErrorCode.INVALID_ARGS,
                tool_name=name,
            )

        # 防御：模型偶发把参数包成 list（如 [{"path": ...}]），解包首元素；
        # 仍非 mapping 则直接返回结构化错误，避免 **kwargs 展开时 TypeError 崩溃。
        if isinstance(kwargs, list) and kwargs and isinstance(kwargs[0], dict):
            kwargs = kwargs[0]
        if not isinstance(kwargs, dict):
            return ToolResult.err(
                f"Tool arguments must be a JSON object, got {type(kwargs).__name__}: {arguments_json[:200]}",
                code=ToolErrorCode.INVALID_ARGS,
                tool_name=name,
            )

        if name == "read" and "path" in kwargs:
            # P0 主链统一 (2026-09-12, codex 审计 #1): read 快路径此前完全绕过
            # ToolPipeline（权限/守卫/敏感路径检查全部跳过，直接查 ContextCache）。
            # 修复: 先过 pipeline 纯权限预检（不执行 handler），放行后才走 cache；
            # cache 未命中/失败降级到完整 pipeline 执行。
            runtime = getattr(self._engine, "_runtime", None)
            pipeline = getattr(runtime, "tool_pipeline", None)
            if pipeline is not None and hasattr(pipeline, "check_permission"):
                preflight = pipeline.check_permission(
                    name, kwargs,
                    permissions_blocks=(
                        getattr(runtime, "_blocks", None)  # CodingRuntime._blocks
                        or getattr(self._engine, "_tool_blocked", None)
                    ),
                )
                if preflight is not None:
                    return parse_tool_result(preflight, tool_name=name)
            try:
                content, cache_hit = self._engine._cache.read_file(kwargs["path"])
                is_dup = self._engine._monitor.record_file_read(kwargs["path"], content)
                self._engine._dementia_detector.record_file_read(kwargs["path"])
                if cache_hit:
                    self._engine._session_cache_hits += 1
                    logger.debug("ContextCache hit for %s (duplicate=%s)", kwargs["path"], is_dup)
                result_dict = {"content": content, "cache_hit": cache_hit}
                return ToolResult.ok(result_dict)
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.debug("Cache read failed, falling back to tool: %s", e)

        try:
            result = self._engine._runtime.execute_tool(name, **kwargs)
            tr = parse_tool_result(result, tool_name=name)
            if tr.is_error:
                # P1 安全对齐(2026-09-11, codex 审计): 仅当错误确属「本地工具未注册/未
                # 找到」时才 fallback MCP。此前任何含 "not found" 的错误(含权限拒绝
                # "tool not found/blocked" 文案)都会触发 MCP fallback —— 等于用 MCP
                # 通道绕过 ToolPipeline 的权限/守卫/敏感路径检查, 是隐藏的旁路。
                # 判据收紧为: error.code == TOOL_NOT_FOUND（未注册语义）才 fallback。
                assert tr.error is not None
                if tr.error.code == ToolErrorCode.TOOL_NOT_FOUND:
                    mcp_tr = self._execute_mcp_tool_typed(name, kwargs)
                    return mcp_tr
                return tr
            return tr
        except Exception as e:
            kw_desc = kwargs if isinstance(kwargs, dict) else str(kwargs)[:120]
            logger.warning("Tool execution failed: %s.%s -> %s", name, kw_desc, e)
            return ToolResult.err(str(e), code=ToolErrorCode.EXECUTION_ERROR, tool_name=name)

    def _execute_mcp_tool(self, name: str, kwargs: dict[str, Any]) -> str:
        """MCP 工具调用（序列化边界）。"""
        tr = self._execute_mcp_tool_typed(name, kwargs)
        return json.dumps(tr.to_dict(), ensure_ascii=False, default=str)

    def _execute_mcp_tool_typed(self, name: str, kwargs: dict[str, Any]) -> ToolResult[Any]:
        """强类型版本：MCP 工具调用。"""
        from lingclaude.engine import mcp_proxy

        self._engine._ensure_mcp()
        result = mcp_proxy.call_tool(name, **kwargs)
        if result.is_error:
            return ToolResult.err(
                result.error or "MCP tool call failed",
                code=ToolErrorCode.MCP_TOOL_NOT_FOUND,
                tool_name=name,
            )
        data = result.data
        if data.success:
            return ToolResult.ok(data.output if isinstance(data.output, dict) else {"result": data.output})
        return ToolResult.err(
            data.error or "MCP tool call failed",
            code=ToolErrorCode.MCP_CALL_FAILED,
            tool_name=name,
        )

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
                    # T1-1 深化: 按模型窗口动态预算（优先 context_window_tokens）
                    model_window_tokens=getattr(self._engine.config, "context_window_tokens", None) or getattr(self._engine.config, "max_budget_tokens", None),
                    use_llm_summary=getattr(self._engine.config, "use_llm_summary", False),
                    provider=getattr(self._engine, "_provider", None),
                ),
            )
            self._engine._messages[:] = result.compressed_messages
            # R5 compact×checkpoint 交互: 压缩后 messages 变了，
            # 如果有 active checkpoint，用压缩后的消息重新保存
            if self._engine.session_store.has_checkpoint:
                try:
                    self._engine._session_persister.save_checkpoint(
                        messages=self._engine._messages,
                        round_idx=0,
                        prompt=self._engine._messages[-1] if self._engine._messages else "",
                        used_tools=True,
                        total_input=0, total_output=0,
                    )
                    logger.info("checkpoint re-saved after compact (%d msgs)", len(self._engine._messages))
                except Exception:
                    logger.warning("checkpoint re-save after compact failed", exc_info=True)
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
                    low = line.strip().lower()
                    if any(k in low for k in ("决定", "选择", "采用", "decided", "chose", "方案")):
                        decisions.append(line.strip()[:200])
                    if any(k in low for k in ("错误", "失败", "error", "failed", "不对")):
                        errors_seen.append(line.strip()[:200])
            elif role == "user":
                for line in text.split("\n"):
                    low = line.strip().lower()
                    if any(k in low for k in ("read", "读取", "查看", "cat ", "view ")):
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
        from lingclaude.model.types import ModelConfig
        # Check for pinned model first (bypasses TaskRouter)
        if self._engine.is_model_pinned():
            pinned_cfg = self._engine._pinned_model_config
            config = ModelConfig(
                model=pinned_cfg.model,
                api_key=pinned_cfg.api_key,
                base_url=pinned_cfg.base_url,
                max_tokens=pinned_cfg.max_tokens,
                temperature=pinned_cfg.temperature,
                system_prompt=pinned_cfg.system_prompt,
            )
            return config, "pinned"

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

        _model_kwargs = {"api_key": target_api_key}
        config = ModelConfig(
            model=target_model,
            **_model_kwargs,
            base_url=target_base_url,
            max_tokens=cfg.max_tokens,
            temperature=target_temp,
            system_prompt="",
        )
        return config, route_key
