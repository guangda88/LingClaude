from __future__ import annotations

"""Comfort Zone Detector — 自触发追问机制。

灵感来源：灵通+ 追问范式元规则 "舒服的地方就是该继续的地方"。

每次产出结论时，强制追问：
1. "什么证据能推翻这个结论？"（可证伪性）
2. "这个结论是否是当前最省力的解释？"（省力偏差）
3. "有没有查过的证据被忽略了？"（选择性注意）

如果以上任一回答为"是"或"不确定"，结论标记为 premature，需要进一步验证。
"""

import re
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from lingclaude.core.types import Result

logger = logging.getLogger(__name__)


class ConclusionRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class ComfortCheckResult:
    conclusion: str
    risk: ConclusionRisk
    falsifiable: bool
    least_effort_bias: bool
    evidence_ignored: bool
    questions: tuple[str, ...] = ()
    recommendation: str = ""


@dataclass
class ComfortZoneDetector:
    """检测结论是否停留在舒适区。

    使用方式：在 POST_TASK hook 中调用 check()，
    如果返回 risk >= MEDIUM，强制追加验证步骤。
    """

    _conclusion_patterns: tuple[re.Pattern[str], ...] = field(default_factory=lambda: (
        re.compile(r"(从未|没有|不存在|不可能|已经|不是)", re.IGNORECASE),
        re.compile(r"(确认|确定|验证|证明).*(没问题|正确|安全|完整)", re.IGNORECASE),
    ))

    _hedging_patterns: tuple[re.Pattern[str], ...] = field(default_factory=lambda: (
        re.compile(r"(可能|也许|大概|似乎|应该是)", re.IGNORECASE),
    ))

    _investigation_keywords: tuple[str, ...] = (
        "查", "grep", "select", "搜索", "search", "查看", "检查", "verify",
    )

    _negative_keywords: tuple[str, ...] = (
        "从未", "没有", "不存在", "不可能", "不会", "不是", "无", "没",
    )

    def check(
        self,
        conclusion: str,
        evidence_examined: tuple[str, ...] = (),
        queries_executed: tuple[str, ...] = (),
    ) -> ComfortCheckResult:
        """检查结论是否可能停留在舒适区。"""
        has_negative = any(kw in conclusion for kw in self._negative_keywords)
        has_investigation = bool(queries_executed)
        has_falsification_attempt = any(
            any(kw in q for kw in ("反面", "矛盾", "推翻", "反例", "否证", "disprove", "contradict"))
            for q in queries_executed
        )

        questions: list[str] = []

        falsifiable = has_falsification_attempt
        if not falsifiable:
            questions.append("什么证据能推翻这个结论？")

        least_effort = has_negative and not has_investigation
        if has_negative:
            questions.append("这是否是最省力的解释？有没有查过替代可能？")

        evidence_ignored = False
        if evidence_examined and has_negative:
            examined_count = len(evidence_examined)
            if examined_count < 2:
                evidence_ignored = True
                questions.append(
                    f"只查了 {examined_count} 个来源，有没有被忽略的证据？"
                )

        if least_effort:
            risk = ConclusionRisk.HIGH
        elif not falsifiable or evidence_ignored:
            risk = ConclusionRisk.MEDIUM
        else:
            risk = ConclusionRisk.LOW

        recommendation = ""
        if risk == ConclusionRisk.HIGH:
            recommendation = "结论可能是最省力的解释。执行至少一个能推翻该结论的查询。"
        elif risk == ConclusionRisk.MEDIUM:
            recommendation = "结论缺少可证伪性检验。补充反例查询。"

        result = ComfortCheckResult(
            conclusion=conclusion[:200],
            risk=risk,
            falsifiable=falsifiable,
            least_effort_bias=least_effort,
            evidence_ignored=evidence_ignored,
            questions=tuple(questions),
            recommendation=recommendation,
        )

        if risk != ConclusionRisk.LOW:
            logger.info(
                "Comfort zone detected: risk=%s falsifiable=%s least_effort=%s ignored=%s",
                risk.value, falsifiable, least_effort, evidence_ignored,
            )

        return result

    def generate_follow_up(self, check_result: ComfortCheckResult) -> tuple[str, ...]:
        """根据检测结果生成追问命令。"""
        follow_ups: list[str] = []

        if check_result.least_effort_bias:
            follow_ups.append(
                "grep/search logs for contradictory evidence before concluding"
            )

        if not check_result.falsifiable:
            follow_ups.append(
                "identify what evidence would disprove the conclusion"
            )

        if check_result.evidence_ignored:
            follow_ups.append(
                "check at least 2 independent sources before negative conclusions"
            )

        return tuple(follow_ups)


def comfort_check_hook(context: Any) -> Any:
    """POST_TASK hook: 自动检测舒适区结论。

    注册方式:
        manager.register("comfort_check", HookType.POST_TASK, comfort_check_hook)
    """
    from lingclaude.core.hooks import HookContext

    if not isinstance(context, HookContext):
        return context

    if not context.output:
        return context

    detector = ComfortZoneDetector()
    result = detector.check(conclusion=context.output)

    if result.risk != ConclusionRisk.LOW:
        follow_ups = detector.generate_follow_up(result)
        meta = dict(context.metadata)
        meta["comfort_check"] = {
            "risk": result.risk.value,
            "questions": list(result.questions),
            "follow_ups": list(follow_ups),
            "recommendation": result.recommendation,
        }
        return HookContext(
            hook_type=context.hook_type,
            session_id=context.session_id,
            prompt=context.prompt,
            output=context.output,
            tool_name=context.tool_name,
            error_message=context.error_message,
            stop_reason=context.stop_reason,
            metadata=meta,
        )

    return context
