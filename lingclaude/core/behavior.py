"""Behavior awareness: emotion detection, intent analysis, quality tracking.

Tracks how LingClaude behaves during conversations and detects issues like
hallucination (answering code questions without reading files) so that
self-optimization can be triggered automatically.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Emotion(str, Enum):
    SATISFIED = "satisfied"
    FRUSTRATED = "frustrated"
    CONFUSED = "confused"
    URGENT = "urgent"
    NEUTRAL = "neutral"


class Intent(str, Enum):
    CODE_QUESTION = "code_question"
    GENERAL_CHAT = "general_chat"
    BUG_REPORT = "bug_report"
    OPTIMIZATION_REQUEST = "optimization_request"
    CORRECTION = "correction"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BehaviorMetrics:
    total_turns: int = 0
    turns_with_tools: int = 0
    turns_without_tools_but_needed: int = 0
    tool_call_count: int = 0
    tool_error_count: int = 0
    emotions_detected: tuple[Emotion, ...] = ()
    corrections_received: int = 0
    frustration_count: int = 0

    @property
    def tool_use_rate(self) -> float:
        if self.total_turns == 0:
            return 0.0
        return self.turns_with_tools / self.total_turns

    @property
    def hallucination_risk(self) -> float:
        if self.total_turns == 0:
            return 0.0
        missed = self.turns_without_tools_but_needed
        return min(1.0, missed / max(self.total_turns, 1))

    @property
    def frustration_rate(self) -> float:
        if self.total_turns == 0:
            return 0.0
        return self.frustration_count / self.total_turns

    @property
    def tool_error_rate(self) -> float:
        if self.tool_call_count == 0:
            return 0.0
        return self.tool_error_count / self.tool_call_count

    def record_turn(
        self,
        *,
        emotion: Emotion | None = None,
        is_correction: bool = False,
        is_frustrated: bool = False,
        used_tools: bool = False,
        needed_tools: bool = False,
        tool_calls: int = 0,
        tool_errors: int = 0,
    ) -> BehaviorMetrics:
        return BehaviorMetrics(
            total_turns=self.total_turns + 1,
            turns_with_tools=self.turns_with_tools + (1 if used_tools else 0),
            turns_without_tools_but_needed=self.turns_without_tools_but_needed + (1 if needed_tools else 0),
            tool_call_count=self.tool_call_count + tool_calls,
            tool_error_count=self.tool_error_count + tool_errors,
            emotions_detected=self.emotions_detected + ((emotion,) if emotion else ()),
            corrections_received=self.corrections_received + (1 if is_correction else 0),
            frustration_count=self.frustration_count + (1 if is_frustrated else 0),
        )

    def record_tool_calls(self, count: int = 1, errors: int = 0) -> BehaviorMetrics:
        return BehaviorMetrics(
            total_turns=self.total_turns,
            turns_with_tools=self.turns_with_tools,
            turns_without_tools_but_needed=self.turns_without_tools_but_needed,
            tool_call_count=self.tool_call_count + count,
            tool_error_count=self.tool_error_count + errors,
            emotions_detected=self.emotions_detected,
            corrections_received=self.corrections_received,
            frustration_count=self.frustration_count,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "total_turns": self.total_turns,
            "tool_use_rate": round(self.tool_use_rate, 3),
            "hallucination_risk": round(self.hallucination_risk, 3),
            "frustration_rate": round(self.frustration_rate, 3),
            "tool_error_rate": round(self.tool_error_rate, 3),
            "corrections_received": self.corrections_received,
        }


_FRUSTRATION_PATTERNS = [
    r"胡说", r"不对", r"错了", r"乱说", r"瞎说", r"你说的不对",
    r"没读", r"没有读", r"幻觉", r"编造", r"乱编", r"不准确",
    r"废话", r"没有用", r"没帮助", r"垃圾", r"什么鬼", r"搞什么",
    r"白痴", r"笨", r"太差了", r"不行", r"失望",
    r"你根本没有", r"你并没有", r"不要猜", r"别猜",
]

_SATISFIED_PATTERNS = [
    r"谢谢", r"好的", r"不错", r"对了", r"正确", r"很好",
    r"可以", r"完美", r"厉害", r"棒", r"解决了", r"明白了",
]

_CONFUSED_PATTERNS = [
    r"什么意思", r"不懂", r"不明白", r"怎么回事", r"为什么",
    r"\?\?+", r"？？+", r"能再说一遍吗", r"解释一下",
]

_URGENT_PATTERNS = [
    r"快点", r"急", r"赶紧", r"马上", r"立刻", r"紧急",
    r"来不及了", r"帮帮我", r"快",
]

_CORRECTION_PATTERNS = [
    r"胡说", r"不对", r"错了", r"你说的不对", r"乱说",
    r"应该是", r"其实是", r"实际上是", r"不是这样的",
    r"正确的是", r"我来纠正", r"纠正一下",
    r"没读", r"没有读", r"幻觉", r"没有去", r"你没去",
    r"不要猜", r"别猜",
]

_CODE_QUESTION_PATTERNS = [
    r"代码", r"文件", r"函数", r"类", r"模块", r"方法",
    r"\.py", r"\.js", r"\.ts", r"\.go", r"\.rs", r"\.java",
    r"import", r"class ", r"def ", r"func ",
    r"读一下", r"看一下", r"分析", r"理解", r"解释.*代码",
    r"这个文件", r"那个文件", r"源码", r"源代码",
    r"grep", r"glob", r"搜索", r"查找", r"找.*文件",
    r"怎么实现", r"怎么工作", r"实现.*功能",
    r"理解.*代码", r"理解.*模块", r"理解.*自己的",
    r"看看", r"读读", r"检查.*代码",
]

_OPTIMIZATION_PATTERNS = [
    r"优化", r"改进", r"提升", r"改善", r"重构", r"自优化",
    r"学习.*成长", r"自我.*进化", r"自我.*优化",
]


def detect_emotion(text: str) -> Emotion:
    for pattern in _FRUSTRATION_PATTERNS:
        if re.search(pattern, text):
            return Emotion.FRUSTRATED
    for pattern in _URGENT_PATTERNS:
        if re.search(pattern, text):
            return Emotion.URGENT
    for pattern in _CONFUSED_PATTERNS:
        if re.search(pattern, text):
            return Emotion.CONFUSED
    for pattern in _SATISFIED_PATTERNS:
        if re.search(pattern, text):
            return Emotion.SATISFIED
    return Emotion.NEUTRAL


def detect_intent(text: str) -> Intent:
    if any(re.search(p, text) for p in _CORRECTION_PATTERNS):
        return Intent.CORRECTION
    if any(re.search(p, text) for p in _OPTIMIZATION_PATTERNS):
        return Intent.OPTIMIZATION_REQUEST
    for p in [r"bug", r"报错", r"错误", r"失败", r"崩溃", r"异常"]:
        if re.search(p, text):
            return Intent.BUG_REPORT
    if any(re.search(p, text) for p in _CODE_QUESTION_PATTERNS):
        return Intent.CODE_QUESTION
    return Intent.GENERAL_CHAT


def is_tool_intent(intent: Intent) -> bool:
    return intent in (Intent.CODE_QUESTION, Intent.BUG_REPORT)
