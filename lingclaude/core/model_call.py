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
from lingclaude.core.model_types import MessageRole, ModelMessage

logger = logging.getLogger(__name__)

# 模型调用最大工具轮次（query_engine 模块级常量迁移至此，避免循环导入）
# 注意：这只是"未配置时的兜底值"。运行时上限由 _resolve_max_tool_rounds()
# 从实例 config.max_turns（config.yaml → agent.max_turns）读取。
# P0-A L0 批次 5：单源已迁 engine/loop/loop_body。S3 守卫禁止 core 模块级
# import engine，故用 PEP 562 模块级 __getattr__ 懒导出（保持 mc.X 消费面）。
# P0-A L0 批次 5（2026-09-22）：循环纯函数助手单源迁 engine/loop/loop_body.py，
# 本模块回 import re-export 保持既有引用面（submission/query_engine/tests 消费）。
# 注意：这只是"未配置时的兜底值"。运行时上限由 _resolve_max_tool_rounds()
# 从实例 config.max_turns（config.yaml → agent.max_turns）读取。
# P0-A L0 批次 5（2026-09-22）：循环体迁 engine/loop/loop_body.py。
# core 不再模块级再导出 engine 符号（S3 守卫）；消费方直切 engine/loop 正身，
# M3 行级台账与 G1 白名单同源收缩。
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
    from lingclaude.engine.loop.loop_body import AGENT_MAX_TOOL_ROUNDS  # P0-A: 兜底常量随循环体迁出, S3 合规懒加载
    return AGENT_MAX_TOOL_ROUNDS

# P0-A L0 批次 3（2026-09-22）：打转检测器 + 循环常量迁 engine/loop/tool_loop_detector.py。
# core 不再模块级再导出 engine 符号（S3 守卫）；消费方直切 engine/loop 正身，
# M3 行级台账与 G1 白名单同源收缩。


