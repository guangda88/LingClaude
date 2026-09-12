"""模型调用 mixin — _call_model/stream_call_model/幻觉闭环/MV-1 校验（从 query_engine 拆出，瘦身）。

QueryEngine 通过多继承接入本 mixin；方法内 self 即 QueryEngine 实例，
依赖其 _router/_provider/_build_messages/_finalize_turn 等成员。
"""
from __future__ import annotations

import logging
from typing import Any, Generator

from lingclaude.core.behavior import detect_intent, is_tool_intent
from lingclaude.core.session_journal import SessionJournal
from lingclaude.core.types import is_tool_error
from lingclaude.model.types import MessageRole, ModelMessage

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


def _accumulate_usage(total_input: int, total_output: int, response: Any, text: str) -> tuple[int, int]:
    """累加 usage；只累加真实值，缺失（全 0）保持 0，不估算。

    response 可以是带 .usage 的 response（_call_model），也可以是 ModelUsage 本身
    （stream finish 事件已解析过）。两种情况都正确读取真实值。
    估算兜底在 _finalize_turn 层做（保证 journal 遥测非 0），不动 done/CLI 契约。
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        # 传入对象本身可能已是 ModelUsage（stream 路径已解析）
        usage = response
    if hasattr(usage, "input_tokens"):
        return total_input + (usage.input_tokens or 0), total_output + (usage.output_tokens or 0)
    return total_input, total_output


_CFG_MTIME_CACHE: dict[str, float] = {}
_HOT_RELOAD_INTERVAL = 30.0  # 秒；节流，避免每轮读盘
_last_hot_reload_check = [0.0]


def _maybe_hot_reload_config(engine: Any) -> None:
    """配置热重载：config 文件 mtime 变化时重建 engine.config 的 max_turns。

    背景：改 config.yaml 后运行中的引擎不感知，旧 max_turns(=8) 导致反复
    撞「达到最大工具调用轮次」。此函数让长会话中改配置即时生效，无需重启。
    防御式：任何异常都静默吞掉，绝不影响主循环。
    """
    import dataclasses
    import time as _time

    now = _time.monotonic()
    if now - _last_hot_reload_check[0] < _HOT_RELOAD_INTERVAL:
        return
    _last_hot_reload_check[0] = now
    try:
        from lingclaude.core.config import find_config_path, load_config

        path = find_config_path()
        if path is None:
            return
        mtime = path.stat().st_mtime
        key = str(path)
        if _CFG_MTIME_CACHE.get(key) == mtime:
            return
        _CFG_MTIME_CACHE[key] = mtime
        cfg = load_config(path)
        cur = getattr(engine, "config", None)
        if cur is not None and getattr(cur, "max_turns", None) != cfg.engine.max_turns:
            object.__setattr__(engine, "config", dataclasses.replace(cur, max_turns=cfg.engine.max_turns))
            logger.info("配置热重载: max_turns -> %d", cfg.engine.max_turns)
    except Exception:
        pass


def _resolve_max_tool_rounds(engine: Any) -> int:
    """解析本轮 agent 循环的轮次上限。

    优先级：热重载后的实例 config.max_turns > 模块常量兜底。
    防御式读取：任何属性缺失/类型不对都回落到常量，绝不抛异常。
    """
    _maybe_hot_reload_config(engine)
    for attr in ("config", "engine_config", "_config", "cfg"):
        cfg = getattr(engine, attr, None)
        val = getattr(cfg, "max_turns", None) if cfg is not None else None
        if isinstance(val, int) and val > 0:
            return val
    return AGENT_MAX_TOOL_ROUNDS

_LOOP_WARN_HINT = (
    "[系统提示] 检测到与历史完全相同的工具调用（原地打转）。"
    "请改变方法/参数，或直接基于已有信息作答，不要重复同一调用。"
    "若下一轮仍出现完全相同调用，任务将被熔断中止。"
)
# 熔断后的继续指引：告诉用户会话仍存活、计数已清零、如何继续。
_LOOP_RECOVER_GUIDE = (
    "[继续指引] 熔断仅中止本轮工具循环，会话与输入通道完好，打转计数已清零。"
    "请直接重新提问即可继续，无需重启进程——"
    "① 改变目标/范围（例如'先只做A，别碰B'）；"
    "② 拆小任务，逐步推进，每步都有新产出；"
    "③ 给出明确方向（例如'按X方案做，先验证Y'）。"
)
_LOOP_ABORT_MSG = (
    "[循环检测] 连续两轮重复完全相同的工具调用（原地打转），任务已熔断停止。"
    "已完成的部分结果如上。"
    + _LOOP_RECOVER_GUIDE
)


class _ToolLoopDetector:
    """区分"原地打转"（重复相同调用）与"正常推进"（每轮有新产出）。

    判定：一轮工具调用的签名 (name, arguments) 全部在历史中出现过 = 打转轮。
    - 连续第 1 次打转 → warn（把纠偏提示注入下一轮消息，给模型改错机会）
    - 连续第 2 次打转 → abort（熔断）
    - 有任何新调用   → 正常推进，streak 清零
    纯文本轮（无工具调用）不参与判定（那是回答，不是循环）。

    5b：denial 熔断（独立分支,按 rule_id 聚合）。
    - 同 rule_id 连续 N 次触发 → 返回 "denial_warn" / "denial_abort"
    - 与现有打转检测并存:打转管 (name, arguments) 重复、denial 管 rule_id 重复
    - 阈值 R5_THRESHOLDS 字典:读工具放宽、写工具收紧（探索类/危险类分开）
    """

    def __init__(self) -> None:
        self._seen: set[tuple[str, str]] = set()
        self._streak = 0
        # 5b: rule_id 熔断状态
        self._denial_streak: dict[str, int] = {}  # rule_id -> 连续触发计数

    def observe_round(self, calls: list[tuple[str, str]]) -> str | None:
        """观察一轮调用，返回 'warn' / 'abort' / None。"""
        if not calls:
            return None
        sig = set(calls)
        has_new = any(c not in self._seen for c in sig)
        self._seen.update(sig)
        if has_new:
            self._streak = 0
            return None
        self._streak += 1
        if self._streak >= 2:
            return "abort"
        return "warn"

    def observe_denial(self, rule_id: str, tool_name: str, threshold: int = 2) -> str | None:
        """5b：观察一次 denial，按 rule_id 聚合。

        Args:
            rule_id: 5a 结构化规则标识（如 "config.deny_tools.exact"）
            tool_name: 关联工具名（用于日志 / 后续扩展;不计入判定）
            threshold: 触发熔断的连续次数（默认 2,与现有 abort 阈值一致）

        Returns:
            None              : 未达阈值,正常放行
            "denial_warn"     : 第 1 次,告警（不再注入 system note,本轮已经记录）
            "denial_abort"    : 达到阈值,熔断（暂停+升级语义见 §7.4）
        """
        self._denial_streak[rule_id] = self._denial_streak.get(rule_id, 0) + 1
        n = self._denial_streak[rule_id]
        if n >= threshold:
            return "denial_abort"
        return "denial_warn"

    def reset_denial(self, rule_id: str) -> None:
        """当一轮成功（无 denial）时,调用此清空对应 rule_id 计数。

        与 observe_round 的 has_new 清零 streak 一致——只要有一次成功就重置。
        """
        self._denial_streak.pop(rule_id, None)


# 5b 阈值表（按工具类型分维度调整;危险工具收紧,只读工具放宽）
# 默认门槛 2,与现有 _ToolLoopDetector 行为一致;后续可按工具类型 read/write 区分
_R5_THRESHOLDS: dict[str, int] = {
    "default": 2,
    # 未来可扩展:
    # "config.deny_tools.exact": {"bash": 1, "read": 5},  # bash 一次性熔断
    # "strict_mode.non_readonly": {"write": 1},  # 写工具一次性熔断
}


class ModelCallMixin:
    """模型调用 + MV-1 校验 + 幻觉闭环。"""

    def _get_journal(self) -> SessionJournal:
        """R5: 获取缓存的 SessionJournal 实例（持久化文件句柄复用）。

        session_id 或 journal_dir 变化时重建。
        """
        cache_key = (self.session_id, str(self._journal_dir))
        if not hasattr(self, "_journal_cache") or self._journal_cache_key != cache_key:
            self._journal_cache = SessionJournal(
                self.session_id, journal_dir=self._journal_dir,
            )
            self._journal_cache_key = cache_key
        return self._journal_cache

    def _journal_append(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """R5: journal append (best-effort, 不阻塞主流程)。"""
        try:
            self._get_journal().append(event_type, data)
        except Exception:
            logger.debug("journal append silently failed for %s", event_type)


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

        loop_detector = _ToolLoopDetector()
        for round_idx in range(_resolve_max_tool_rounds(self)):
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
                        self._task_router.record_error(pname, result.error)
                if consecutive_failures >= self.config.consecutive_failure_limit:
                    logger.warning(
                        "硬中断触发: 连续模型调用失败 %d 次，强制停止",
                        consecutive_failures,
                    )
                    self._log_to_flywheel("hard_interrupt", f"连续模型调用失败 {consecutive_failures} 次", tool_name="provider")
                    return f"[硬中断] 连续模型调用失败 {consecutive_failures} 次，自动停止。请检查模型服务状态。"
                continue

            response = result.data
            round_text = getattr(response, "content", "") or ""
            total_input, total_output = _accumulate_usage(
                total_input, total_output, response, round_text,
            )
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
                self._clear_checkpoint()
                return self._finalize_turn(prompt, response.content, used_tools, total_input, total_output, resolved_config)

            used_tools = True
            self._tool_call_executor.process(response.tool_calls, messages, content=response.content)
            # R5 阶段1: 非 stream 工具轮也保存 checkpoint
            self._save_checkpoint(
                messages, round_idx, prompt, used_tools, total_input, total_output,
            )
            for tc in response.tool_calls:
                self._journal_append("tool_call", {
                    "tool_call_id": tc.id, "name": tc.name, "arguments": tc.arguments,
                })

            round_error_count = sum(
                1 for tc in response.tool_calls
                if is_tool_error(self._get_last_tool_output(messages, tc.id))
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
                    return self._finalize_turn(
                        prompt,
                        (response.content or "") + _LOOP_ABORT_MSG,
                        used_tools, total_input, total_output, resolved_config,
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

        loop_detector = _ToolLoopDetector()
        for round_idx in range(_resolve_max_tool_rounds(self)):
            round_text_parts: list[str] = []
            round_tool_calls: list[ToolCall] = []
            stream_error: str | None = None

            # F12f:同回合失败换候选 — 最多尝试 2 个 provider。
            # 首选失败且尚无任何文本输出时,重新 resolve(TaskRouter round-robin
            # 前进即自动换下一候选)+ record_error,不让单点故障直接甩给用户。
            for cfg_attempt in range(2):
                round_text_parts.clear()
                round_tool_calls.clear()
                stream_error = None

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
                        # N5a-v2: usage key 存在但值为 None 时 .get 默认值不生效,
                        # 显式 or 兜底, 防 provider 异常流炸穿整个 turn
                        usage = event.get("usage") or ModelUsage()
                        total_input, total_output = _accumulate_usage(
                            total_input, total_output, usage,
                            "".join(round_text_parts),
                        )
                    elif event["type"] == "error":
                        stream_error = event["error"]

                if stream_error is None:
                    # F12f:成功也记 success(与 _call_model 对称)
                    if resolved_config:
                        pname = self._task_router.get_provider_name(
                            resolved_config.api_key, resolved_config.base_url,
                        )
                        if pname:
                            self._task_router.record_success(pname)
                    break

                # 失败:记录 provider 错误(熔断统计,与 _call_model 对称;F12j 硬错误立即熔断)
                if resolved_config:
                    pname = self._task_router.get_provider_name(
                        resolved_config.api_key, resolved_config.base_url,
                    )
                    if pname:
                        self._task_router.record_error(pname, stream_error)

                # 尚无输出 → 换下一候选重试一次
                if cfg_attempt == 0 and not round_text_parts:
                    next_cfg, _ = self._resolve_model_config(prompt)
                    if next_cfg and (
                        (next_cfg.model, next_cfg.base_url)
                        != (resolved_config.model, resolved_config.base_url)
                    ):
                        yield {
                            "type": "status",
                            "message": (
                                f"[{pname or '首选模型'}] 失败，"
                                f"切换备选 {next_cfg.model} 重试..."
                            ),
                        }
                        resolved_config = next_cfg
                        continue
                break

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
                # R5 阶段1: turn 正常完成 → 清 checkpoint + journal turn_end
                self._clear_checkpoint()
                # P0: journal 兜底 — usage 全 0 时估算, 保证遥测非 0
                j_in, j_out = total_input, total_output
                if j_in == 0 and j_out == 0:
                    j_in = max(1, _estimate_tokens(prompt))
                    j_out = _estimate_tokens(final_content)
                self._journal_append("turn_end", {
                    "final_content_preview": final_content[:200],
                    "total_input": j_in, "total_output": j_out,
                })
                yield {"type": "done", "content": final_content,
                       "usage": {"input_tokens": j_in, "output_tokens": j_out}}
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
                self._journal_append("tool_call", {
                    "tool_call_id": tc.id, "name": tc.name, "arguments": tc.arguments,
                })
                yield {"type": "tool_call_start", "name": tc.name, "arguments": tc.arguments}
                tool_output = self._execute_tool_with_retry(tc.name, tc.arguments)
                is_error = is_tool_error(tool_output)
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
                self._journal_append("tool_result", {
                    "tool_call_id": tc.id,
                    "output_preview": preview,
                    "is_error": is_error,
                })

            # R5 阶段1: 工具轮执行完 → 保存 checkpoint（stream 路径此前 0 调用）
            # 崩溃/kill 后 resume_interrupted 可从此处恢复，最多丢一轮
            self._save_checkpoint(
                messages, round_idx, prompt, used_tools, total_input, total_output,
            )
            self._journal_append("checkpoint", {
                "round_idx": round_idx, "messages_count": len(messages),
            })

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
                           "usage": {"input_tokens": total_input, "output_tokens": total_output}}
                    return

        content = response_content or "[达到最大工具调用轮次]"
        final_content = self._finalize_turn(prompt, content, used_tools, total_input, total_output, resolved_config)
        yield {"type": "done", "content": final_content,
               "usage": {"input_tokens": total_input, "output_tokens": total_output}}

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
