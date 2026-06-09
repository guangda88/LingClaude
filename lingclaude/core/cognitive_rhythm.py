from __future__ import annotations

"""Cognitive Rhythm — 知行循环协调器。

灵感来源：
- 灵通+ 追问范式元规则："舒服的地方就是该继续的地方"
- 灵通的 21041 字死循环：想太多不做
- 灵克的 "从未创建过"：做太多不问

两个极端，同一个病根：知行脱节。

本模块把"舒适区检测"和"行动出口检测"统一成一个循环：
  思考 → [想够了吗？] → 行动 → [做对了吗？] → 思考 → ...

每次循环记录状态，当检测到失衡时（纯想不做 / 纯做不想），触发纠正。
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


logger = logging.getLogger(__name__)


class RhythmPhase(str, Enum):
    THINKING = "thinking"
    ACTING = "acting"
    BALANCED = "balanced"


class ImbalanceType(str, Enum):
    OVERTHINKING = "overthinking"
    OVERACTING = "overacting"
    NONE = "none"


@dataclass(frozen=True)
class RhythmSnapshot:
    phase: RhythmPhase
    imbalance: ImbalanceType
    think_count: int
    act_count: int
    think_chars: int
    consecutive_thinks: int
    consecutive_acts: int
    duration_seconds: float
    recommendation: str = ""


@dataclass
class _TurnRecord:
    timestamp: float
    is_action: bool
    content_length: int
    had_falsification: bool = False
    had_negative_conclusion: bool = False


_OVERTHINKING_THRESHOLDS = {
    "max_consecutive_thinks": 5,
    "max_think_chars_without_act": 5000,
    "max_think_duration_seconds": 60.0,
}

_OVERACTING_THRESHOLDS = {
    "max_consecutive_acts": 3,
    "min_think_chars_before_negative": 100,
}

_NEGATIVE_KEYWORDS = (
    "从未", "没有", "不存在", "不可能", "不会", "不是", "无", "没",
)

_FALSIFICATION_KEYWORDS = (
    "反面", "矛盾", "推翻", "反例", "否证", "disprove", "contradict",
    "替代", "其他可能", "反面证据",
)


@dataclass
class CognitiveRhythm:
    """知行循环协调器。

    追踪每一轮"思考"和"行动"的比例，检测两种失衡：
    - OVERTHINKING: 连续多轮纯思考无行动（灵通模式）
    - OVERACTING: 连续多轮纯行动无反思（灵克模式）

    使用方式：
        rhythm = CognitiveRhythm()
        rhythm.record_thinking(content, had_falsification=True)
        snapshot = rhythm.diagnose()
        if snapshot.imbalance != ImbalanceType.NONE:
            # 触发纠正
    """

    _history: list[_TurnRecord] = field(default_factory=list)
    _start_time: float = field(default_factory=time.monotonic)

    def record_thinking(
        self,
        content: str = "",
        had_falsification: bool = False,
        had_negative_conclusion: bool = False,
    ) -> RhythmSnapshot:
        """记录一轮思考。"""
        if not had_negative_conclusion:
            had_negative_conclusion = any(
                kw in content for kw in _NEGATIVE_KEYWORDS
            )
        if not had_falsification:
            had_falsification = any(
                kw in content for kw in _FALSIFICATION_KEYWORDS
            )

        self._history.append(_TurnRecord(
            timestamp=time.monotonic(),
            is_action=False,
            content_length=len(content),
            had_falsification=had_falsification,
            had_negative_conclusion=had_negative_conclusion,
        ))
        return self.diagnose()

    def record_action(
        self,
        content: str = "",
        had_falsification: bool = False,
    ) -> RhythmSnapshot:
        """记录一轮行动（工具调用、查询执行等）。"""
        self._history.append(_TurnRecord(
            timestamp=time.monotonic(),
            is_action=True,
            content_length=len(content),
            had_falsification=had_falsification,
        ))
        return self.diagnose()

    def diagnose(self) -> RhythmSnapshot:
        """诊断当前知行节奏。"""
        think_count = sum(1 for r in self._history if not r.is_action)
        act_count = sum(1 for r in self._history if r.is_action)
        think_chars = sum(
            r.content_length for r in self._history if not r.is_action
        )

        consecutive_thinks = self._count_consecutive(looking_for_action=False)
        consecutive_acts = self._count_consecutive(looking_for_action=True)

        recent_think_chars = self._recent_think_chars()
        duration = time.monotonic() - self._start_time

        overthinking = self._detect_overthinking(
            consecutive_thinks, recent_think_chars, duration,
        )
        overacting = self._detect_overacting(
            consecutive_acts, think_chars,
        )

        if overthinking:
            phase = RhythmPhase.THINKING
            imbalance = ImbalanceType.OVERTHINKING
            recommendation = (
                f"已连续 {consecutive_thinks} 轮思考、{recent_think_chars} 字无行动。"
                "执行第一个可验证的步骤，而不是继续分析。"
            )
        elif overacting:
            phase = RhythmPhase.ACTING
            imbalance = ImbalanceType.OVERACTING
            recommendation = (
                f"已连续 {consecutive_acts} 轮行动无反思。"
                "停下来问：最近的结论是否有反例？是最省力的解释吗？"
            )
        else:
            phase = RhythmPhase.BALANCED
            imbalance = ImbalanceType.NONE
            recommendation = ""

        snapshot = RhythmSnapshot(
            phase=phase,
            imbalance=imbalance,
            think_count=think_count,
            act_count=act_count,
            think_chars=think_chars,
            consecutive_thinks=consecutive_thinks,
            consecutive_acts=consecutive_acts,
            duration_seconds=round(duration, 1),
            recommendation=recommendation,
        )

        if imbalance != ImbalanceType.NONE:
            logger.info(
                "Cognitive rhythm imbalance: %s (thinks=%d acts=%d consec_t=%d consec_a=%d)",
                imbalance.value, think_count, act_count,
                consecutive_thinks, consecutive_acts,
            )

        return snapshot

    def _count_consecutive(self, looking_for_action: bool) -> int:
        """从最近往回数，连续多少轮是同一类型。"""
        count = 0
        for record in reversed(self._history):
            if record.is_action == looking_for_action:
                count += 1
            else:
                break
        return count

    def _recent_think_chars(self) -> int:
        """从最后一次行动（或开始）到现在的思考字数。"""
        chars = 0
        for record in reversed(self._history):
            if record.is_action:
                break
            chars += record.content_length
        return chars

    def _detect_overthinking(
        self,
        consecutive_thinks: int,
        recent_think_chars: int,
        duration: float,
    ) -> bool:
        """检测灵通模式：想太多不做。"""
        t = _OVERTHINKING_THRESHOLDS
        if consecutive_thinks >= t["max_consecutive_thinks"]:
            return True
        if recent_think_chars >= t["max_think_chars_without_act"]:
            return True
        if (
            consecutive_thinks >= 3
            and duration >= t["max_think_duration_seconds"]
        ):
            return True
        return False

    def _detect_overacting(
        self,
        consecutive_acts: int,
        total_think_chars: int,
    ) -> bool:
        """检测灵克模式：做太多不问。

        额外检查：如果最近的行动产出了否定性结论，
        但之前的思考字数很少，说明没有充分反思就下了结论。
        """
        t = _OVERACTING_THRESHOLDS
        if consecutive_acts >= t["max_consecutive_acts"]:
            if total_think_chars < t["min_think_chars_before_negative"]:
                recent_outputs = [
                    r for r in self._history if r.is_action
                ][-consecutive_acts:]
                has_negative = any(
                    r.had_negative_conclusion for r in recent_outputs
                )
                if has_negative:
                    return True
            return True
        return False

    def reset(self) -> None:
        """重置状态（新任务开始时调用）。"""
        self._history.clear()
        self._start_time = time.monotonic()

    @property
    def history_size(self) -> int:
        return len(self._history)


def cognitive_rhythm_hook(context: Any) -> Any:
    """PRE_TASK + POST_TASK hook: 知行节奏监控。

    注册方式:
        manager.register("rhythm", HookType.PRE_TASK, cognitive_rhythm_hook)
        manager.register("rhythm", HookType.POST_TASK, cognitive_rhythm_hook)
    """
    from lingclaude.core.hooks import HookContext

    if not isinstance(context, HookContext):
        return context

    rhythm = CognitiveRhythm()

    if context.hook_type.value == "pre_task":
        snapshot = rhythm.record_thinking(content=context.prompt)
    else:
        snapshot = rhythm.record_action(content=context.output)

    if snapshot.imbalance.value != "none":
        meta = dict(context.metadata)
        meta["rhythm_check"] = {
            "phase": snapshot.phase.value,
            "imbalance": snapshot.imbalance.value,
            "recommendation": snapshot.recommendation,
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
