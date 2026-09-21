"""L5/audit 块 — 从 query_engine.py 拆出"""

import hashlib
import logging
from pathlib import Path
from uuid import uuid4

from lingclaude.engine.loop.l5_conversation_loop import L5ConversationLoop, L5ConversationConfig, L5RoundResult
from lingclaude.core.degradation_detector import extract_tool_calls_from_text

logger = logging.getLogger(__name__)

L5Orchestrator = None
L5Context = None
R5SignalSourceMock = None
Z3PredicateMock = None
DeclarationConsistencyChecker = None
_intent_precheck = None
_R5KBConflictSource = None

try:
    from lingyuan.l5_orchestrator import L5Orchestrator as _L5O, L5Context as _L5C, R5SignalSourceMock as _R5M, Z3PredicateMock as _Z3M
    L5Orchestrator = _L5O
    L5Context = _L5C
    R5SignalSourceMock = _R5M
    Z3PredicateMock = _Z3M
except ImportError:
    pass
try:
    from z3_declaration_consistency import DeclarationConsistencyChecker as _DCC
    DeclarationConsistencyChecker = _DCC
except ImportError:
    pass
try:
    from lingyuan.l5_orchestrator import intent_precheck as _ip
    _intent_precheck = _ip
except ImportError:
    pass
try:
    from experiments.r5_kb_conflict import R5KBConflictSource as _R5KB
    _R5KBConflictSource = _R5KB
except ImportError:
    pass