class ModelCallMixin:
    """模型调用 + MV-1 校验 + 幻觉闭环。"""

    @property
    def hooks(self) -> "Any":
        """第 0 步（2026-09-21）：循环体治理钩子注入面（loop_seam.LoopHooks）。

        默认来自 wiring 装配的 self._loop_hooks（DefaultLoopHooks(self)，行为零
        变化）；测试 / headless / 热更可替换为 fake 实现驱动同一循环体。
        缺失时惰性构造 DefaultLoopHooks(self) 兜底（裸构造 / 老引擎兼容）。
        """
        h = getattr(self, "_loop_hooks", None)
        if h is None:
            from lingclaude.engine.loop.hooks import DefaultLoopHooks
            self._loop_hooks = DefaultLoopHooks(self)
            h = self._loop_hooks
        return h

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

    def _get_evidence_ledger(self) -> Any:
        """P2-9（2026-09-21）: 获取缓存的 EvidenceLedger（H17 协议化观测源）。

        工具结果进历史时登记 runtime_observation（成功证据=测试结果/exit=0），
        裸完成宣称打回时查 ledger——有成功观测则放行，无观测 fail-closed。
        session 变化时重建（与会话隔离）。
        """
        from lingclaude.core.evidence_protocol import EvidenceLedger
        cache_key = self.session_id
        if not hasattr(self, "_evidence_ledger") or getattr(self, "_evidence_ledger_key", None) != cache_key:
            self._evidence_ledger = EvidenceLedger()
            self._evidence_ledger_key = cache_key
        return self._evidence_ledger

    def _journal_append(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """R5: journal append (best-effort, 不阻塞主流程)。"""
        try:
            self._get_journal().append(event_type, data)
        except Exception:
            logger.debug("journal append silently failed for %s", event_type)

    def _record_provider_outcome(
        self, cfg: Any, kind: str, error: str | None = None,
    ) -> str | None:
        """记录 provider 成败到 TaskRouter 熔断统计（真重复收敛：原 4 处逐字）。

        kind: "success" → record_success；"error" → record_error(error)
        返回 pname（找不到时 None）；cfg 为空时跳过。
        """
        if not cfg:
            return None
        pname = self._task_router.get_provider_name(cfg.api_key, cfg.base_url)
        if not pname:
            return None
        if kind == "success":
            self._task_router.record_success(pname)
        else:
            self._task_router.record_error(pname, error or "")
        return pname

    def _hard_interrupt_message(
        self, scope: str, consecutive_failures: int,
    ) -> str:
        """统一硬中断消息生成 + 日志记录（维护点 4→1，逐点保真）。

        scope 编码 4 种差异（logger 文案 / flywheel 有无 / 消息文本均不相同，
        收敛为单源但逐点保留原行为——尤其 stream 两处原本不记 flywheel）：
          "model_call"      → 非 stream provider 失败（有 flywheel）
          "tool_loop_call"  → 非 stream 工具全错（有 flywheel）
          "model_stream"    → stream provider 失败（无 flywheel，保真）
          "tool_loop_stream"→ stream 工具全错（无 flywheel，保真）
        返回给调用点的中断消息文本（调用点负责 return / yield 各自形态）。
        """
        if scope == "model_call":
            logger.warning("硬中断触发: 连续模型调用失败 %d 次，强制停止", consecutive_failures)
            self._log_to_flywheel(
                "hard_interrupt", f"连续模型调用失败 {consecutive_failures} 次", tool_name="provider",
            )
            return f"[硬中断] 连续模型调用失败 {consecutive_failures} 次，自动停止。请检查模型服务状态。"
        if scope == "tool_loop_call":
            logger.warning("硬中断触发: 连续工具失败 %d 次，强制停止", consecutive_failures)
            self._log_to_flywheel(
                "hard_interrupt", f"连续工具失败 {consecutive_failures} 次", tool_name="tool_loop",
            )
            return f"\n[硬中断] 连续工具调用失败 {consecutive_failures} 次，自动停止。"
        if scope == "model_stream":
            logger.warning("硬中断触发(stream): 连续模型调用失败 %d 次，强制停止", consecutive_failures)
            return f"连续模型调用失败 {consecutive_failures} 次，自动停止。请检查模型服务状态。"
        # tool_loop_stream
        logger.warning("硬中断触发(stream): 连续工具失败 %d 次，强制停止", consecutive_failures)
        return f"连续工具调用失败 {consecutive_failures} 次，自动停止。"


    def _call_model(self, prompt: str) -> str:
        """P0-A L0 批次 5（2026-09-22）：循环体迁 engine/loop/loop_body.run_call_model_loop。

        本方法保留为薄委托壳（L0 语义等价：QueryEngine 经 mixin 调用面不变，
        循环体逐字迁移、self→engine 显式参数）。契约 §二：core 只允许
        from lingclaude.engine.loop import <白名单名>。
        """
        from lingclaude.engine.loop.loop_body import run_call_model_loop
        return run_call_model_loop(self, prompt)

    def _log_model_request(self, prompt: str, messages: list, tools: Any) -> int:
        """MV-1 L-a: model-visible means logged. 返回 seq 供事后断言。"""
        try:
            tool_names = tuple(
                t.get("function", {}).get("name", "")
                for t in (tools or [])
                if isinstance(t, dict)
            )
            snapshot = self._snapshot_messages(messages)
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

    @staticmethod
    def _snapshot_messages(messages: list) -> list:
        """MV-1 三处校验共用的消息快照构造（单源，防逐字漂移）。"""
        return [
            m if isinstance(m, dict) else getattr(m, "to_dict", lambda: {"role": str(getattr(m, "role", "user")), "content": str(getattr(m, "content", ""))})()
            for m in messages
        ]

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
            snapshot = self._snapshot_messages(messages)
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
            snapshot = self._snapshot_messages(messages)
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
        """P0-A L0 批次 5（2026-09-22）：循环体迁 engine/loop/loop_body.run_stream_call_model_loop。

        薄委托壳（L0 语义等价），循环体逐字迁移见 loop_body.py。
        """
        from lingclaude.engine.loop.loop_body import run_stream_call_model_loop
        yield from run_stream_call_model_loop(self, prompt)

    def _should_hallucination_correct(
        self, prompt: str, used_tools: bool, messages: list | None = None,
    ) -> bool:
        bm = self._behavior
        if bm.hallucination_risk >= 0.3 and not used_tools:
            intent = detect_intent(prompt)
            if is_tool_intent(intent):
                return True
        # P1-3 (2026-09-19, 幻觉调研第二批): 第二判定轨——凭空完成声明打回。
        # k3 形态（H17）+ H3 路由切换断裂：模型无工具调用却输出「已提交
        # abc1234 / 测试全绿 / 验证全部通过 / 已开线程 agent_id: xxx」，
        # 原 risk≥0.3 条件完全漏网（risk 是慢变量，单轮凭空声明拉不动）。
        # 本轮无任何工具调用 + 命中高危完成式声明 → 直接打回重验。
        # 打回有成本（一轮 LLM 调用），模式取高精度完成式强信号，不做推断文本。
        if not used_tools and messages:
            try:
                from lingclaude.core.prior_verifier import detect_bare_completion_claims
                # 声明散落在本 turn 全部 assistant 输出里，不只最后一轮
                round_text = "\n".join(
                    str(m.content) for m in messages
                    if str(getattr(getattr(m, "role", None), "value", getattr(m, "role", ""))) == "assistant"
                )
                if not round_text:
                    return False
                claims = detect_bare_completion_claims(round_text)
                if not claims:
                    return False
                # P2-9（2026-09-21）：H17 升格为证据边界协议——裸完成宣称打回前
                # 先查 EvidenceLedger：本回合若已有成功观测（工具执行且非 error，
                # 如 pytest/编译 exit=0）则宣称有观测支撑，放行不打回；无成功观测
                # 才 fail-closed 打回（H17 裸宣称协议化核心）。
                try:
                    ledger = self._get_evidence_ledger()
                    success_obs = [o for o in ledger.all() if o.is_success_evidence]
                    if success_obs:
                        logger.info(
                            "P2-9 H17 协议化: 裸完成宣称 %d 条但有 %d 条成功观测支撑，放行",
                            len(claims), len(success_obs),
                        )
                        return False
                except Exception:
                    logger.debug("P2-9 证据账本查询失败（fail-closed 保持原打回）", exc_info=True)
                # 无成功观测 → 原语义打回（高精度完成式强信号）
                logger.warning(
                    "P1-3 凭空完成声明打回: %d 条 (%s)",
                    len(claims),
                    "; ".join(f"{c[:40]}[{k}]" for c, k in claims[:3]),
                )
                return True
            except Exception as e:  # 探测器故障绝不阻断主流程（fail-soft）
                logger.debug("P1-3 bare-completion detector error: %s", e)
        return False

    def _hallucination_correction(
        self,
        messages: list,
        original_response: str,
        tools: tuple[dict[str, Any], ...] | None,
        config: Any,
        depth: int = 0,
    ) -> str | None:
        from lingclaude.engine.loop.loop_body import _slim_tool_output  # P0-A: core→engine 消费边, M3 行级豁免
        MAX_CORRECTION_DEPTH = 2
        if depth >= MAX_CORRECTION_DEPTH:
            logger.warning("幻觉闭环达到最大递归深度，放弃修正")
            return None

        from lingclaude.core.model_types import ModelMessage, MessageRole

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
        # P1-3 (2026-09-19): 凭空完成声明打回时，修正 prompt 点名具体声明 ——
        # 让模型知道哪句话是凭空的（提交哈希/测试全绿/开线程），重答时必须
        # 附真实工具证据或撤回声明，而不是换个说法重复一遍。
        try:
            from lingclaude.core.prior_verifier import detect_bare_completion_claims
            _claims = detect_bare_completion_claims(original_response or "")
            if _claims:
                _named = "; ".join(f"「{c}」" for c, _k in _claims[:4])
                correction_prompt = (
                    f"⚠ 系统干预: 你的回复包含未经工具执行的凭空完成式声明: {_named}。"
                    "这些动作你没有实际执行过。请立即使用真实工具（bash/read/write 等）完成"
                    "或验证对应动作，然后基于真实结果重新回答；无法执行时必须明确撤回这些声明。"
                )
        except Exception:  # 点名失败不影响打回主流程
            pass
        messages.append(ModelMessage(role=MessageRole.ASSISTANT, content=original_response))
        messages.append(ModelMessage(role=MessageRole.USER, content=correction_prompt))

        if self._provider is None or tools is None:
            return None

        result = self._provider.complete(tuple(messages), config=config, tools=tools)
        if result.is_error:
            return None

        response = result.data
        if response.tool_calls:
            # P17 修复 (2026-09-14): 幻觉修正路径工具执行必须与主路径对称写 journal——
            # 否则 _finalize_turn 从 journal 取 tool_evidence 为空，修正轮真实执行的
            # bash/write 等会被 prior_verifier 当「无据」打 ⚠ [工具结果未验证]（误报）。
            # 2026-09-17 修复 (消息序契约): assistant 消息移出循环 —— 原实现在
            # for 循环体内 append，N 个 tool_calls 产生 N 条重复 ASSISTANT 且与
            # TOOL 结果交错，违反 OpenAI/Anthropic「assistant.tool_calls 必须紧跟
            # 其全部 tool 结果」契约。
            messages.append(ModelMessage(
                role=MessageRole.ASSISTANT,
                content=response.content,
                tool_calls=response.tool_calls,
            ))
            for tc in response.tool_calls:
                self.hooks.journal_append("tool_call", {
                    "tool_call_id": tc.id, "name": tc.name, "arguments": tc.arguments,
                })
                tool_output = self._execute_tool_with_retry(tc.name, tc.arguments)
                preview = tool_output[:200] if len(tool_output) > 200 else tool_output
                self.hooks.journal_append("tool_result", {
                    "tool_call_id": tc.id,
                    "output_preview": preview,
                    "is_error": is_tool_error(tool_output),
                })
                messages.append(ModelMessage(
                    role=MessageRole.TOOL,
                    content=_slim_tool_output(tc.name, tool_output),
                    name=tc.name,
                    tool_call_id=tc.id,
                ))
            final = self._provider.complete(tuple(messages), config=config, tools=tools)
            if final.is_ok and final.data.content:
                self._behavior = self._behavior.record_tool_calls(count=len(response.tool_calls))
                return final.data.content
            return None

        return response.content if response.content else None


# PEP 562 模块级懒导出（S3 合规）：AGENT_MAX_TOOL_ROUNDS 单源在
# engine/loop/loop_body.py，此处仅保 re-export 消费面（mc.AGENT_MAX_TOOL_ROUNDS），
# 不落模块级 import（S3 守卫：core 模块级倒装=0）。
def __getattr__(name: str) -> Any:
    if name == "AGENT_MAX_TOOL_ROUNDS":
        from lingclaude.engine.loop.loop_body import AGENT_MAX_TOOL_ROUNDS as _v
        return _v
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
