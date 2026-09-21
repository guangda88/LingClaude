"""P0-A L0 批次 5（2026-09-22）：双路径轮次循环体（自 core/model_call.py 迁入）。

只挪不改（契约 §六 L0）：`_call_model` / `stream_call_model` 的循环体逐字迁移为
模块函数 `run_call_model_loop(engine, prompt)` / `run_stream_call_model_loop(engine, prompt)`，
`self` → `engine` 显式参数，语义零变化。治理钩子一律经 `engine.hooks`（LoopHooks）。

循环纯函数助手（_accumulate_usage / _slim_tool_output / _estimate_tokens /
_estimate_message_tokens / _resolve_max_tool_rounds / AGENT_MAX_TOOL_ROUNDS）随行迁入，
core/model_call.py 回 import re-export 保持既有引用面（tests 消费）。

import 方向（契约 §二）：本模块不得 import lingclaude.cli.*；
engine→core 允许（core.model_types / core.types / core.behavior）。
"""
from __future__ import annotations

import logging
from typing import Any, Generator

from lingclaude.core.types import is_tool_error
from lingclaude.core.model_types import MessageRole, ModelMessage, ModelUsage, ToolCall
from lingclaude.engine.loop.tool_loop_detector import (
    _LOOP_ABORT_MSG,
    _LOOP_WARN_HINT,
    _ToolLoopDetector,
)

logger = logging.getLogger(__name__)

# 模型调用最大工具轮次（query_engine 模块级常量迁移至此，避免循环导入）
# 注意：这只是"未配置时的兜底值"。运行时上限由 _resolve_max_tool_rounds()
# 从实例 config.max_turns（config.yaml → agent.max_turns）读取。
AGENT_MAX_TOOL_ROUNDS = 40


def _estimate_tokens(text: str, per_char: float = 0.28) -> int:
    """估算文本 token 数（usage 缺失时的兜底，不替代真实值）。

    - 中文为主时 ~0.28 token/字，英文 ~0.25 token/词（4 字符/词）。
    - 返回 int，空文本返回 0。
    """
    if not text:
        return 0
    return max(1, int(len(text) * per_char))


# P1-5（2026-09-21, 全 15 家精读 §3.2 codex 式大结果瘦身）：
# 工具结果进历史前的瘦身阈值。codex 语义：rollout 只存引用/摘要，
# 客户端按需重建——lc 在历史里存「前 N 字符 + 截断说明 + 总长度引用」，
# 既省长会话 token 膨胀，又保留关键信息（头 800 字符通常含报错/结果核心）。
_TOOL_RESULT_SLIM_THRESHOLD = 1600   # 超过此字符数的工具结果进历史前截断
_TOOL_RESULT_SLIM_KEEP = 800         # 截断后保留的前缀字符数


def _slim_tool_output(tool_name: str, output: str) -> str:
    """P1-5: 大工具结果瘦身——超过阈值只进历史存「前缀 + 截断说明 + 总长引用」。

    小结果原样返回（零开销）；大结果截断为前 800 字符 + 显式标记，
    标记里带总长与工具名，模型需要更多时可重新调用同一工具取全量。
    这是「进历史前截断」（§3.2），不改工具执行层——工具本身仍拿到全量。
    """
    if output is None:
        return ""
    if len(output) <= _TOOL_RESULT_SLIM_THRESHOLD:
        return output
    kept = output[:_TOOL_RESULT_SLIM_KEEP]
    return (
        f"{kept}\n"
        f"[工具结果瘦身: {tool_name} 输出共 {len(output)} 字符，"
        f"历史仅保留前 {_TOOL_RESULT_SLIM_KEEP} 字符。"
        f"如需完整内容请重新调用该工具。]"
    )


def _estimate_message_tokens(messages: list[Any]) -> int:
    """估算 messages 总 token 数（兜底；消息可为 str/dict/带 content 对象）。

    2026-09-14 (lingyuan 真重复收敛): 原 query_engine.py:97 与
    query_engine_turn_mixin.py:30 各有一份逐字相同实现（Q4 拆文件遗留副本），
    收敛为本模块单源，两处改为 import 引用 —— 维护点 2→1。
    """
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