class L5Auditor:
    def __init__(self, engine) -> None:
        self._engine = engine

    def check_degradation(self, prompt: str, output: str) -> None:
        is_tool = any(
            kw in prompt.lower()
            for kw in ("tool_name", "tool_call", "function", "<tool>")
        )
        if not is_tool:
            return

        calls = extract_tool_calls_from_text([prompt, output], self._engine._total_messages_sent)
        for call in calls:
            new_alerts = self._engine._degradation_detector.record_call(call)
            self._engine._degradation_alerts.extend(new_alerts)
            for alert in new_alerts:
                logger.warning(
                    "退化检测 [%s] @ msg#%d: %s",
                    alert.signal.value,
                    alert.msg_index,
                    alert.detail,
                )

    def get_degradation_alerts(self) -> list:
        return list(self._engine._degradation_alerts)

    def get_degradation_health(self) -> dict:
        return self._engine._degradation_detector.get_health_indicators()

    def apply_l5_audit(self, prompt: str, output: str) -> str:
        """L5对话层循环审计 (轻量级) — 关键词触发+记录, 不调LLM

        仅当 should_trigger 命中时,在 audit_history 记录 round 0 placeholder
        (说明"检测到高风险关键词,待三方联调启用真实审视")。
        完整LLM审视通过 self.run_l5_audit_full() 显式调用,避免主流程延迟,
        保护现有1406测试时序。

        真正的"用户sure?代码化"路径: 等三方(self-NLI/R5/Z3)联调就绪后,
        run_l5_audit_full 在 submit() 末尾被自动启用。
        """
        if not self._engine._l5_loop.should_trigger(prompt):
            return output
        try:
            rules = self._engine._collect_relevant_rules()
            tool_log = self._engine._collect_tool_call_log()
            self._engine._l5_loop._audit_history.append(
                L5RoundResult(
                    round_num=0,
                    response=output,
                    consistency_score=0.0,
                    declared_rules=rules,
                    actual_actions=tool_log,
                    inconsistencies=[
                        "L5 trigger detected — full audit pending 三方联调 (lingyuan.l5_orchestrator + R5 M2/M3 + z3_declaration_consistency)"
                    ],
                )
            )
            logger.info(
                "L5对话层触发: prompt含高风险关键词, round 0 placeholder 已记录"
            )
        except Exception as e:  # noqa: BLE001 — 审计失败不阻塞主流程
            logger.warning("L5 audit placeholder failed: %s", e)
        return output

    def ensure_l5_orchestrator(self):
        """Lazy init 三方 L5Orchestrator (R5 mock + Z3 真实 + self-NLI 占位)"""
        if self._engine._l5_orchestrator is not None:
            return True
        if L5Orchestrator is None:
            logger.warning("L5Orchestrator 不可用 (lingminopt 未安装), fallback 到 L5ConversationLoop")
            return False
        try:
            # R5 mock (灵研 7/22 出真实模块)
            r5_source = R5SignalSourceMock()
            # Z3 真实 checker (adapter 到 Protocol)
            if DeclarationConsistencyChecker is not None:
                checker = DeclarationConsistencyChecker()
                class _Z3Adapter:
                    def validate(self, claim_rules, actual_actions):
                        cr = checker.check(claim_rules, actual_actions)
                        ratio = getattr(cr, 'ratio', None) or getattr(cr, 'consistency_ratio', 1.0)
                        missing = getattr(cr, 'unmatched_declared', set())
                        class _R:
                            def __init__(self):
                                self.ratio = ratio
                                self.missing = missing
                        return _R()
                z3 = _Z3Adapter()
            else:
                z3 = Z3PredicateMock()
            ctx = L5Context(session_id=self._engine.session_id, round=0)
            self._engine._l5_orchestrator = L5Orchestrator(
                r5_source=r5_source,
                z3_predicate=z3,
                l5_context=ctx,
            )
            return True
        except Exception as e:
            logger.warning("L5Orchestrator 初始化失败: %s", e)
            self._engine._l5_orchestrator = None
            return False

    def run_l5_audit_full(
        self,
        prompt: str,
        output: str,
        rules: list | None = None,
        tool_log: list | None = None,
    ) -> str:
        """L5对话层循环完整审视 — 调三方 L5Orchestrator + Z3 + R5

        失败时 graceful fallback 到 L5ConversationLoop (自实现) 或原 output。
        """
        if self.ensure_l5_orchestrator() and self._engine._l5_orchestrator is not None:
            return self._run_l5_orchestrator(prompt, output, rules, tool_log)
        # fallback: 自实现 L5ConversationLoop
        rules = rules if rules is not None else self._engine._collect_relevant_rules()
        tool_log = tool_log if tool_log is not None else self._engine._collect_tool_call_log()
        try:
            return self._engine._l5_loop.run(
                user_intent=prompt,
                rules=rules,
                tool_call_log=tool_log,
                model_call=self._engine._call_model,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("L5 fallback audit failed: %s", e)
            return output

    def _run_l5_orchestrator(
        self,
        prompt: str,
        output: str,
        rules: list | None = None,
        tool_log: list | None = None,
    ) -> str:
        """通过灵极优 L5Orchestrator 做完整三方审视

        先调 FactVerifier 做事实校验 (T1), 注入 UNVERIFIED 到 inconsistencies。
        """
        rules = rules or self._engine._collect_relevant_rules()
        tool_log = tool_log or self._engine._collect_tool_call_log()
        orch = self._engine._l5_orchestrator

        # T1: 事实校验 — 查灵知 KG 验证 claim 是否有来源
        fact_check_warnings: list = []
        try:
            from lingclaude.core.fact_checker import KGFactChecker, ClaimExtractor, audit_response
            checker = KGFactChecker()
            fc_result = audit_response(output, checker=checker)
            if fc_result.get("warning"):
                fact_check_warnings = [
                    f"事实校验: {fc_result['warning']}",
                ]
                for c in fc_result.get("claims", []):
                    if not c.get("found"):
                        fact_check_warnings.append(
                            f"  - 无来源 claim: \"{c['text']}\" (confidence={c['confidence']:.2f})"
                        )
                logger.info("T1 事实校验: %d/%d 通过", 
                    fc_result["total"] - fc_result["failed"], fc_result["total"])
        except Exception as e:
            logger.warning("T1 事实校验失败 (不阻塞): %s", e)

        try:
            orch.l5_context.round = 1
            result = orch.validate(
                claim=prompt,
                evidence=tool_log,
                actual_rounds=1,
                expected_rounds=4,
            )
            # 整合 T1 警告到审计结果
            if fact_check_warnings:
                if hasattr(result, 'detail') and isinstance(result.detail, dict):
                    result.detail["fact_check"] = fact_check_warnings
                logger.warning("L5 + T1: consistency=%.3f, fact_check_issues=%d",
                    result.consistency, len(fact_check_warnings))
            if result.should_early_exit:
                return output
            # 如果有事实校验问题, 强制标记修正
            if result.should_fix or fact_check_warnings:
                logger.info("L5触发修正: consistency=%.3f, fact_warnings=%d",
                    result.consistency, len(fact_check_warnings))
                return output  # 等三方全量联调后启用自动修正
            logger.info(
                "L5三方审计: round=1 consistency=%.3f contrib=%s",
                result.consistency, result.contributions,
            )
            return output
        except Exception as e:  # noqa: BLE001
            logger.warning("L5Orchestrator audit failed: %s", e)
            return output

    def collect_relevant_rules(self) -> list:
        """从CRUSH.md/CRUSH核心规则提取关键规则 (L5 audit用)"""
        return [
            "优先使用code_search而非grep (行号+上下文)",
            "使用execute_command而非裸bash (含caller校验)",
            "写前查权限 / 读后必验证 / 写完必核对",
            "handover铁律: 读后必现场验证, 读完必回写",
            "用户sure? = 检查声明vs行为一致性",
            "工具调用前必查规则, 不假设",
            "敏感操作前置, 大操作分步",
        ]

    def collect_tool_call_log(self) -> list:
        """收集本轮工具调用记录 (L5 audit白箱证据)

        优先读真实工具调用日志 _tool_call_log（wiring 注册，tool_executor 写入）；
        fallback 到 _degradation_alerts（旧实现，仅覆盖退化场景）。
        """
        real = getattr(self._engine, "_tool_call_log", None)
        if real:
            return list(real[-64:])
        return [
            f"{a.signal.value}: {a.detail}"
            for a in self._engine._degradation_alerts[-10:]
        ]

    def check_behavior(self, prompt: str) -> str | None:
        """T0: 行为校验 (零推理成本)

        在进入主流程前检查明显行为问题。借鉴 AtomCode VerifyCadenceHook。
        """
        try:
            from lingclaude.core.behavior_check import check
            tool_history = [
                {"tool_name": m.split(":")[0], "command": m}
                for m in self._engine._messages[-20:]
            ]
            result = check(tool_history=tool_history, output=prompt)
            if not result.passed:
                nudge_text = "\n".join(f"⚠️ {n}" for n in result.nudges)
                if result.should_block:
                    logger.warning("T0 行为校验拦截: %s", nudge_text)
                    return f"[行为校验] 检测到可能的问题:\n{nudge_text}"
                logger.info("T0 行为校验提示: %s", nudge_text)
        except Exception as e:
            logger.warning("T0 行为校验失败 (不阻塞): %s", e)
        return None

    def get_l5_audit_history(self) -> list:
        """暴露L5 audit_history, 供60s轮询反馈闭环使用"""
        return list(self._engine._l5_loop.audit_history)

    def check_intent(self, prompt: str) -> str | None:
        """T2: 意图确认 (round 0)"""
        if _intent_precheck is None:
            return None
        if not self._engine._l5_loop.should_trigger(prompt):
            return None
        try:
            result = _intent_precheck(prompt, prompt, llm=None, threshold=0.5)
            if result is not None and not result.passed:
                logger.warning("T2 意图确认不匹配 (similarity=%.2f)", result.similarity)
                return (f"[意图确认] 我理解的是「{result.restated_intent}」，"
                        f"和您说的「{result.user_intent}」有差异。请确认是否继续。")
        except Exception as e:
            logger.warning("T2 意图确认失败 (不阻塞): %s", e)
        return None

    def check_entity_conflict(self, prompt: str, output: str) -> str:
        """T3: 实体冲突检查 (输出后) — 仅日志, 不修改 output"""
        if _R5KBConflictSource is None:
            return output
        try:
            detector = _R5KBConflictSource()
            result = detector.ent_kb_conflict(claim=output, evidence=[])
            if result is not None and hasattr(result, "conflict_score") and result.conflict_score > 0.6:
                logger.warning("T3 实体冲突: score=%.2f ungrounded=%s",
                    result.conflict_score, getattr(result, "ungrounded_claims", []))
        except Exception as e:
            logger.warning("T3 实体冲突检查失败 (不阻塞): %s", e)
        return output

    def check_l1_handover(self) -> None:
        if self._engine._total_messages_sent < self._engine.L1_MESSAGE_THRESHOLD:
            return
        if self._engine._total_messages_sent == self._engine._l1_last_triggered_at:
            return
        self._engine._l1_last_triggered_at = self._engine._total_messages_sent
        keep_pairs = 6
        recent = self._engine._messages[-keep_pairs:] if len(self._engine._messages) > keep_pairs else self._engine._messages[:]
        summary_parts = self._engine._messages[:-keep_pairs] if len(self._engine._messages) > keep_pairs else []
        summary_text = ""
        if summary_parts:
            for chunk in summary_parts[:20]:
                summary_text += chunk[:200] + "\n"
            summary_text = summary_text[:2000]
        injection = f"[L1交接刷新 @ msg#{self._engine._total_messages_sent}]\n{summary_text}"
        self._engine._messages[:] = [injection] + recent
        if len(self._engine._conversation) > keep_pairs:
            conv_recent = self._engine._conversation[-keep_pairs:]
            self._engine._conversation[:] = [("system", injection)] + conv_recent
        # 2026-09-21 (P1 cache_epoch): L1 裁剪重写了历史头部字节 → 前缀缓存失效。
        # epoch 单调 +1（对齐 atomcode compaction 语义），turn 边界由调用方保证
        # （本方法只在 turn 收尾时被调用）。
        self._engine._history_epoch += 1
        handover_path = Path.home() / ".lingclaude" / "handover.md"
        if handover_path.exists():
            self._engine._l1_handover_checksum = hashlib.md5(
                handover_path.read_bytes(), usedforsecurity=False
            ).hexdigest()
        logger.info("L1 handover refresh triggered at message #%d", self._engine._total_messages_sent)

    def check_l2_restart(self) -> bool:
        if self._engine._total_messages_sent < self._engine.L2_MESSAGE_THRESHOLD:
            return False
        handover_path = Path.home() / ".lingclaude" / "handover.md"
        handover_text = ""
        if handover_path.exists():
            handover_text = handover_path.read_text(encoding="utf-8")[:3000]
            current_checksum = hashlib.md5(
                handover_path.read_bytes(), usedforsecurity=False
            ).hexdigest()
            if self._engine._l1_handover_checksum and current_checksum == self._engine._l1_handover_checksum:
                logger.warning(
                    "L2: handover.md未更新(L1后无变化) checksum=%s",
                    current_checksum[:8],
                )
        old_session = self._engine.session_id
        self._engine._save_session_state()
        self._engine._messages.clear()
        self._engine._conversation.clear()
        self._engine._transcript.clear()
        self._engine.session_id = uuid4().hex[:16]
        self._engine._total_messages_sent = 0
        self._engine._l1_last_triggered_at = -1
        self._engine._l1_handover_checksum = ""
        self._engine._degradation_detector.reset()
        # 2026-09-21 (P1 cache_epoch): L2 清空历史 = 最大粒度的缓存失效，单调 +1。
        self._engine._history_epoch += 1
        injection = (
            f"[L2会话重启 — 旧会话 {old_session}]\n"
            f"{handover_text}"
        )
        self._engine._messages.append(injection)
        self._engine._conversation.append(("system", injection))
        logger.info("L2 session restart: %s -> %s", old_session, self._engine.session_id)
        return True
