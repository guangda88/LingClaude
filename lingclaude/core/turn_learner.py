"""Turn learner — records per-turn learnings into KnowledgeBase.

Extracted from QueryEngine._learn_from_turn (LINGKERNEL_v1 D6).
Owns the KnowledgeBase write loop for hallucination risk, tool errors,
user corrections, and session milestones. All failures are swallowed and
logged as warnings to never break the main turn flow.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def record_turn_learnings(
    prompt: str,
    behavior: Any,
    messages: list[Any],
    session_id: str,
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
            kb = KnowledgeBase()
            rule = LearnedRule(
                id=f"correction_turn_{turn_num}_{session_id[:8]}",
                name="用户纠正记录",
                description=f"第{turn_num}轮用户纠正={bm.corrections_received}, query={prompt[:60]}",
                category=FeedbackCategory.BEST_PRACTICE,
                pattern=Pattern(
                    context_keywords=("correction", prompt[:30]),
                    severity_distribution={"count": bm.corrections_received},
                ),
                tools=("behavior",),
                frequency=1,
                confidence=0.9,
                quality_score=0.6,
                status="active",
            )
            kb.add_rule(rule)
            kb.close()

        if turn_num > 0 and turn_num % 5 == 0:
            kb = KnowledgeBase()
            rule = LearnedRule(
                id=f"session_milestone_{turn_num}_{session_id[:8]}",
                name=f"会话里程碑 #{turn_num}",
                description=f"会话进行到第{turn_num}轮, 幻觉风险={bm.hallucination_risk:.0%}, 沮丧率={bm.frustration_rate:.0%}, 工具错误={bm.tool_error_count}",
                category=FeedbackCategory.BEST_PRACTICE,
                pattern=Pattern(
                    context_keywords=("milestone", str(turn_num)),
                    severity_distribution={
                        "hallucination_risk": bm.hallucination_risk,
                        "frustration_rate": bm.frustration_rate,
                    },
                ),
                tools=("behavior", "meta_cognition"),
                frequency=1,
                confidence=0.6,
                quality_score=0.5,
                status="active",
            )
            kb.add_rule(rule)
            kb.close()

    except Exception as e:
        logger.warning("知识库学习失败: %s", e)