def _accumulate_usage(total_input: int, total_output: int, response: Any, text: str,
                      total_cached: int = 0) -> tuple[int, int, int]:
    """累加 usage；只累加真实值，缺失（全 0）保持 0，不估算。

    response 可以是带 .usage 的 response（_call_model），也可以是 ModelUsage 本身
    （stream finish 事件已解析过）。两种情况都正确读取真实值。
    估算兜底在 _finalize_turn 层做（保证 journal 遥测非 0），不动 done/CLI 契约。
    2026-09-21: 返回三元组，第三项为累计 cached_tokens（0 = provider 未回传）。
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        # 传入对象本身可能已是 ModelUsage（stream 路径已解析）
        usage = response
    if hasattr(usage, "input_tokens"):
        return (
            total_input + (usage.input_tokens or 0),
            total_output + (usage.output_tokens or 0),
            total_cached + (getattr(usage, "cached_tokens", 0) or 0),
        )
    return total_input, total_output, total_cached


def _resolve_max_tool_rounds(engine: Any) -> int:
    """运行时工具轮次上限：config.max_turns 优先，兜底 AGENT_MAX_TOOL_ROUNDS。"""
    try:
        # P0-A 行为保真（2026-09-22）：迁移前主循环经 core.model_call 版本取值，
        # 每轮触发配置热重载检查；迁移副本必须保留该语义，否则 config.yaml
        # 的 max_turns 热更在主循环路径失效（第四回归修复）。
        from lingclaude.core.model_call import _maybe_hot_reload_config
        _maybe_hot_reload_config(engine)
    except Exception:
        pass
    for attr in ("config", "engine_config", "_config", "cfg"):
        cfg = getattr(engine, attr, None)
        val = getattr(cfg, "max_turns", None) if cfg is not None else None
        if isinstance(val, int) and val > 0:
            return val
    return AGENT_MAX_TOOL_ROUNDS


def run_call_model_loop(engine: Any, prompt: str) -> str:
    """`ModelCallMixin._call_model` 循环体（L0 逐字迁移，self→engine）。"""
    # R9 清理(2026-09-16): 原此处有 decision = engine._router.route(prompt),
    # 结果在下一行就被 _resolve_model_config 的返回值覆盖, 纯死计算, 删除。
    messages = engine._build_messages(prompt)
    tools = engine._build_openai_tools(query=prompt)
    resolved_config, decision = engine._resolve_model_config(prompt)
    # P1（2026-09-20，atomcode inflight 快照借鉴）: turn_start 即落 checkpoint
    # —— 「用户按下回车那一刻，数据已在磁盘上」。此前 checkpoint 只在工具轮
    # 完成后写（R5 阶段1），纯文本回复全程 0 落盘：首 token 前 kill/崩溃
    # → 用户输入与已建 messages 全丢。现于首模型请求前写 round=-1 checkpoint，
    # 崩溃后 resume_interrupted 至少能把本轮 prompt 恢复出来。
    try:
        engine._save_checkpoint(messages, -1, prompt, False, 0, 0)
    except Exception as e:  # noqa: BLE001 — inflight 落盘是 best-effort，不阻塞主流程
        logger.warning("turn_start checkpoint failed (non-blocking): %s", e)
    # LINGKERNEL_v1 D3: MV-1 上线 - 发模型前先落 log, 发完断言可重建
    mv1_seq = engine._log_model_request(prompt, messages, tools)
    # D8 双点校验之一: pre-send fail-closed (不可重建则不发)
    if not engine._pre_send_check(mv1_seq, messages):
        engine._log_to_flywheel("mv1_pre_send_blocked", f"seq={mv1_seq} request blocked", tool_name="model_request_log")
        return "[MV-1 fail-closed] 请求未通过可重建校验，已阻止发送并记录违规。"
    used_tools = False
    response = None
    total_input = 0
    total_output = 0
    total_cached = 0
    # ctx 口径采样（2026-09-21）：最后一个成功请求轮的 prompt_tokens，
    # 语义同 stream_call_model——toolbar ctx 分子的正确来源。
    last_round_input = 0
    consecutive_failures = 0

    loop_detector = _ToolLoopDetector()
    for round_idx in range(_resolve_max_tool_rounds(engine)):
        result = engine._provider.complete(
            tuple(messages), config=resolved_config, tools=tools,
        )
        # MV-1 断言: 实际发往模型的消息必须可从 log 重建
        engine._assert_model_visible(mv1_seq, messages)
        if result.is_error:
            consecutive_failures += 1
            engine._track_behavior(prompt, f"[模型调用失败] {result.error}", used_tools=False)
            if resolved_config:
                engine.hooks.record_provider_outcome(resolved_config, "error", result.error)
            if consecutive_failures >= engine.config.consecutive_failure_limit:
                return engine._hard_interrupt_message("model_call", consecutive_failures)
            continue

        response = result.data
        round_text = getattr(response, "content", "") or ""
        total_input, total_output, total_cached = _accumulate_usage(
            total_input, total_output, response, round_text, total_cached,
        )
        # ctx 口径采样：本请求轮真实 prompt_tokens（整包非增量），见初始化注释
        last_round_input = (
            getattr(getattr(response, "usage", None), "input_tokens", 0) or 0
        ) or last_round_input
        if resolved_config:
            engine.hooks.record_provider_outcome(resolved_config, "success")

        if not response.tool_calls:
            content = response.content
            if engine.hooks.should_hallucination_correct(prompt, used_tools, messages):
                content = engine.hooks.hallucination_correction(messages, content, tools, resolved_config)
                if content:
                    return engine._finalize_turn(prompt, content, used_tools, total_input, total_output, resolved_config, total_cached, ctx_input_tokens=last_round_input or None)
            engine._clear_checkpoint()
            return engine._finalize_turn(prompt, response.content, used_tools, total_input, total_output, resolved_config, total_cached, ctx_input_tokens=last_round_input or None)

        used_tools = True
        engine._tool_call_executor.process(response.tool_calls, messages, content=response.content)
        # R5 阶段1: 非 stream 工具轮也保存 checkpoint
        engine._save_checkpoint(
            messages, round_idx, prompt, used_tools, total_input, total_output,
        )
        for tc in response.tool_calls:
            engine.hooks.journal_append("tool_call", {
                "tool_call_id": tc.id, "name": tc.name, "arguments": tc.arguments,
            })

        round_error_count = sum(
            1 for tc in response.tool_calls
            if is_tool_error(engine._get_last_tool_output(messages, tc.id))
        )
        # 打转检测：全旧调用轮先警告注入、连续两次才熔断（区分原地打转与正常推进）。
        # 全错误轮不参与打转计数——那是连续失败熔断（硬中断）的辖域，语义优先。
        if round_error_count == 0:
            verdict = loop_detector.observe_round(
                [(tc.name, tc.arguments) for tc in response.tool_calls]
            )
            if verdict == "warn":
                messages.append(ModelMessage(role=MessageRole.USER, content=_LOOP_WARN_HINT))
            elif verdict == "abort":
                return engine._finalize_turn(
                    prompt,
                    (response.content or "") + _LOOP_ABORT_MSG,
                    used_tools, total_input, total_output, resolved_config, total_cached,
                    ctx_input_tokens=last_round_input or None,
                )
        if round_error_count == len(response.tool_calls) and round_error_count > 0:
            consecutive_failures += 1
            if consecutive_failures >= engine.config.consecutive_failure_limit:
                content = response.content or ""
                return engine._finalize_turn(
                    prompt,
                    content + engine._hard_interrupt_message("tool_loop_call", consecutive_failures),
                    used_tools, total_input, total_output, resolved_config, total_cached,
                    ctx_input_tokens=last_round_input or None,
                )
        else:
            consecutive_failures = 0

    content = response.content if response and response.content else "[达到最大工具调用轮次]"
    return engine._finalize_turn(prompt, content, used_tools, total_input, total_output, resolved_config, total_cached, ctx_input_tokens=last_round_input or None)


def run_stream_call_model_loop(engine: Any, prompt: str) -> Generator[dict[str, Any], None, None]:
    """`ModelCallMixin.stream_call_model` 循环体（L0 逐字迁移，self→engine）。"""
    messages = engine._build_messages(prompt)
    tools = engine._build_openai_tools()
    resolved_config, _ = engine._resolve_model_config(prompt)
    # P1（2026-09-20，atomcode inflight 快照借鉴）: turn_start 即落 checkpoint
    # —— 与 _call_model 同语义，覆盖流式路径（CLI 交互全部走这里）。
    # 此前流式路径 checkpoint 只在工具轮后写（R5 阶段1），纯文本回复
    # 全程 0 落盘；首 token 前 kill → 本轮输入丢失。
    try:
        engine._save_checkpoint(messages, -1, prompt, False, 0, 0)
    except Exception as e:  # noqa: BLE001 — best-effort，不阻塞主流程
        logger.warning("turn_start checkpoint failed (non-blocking): %s", e)
    used_tools = False
    response_content = ""
    total_input = 0
    total_output = 0
    total_cached = 0
    # ctx 口径采样（2026-09-21）：最后一个成功请求轮的 prompt_tokens。
    # 语义见流式循环内注释——toolbar ctx 分子的正确来源。
    last_round_input = 0
    consecutive_failures = 0
    # 2026-09-17 双写修复: 标记本 turn 的 _messages 镜像是否已由 engine
    # 写入（_finalize_turn 是唯一写入点）。CLI 层据 done.finalized 决定
    # 是否兜底 append —— 正常完成/超轮次路径 engine 已写，CLI 不再写；
    # 打转熔断（loop abort）engine 不写，CLI 兜底（旧行为保留）。
    finalized = False

    loop_detector = _ToolLoopDetector()
    for round_idx in range(_resolve_max_tool_rounds(engine)):
        round_text_parts: list[str] = []
        round_tool_calls: list[ToolCall] = []
        stream_error: str | None = None
        usage: Any = ModelUsage()

        # F12f:同回合失败换候选 — 最多尝试 2 个 provider。
        # 首选失败且尚无任何文本输出时,重新 resolve(TaskRouter round-robin
        # 前进即自动换下一候选)+ record_error,不让单点故障直接甩给用户。
        for cfg_attempt in range(2):
            round_text_parts.clear()
            round_tool_calls.clear()
            stream_error = None

            for event in engine._provider.stream_complete(
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
                    # N5a-v2: usage key 存在但值为 None 时 .get 默认值不生效,
                    # 显式 or 兜底, 防 provider 异常流炸穿整个 turn
                    usage = event.get("usage") or ModelUsage()
                    total_input, total_output, total_cached = _accumulate_usage(
                        total_input, total_output, usage,
                        "".join(round_text_parts), total_cached,
                    )
                elif event["type"] == "error":
                    stream_error = event["error"]

            if stream_error is None:
                # F12f:成功也记 success(与 _call_model 对称)
                engine.hooks.record_provider_outcome(resolved_config, "success")
                # ctx 口径采样（2026-09-21 修复 toolbar 949% 虚高）：
                # 本请求轮的真实 prompt_tokens = 当前实际发送的上下文体量，
                # 供 _finalize_turn 写 _last_turn_input（toolbar ctx 口径）。
                # usage.input_tokens 是本请求轮整包（非增量）。
                last_round_input = (
                    getattr(usage, "input_tokens", 0) or 0
                ) or last_round_input
                break

            # 失败:记录 provider 错误(熔断统计,与 _call_model 对称;F12j 硬错误立即熔断)
            failed_pname = engine.hooks.record_provider_outcome(
                resolved_config, "error", stream_error,
            )

            # 尚无输出 → 换下一候选重试一次
            if cfg_attempt == 0 and not round_text_parts:
                next_cfg, _ = engine._resolve_model_config(prompt)
                if next_cfg and (
                    (next_cfg.model, next_cfg.base_url)
                    != (resolved_config.model, resolved_config.base_url)
                ):
                    # P1-F2: 降级链健康度门禁——换候选前先探活目标节点，
                    # 不盲切（GLM 限额→free 池补位类场景：目标死了切过去
                    # 只是把失败换个地方）。门禁拒绝 → 不换，直接走本层
                    # 熔断/报错轨（目标探活为 hard_4xx 时其 slot 已被
                    # check_switch_target_health 拉入熔断，下轮 resolve 自会跳过）。
                    _sw_allowed, _sw_reason = (True, "门禁未启用")
                    try:
                        _sw_provider = engine._task_router.get_provider_name(
                            next_cfg.api_key, next_cfg.base_url
                        )
                        if _sw_provider:
                            _sw_allowed, _sw_reason = (
                                engine.hooks.switch_target_health(_sw_provider)
                            )
                    except Exception as _sw_err:  # 门禁自身故障不放大队失败
                        logger.debug("switch health gate error: %s", _sw_err)
                        _sw_allowed, _sw_reason = True, f"门禁异常放行: {_sw_err}"
                    if _sw_allowed:
                        yield {
                            "type": "status",
                            "message": (
                                f"[{failed_pname or '首选模型'}] 失败，"
                                f"切换备选 {next_cfg.model} 重试..."
                            ),
                        }
                        resolved_config = next_cfg
                        continue
                    logger.warning(
                        "降级门禁拦截切换 → %s: %s（维持原候选走熔断轨）",
                        next_cfg.model, _sw_reason,
                    )
                    yield {
                        "type": "status",
                        "message": f"[降级门禁] 备选 {next_cfg.model} 探活不健康，不切换",
                    }
                    break
            break

        if stream_error is not None:
            consecutive_failures += 1
            engine._track_behavior(prompt, f"[模型调用失败] {stream_error}", used_tools=False)
            if consecutive_failures >= engine.config.consecutive_failure_limit:
                yield {
                    "type": "hard_interrupt",
                    "message": engine._hard_interrupt_message("model_stream", consecutive_failures),
                }
                return
            yield {"type": "error", "error": stream_error}
            return

        round_content = "".join(round_text_parts)

        if not round_tool_calls:
            content = round_content
            if engine.hooks.should_hallucination_correct(prompt, used_tools, messages):
                yield {"type": "status", "message": "幻觉闭环修正中..."}
                corrected = engine.hooks.hallucination_correction(
                    messages, content, tools, resolved_config,
                )
                if corrected:
                    yield {"type": "text_delta", "text": corrected}
                    content = corrected
            final_content = engine._finalize_turn(prompt, content, used_tools, total_input, total_output, resolved_config, total_cached, ctx_input_tokens=last_round_input or None)
            engine._append_to_session_history(prompt, final_content)
            engine._learn_from_turn(prompt, final_content)
            # R5 阶段1: turn 正常完成 → 清 checkpoint + journal turn_end
            engine._clear_checkpoint()
            # 2026-09-17 双写修复: finalized=True 告知 CLI 层本 turn 的
            # _messages 镜像已由 _finalize_turn 写入（H20 统一点），CLI
            # 不得再 append —— 此前正常 done 与 CLI 兜底各写一遍，存档
            # 里每个回合成对翻倍（用户消息×2+回复×2）。
            finalized = True
            # P0: journal 兜底 — usage 全 0 时估算, 保证遥测非 0
            j_in, j_out = total_input, total_output
            if j_in == 0 and j_out == 0:
                j_in = max(1, _estimate_tokens(prompt))
                j_out = _estimate_tokens(final_content)
            engine.hooks.journal_append("turn_end", {
                "final_content_preview": final_content[:200],
                "total_input": j_in, "total_output": j_out,
            })
            yield {"type": "done", "content": final_content,
                   "usage": {"input_tokens": j_in, "output_tokens": j_out,
                             "cached_tokens": total_cached},
                   "finalized": finalized}
            return

        used_tools = True
        engine._behavior = engine._behavior.record_tool_calls(count=len(round_tool_calls))
        messages.append(ModelMessage(
            role=MessageRole.ASSISTANT,
            content=round_content,
            tool_calls=tuple(round_tool_calls),
        ))

        round_error_count = 0
        for tc in round_tool_calls:
            engine.hooks.journal_append("tool_call", {
                "tool_call_id": tc.id, "name": tc.name, "arguments": tc.arguments,
            })
            yield {"type": "tool_call_start", "name": tc.name, "arguments": tc.arguments}
            tool_output = engine._execute_tool_with_retry(tc.name, tc.arguments)
            is_error = is_tool_error(tool_output)
            if is_error:
                round_error_count += 1
                engine._behavior = engine._behavior.record_tool_calls(count=0, errors=1)
                engine.hooks.log_flywheel(
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
                content=_slim_tool_output(tc.name, tool_output),
                name=tc.name,
                tool_call_id=tc.id,
            ))
            engine.hooks.journal_append("tool_result", {
                "tool_call_id": tc.id,
                "output_preview": preview,
                "is_error": is_error,
            })
            # P2-9（2026-09-21）：工具结果登记 runtime_observation（H17 观测源）。
            # 成功证据 = 工具执行且非 error（exit=0/无异常）；测试类工具（pytest/
            # 编译）的成功是本回合「已完成/已通过」宣称的合法观测支撑。
            try:
                engine._get_evidence_ledger().record_observation(
                    "tool_result",
                    source=tc.name,
                    payload={"tool_call_id": tc.id, "is_error": is_error},
                    is_success_evidence=(not is_error),
                )
            except Exception:
                pass  # 观测登记 best-effort，不阻断主流程

        # R5 阶段1: 工具轮执行完 → 保存 checkpoint（stream 路径此前 0 调用）
        # 崩溃/kill 后 resume_interrupted 可从此处恢复，最多丢一轮
        engine._save_checkpoint(
            messages, round_idx, prompt, used_tools, total_input, total_output,
        )
        engine.hooks.journal_append("checkpoint", {
            "round_idx": round_idx, "messages_count": len(messages),
        })

        if round_error_count == len(round_tool_calls) and round_error_count > 0:
            consecutive_failures += 1
            if consecutive_failures >= engine.config.consecutive_failure_limit:
                yield {
                    "type": "hard_interrupt",
                    "message": engine._hard_interrupt_message("tool_loop_stream", consecutive_failures),
                }
                return
        else:
            consecutive_failures = 0

        # 打转检测（stream 版）：与 call_model 同策略——先警告注入，再熔断。
        # 全错误轮不参与打转计数（那是连续失败熔断的辖域，语义优先）。
        if round_error_count == 0:
            verdict = loop_detector.observe_round(
                [(tc.name, tc.arguments) for tc in round_tool_calls]
            )
            if verdict == "warn":
                messages.append(ModelMessage(role=MessageRole.USER, content=_LOOP_WARN_HINT))
            elif verdict == "abort":
                yield {"type": "text_delta", "text": _LOOP_ABORT_MSG}
                yield {"type": "done", "content": response_content + _LOOP_ABORT_MSG,
                       "usage": {"input_tokens": total_input, "output_tokens": total_output,
                          "cached_tokens": total_cached},
                       "finalized": False}
                return

        # 2026-09-15（会话问题重构 P1-1）: round 边界事件 —— 一轮完成、
        # 下轮即将开始（或结束）。CLI 层在此消费挂起队列：斜杠命令立即
        # 执行（改 CLI 状态/engine 配置，不进 messages，无跨线程竞态），
        # 普通文本记入 queued_next 插队（当前 tool 轮继续，turn 结束后
        # 作为下一轮输入直接执行，不等用户再打字）。纯观察点，不改引擎
        # 内部状态 —— 引擎继续自己的 tool 轮，CLI 只读队列。
        # 2026-09-17 修复 (response_content 恒空): 累积每轮文本 —— 原实现
        # 初始化后全函数体零赋值，loop-abort/超轮次 done 的 content 恒回退
        # 占位文案，最后一轮真实回复被丢弃。
        if round_content:
            response_content = (
                round_content if not response_content
                else response_content + "\n" + round_content
            )
        yield {
            "type": "round_end",
            "round_idx": round_idx,
            "has_tool_calls": bool(round_tool_calls),
            "error_count": round_error_count,
        }

    content = response_content or "[达到最大工具调用轮次]"
    final_content = engine._finalize_turn(prompt, content, used_tools, total_input, total_output, resolved_config, total_cached)
    # 超轮次路径 engine 已写镜像（同正常 done），CLI 不再兜底
    finalized = True
    yield {"type": "done", "content": final_content,
           "usage": {"input_tokens": total_input, "output_tokens": total_output,
                     "cached_tokens": total_cached},
           "finalized": finalized}
