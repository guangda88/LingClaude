"""Turn learner — records per-turn learnings into KnowledgeBase.

Extracted from QueryEngine._learn_from_turn (LINGKERNEL_v1 D6).
Owns the KnowledgeBase write loop for hallucination risk, tool errors,
user corrections, and session milestones. All failures are swallowed and
logged as warnings to never break the main turn flow.
"""

from __future__ import annotations

import logging
from typing import Any
from datetime import datetime as _dt

logger = logging.getLogger(__name__)


def record_turn_learnings(
    prompt: str,
    behavior: Any,
    messages: list[Any],
    session_id: str,
    response: str = "",
) -> None:
    """Record per-turn learning signals into KnowledgeBase.

    Args:
        prompt: The user prompt for this turn (truncated for rule context).
        behavior: BehaviorMetrics instance (has hallucination_risk, tool_error_count,
            corrections_received, frustration_rate).
        messages: Current conversation message list (used for turn count).
        session_id: Current session ID (truncated for rule IDs).
    """
    try:
        from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase
        from lingclaude.self_optimizer.learner.models import (
            FeedbackCategory,
            LearnedRule,
            Pattern,
        )

        turn_num = len(messages) // 2
        bm = behavior

        if bm.hallucination_risk > 0.3:
            kb = KnowledgeBase()
            rule = LearnedRule(
                id=f"hallucination_turn_{turn_num}_{session_id[:8]}",
                name="幻觉风险检测",
                description=f"第{turn_num}轮幻觉风险={bm.hallucination_risk:.0%}, query={prompt[:60]}",
                category=FeedbackCategory.SECURITY,
                pattern=Pattern(
                    context_keywords=("hallucination", prompt[:30]),
                    severity_distribution={"risk": bm.hallucination_risk},
                ),
                tools=("prior_verifier", "behavior"),
                frequency=1,
                confidence=0.7,
                quality_score=max(0.1, 1.0 - bm.hallucination_risk),
                status="active",
            )
            kb.add_rule(rule)
            kb.close()

        if bm.tool_error_count > 0:
            kb = KnowledgeBase()
            rule = LearnedRule(
                id=f"tool_error_turn_{turn_num}_{session_id[:8]}",
                name="工具错误记录",
                description=f"第{turn_num}轮工具错误={bm.tool_error_count}, query={prompt[:60]}",
                category=FeedbackCategory.BUG_RISK,
                pattern=Pattern(
                    context_keywords=("tool_error", prompt[:30]),
                    severity_distribution={"errors": bm.tool_error_count},
                ),
                tools=("behavior",),
                frequency=1,
                confidence=0.8,
                quality_score=0.5,
                status="active",
            )
            kb.add_rule(rule)
            kb.close()

        if bm.corrections_received > 0:
            # F3-2: corrections 流落库——「记错」之后必须留「改对」的账，
            # 供 F0 指标（规则有效率）与未来证据挂钩消费；历史 corrections 恒 0
            # 的缺口在此闭合（log_correction 此前全仓零调用）。
            try:

                from lingclaude.core.data_flywheel import CorrectionEntry, DataFlywheel
                from lingclaude.self_optimizer.experiments import ExperimentLedger

                # F2 归因链：纠错发生时若存在 pending 实验单，即处于该参数
                # 应用的观察窗内 → 携带 experiment_id 作为证据上下文
                # （corrections.experiment_id 列，F5 证据挂钩的挂点）。
                exp_id = ExperimentLedger().current_pending_id()
                fw = DataFlywheel()
                # G1 落库格式修正（2026-09-23）：correction 必须存真实纠正
                # 内容（用户原话），original_error 存被纠正的 assistant 输出
                # ——旧格式两者都是元数据壳（计数/prompt 截断），对避错零价值。
                # messages 为纯字符串交替 [p1,a1,...,pN,aN]，learn 在双 append
                # 之后调用 → [-2]=本轮 prompt，[-3]=被纠正的上轮输出。
                corrected_output = (
                    messages[-3] if len(messages) >= 3 else ""
                ) or (response or "")
                prev_output_preview = str(corrected_output)[:300]
                user_correction = str(prompt)[:500]
                fw.log_correction(
                    CorrectionEntry(
                        original_error=(
                            f"turn_{turn_num} output: {prev_output_preview}"
                        ),
                        correction=(
                            f"{user_correction} "
                            f"[user correction x{bm.corrections_received}, "
                            f"session {session_id[:8]}]"
                        ),
                        source="turn_learner",
                        confidence=0.9,
                        applied_at=_dt.now().isoformat(),
                        experiment_id=exp_id,
                    )
                )
                fw.close()
            except Exception as e:
                logger.warning("log_correction failed: %s", e)
            kb = KnowledgeBase()
            rule = LearnedRule(
                id=f"correction_pattern_{session_id[:8]}",
                name="用户纠正模式",
                description=f"用户纠正累计={bm.corrections_received}, 最近 query={prompt[:60]}",
                category=FeedbackCategory.BEST_PRACTICE,
                pattern=Pattern(
                    context_keywords=("correction", prompt[:30]),
                    severity_distribution={"count": bm.corrections_received},
                ),
                tools=("behavior",),
                frequency=max(1, bm.corrections_received),
                confidence=0.9,
                quality_score=0.6,
                status="active",
            )
            kb.add_rule(rule)
            kb.close()

        # F3-2b: per-turn 里程碑占位规则已移除——纯轮次流水账（#N）不构成知识，
        # 曾累计 ~5000 行噪声。轮次级指标由 DATALOG/T_SNAP 遥测快照承载（P0#1）。

    except Exception as e:
        logger.warning("知识库学习失败: %s", e)
