"""Tests for behavior awareness: emotion detection, intent analysis, behavior metrics."""
from __future__ import annotations

import pytest

from lingclaude.core.behavior import (
    BehaviorMetrics,
    Emotion,
    Intent,
    detect_emotion,
    detect_intent,
    is_tool_intent,
)


class TestEmotionDetection:
    def test_frustrated(self) -> None:
        assert detect_emotion("你没有读代码就开始胡说了") == Emotion.FRUSTRATED
        assert detect_emotion("你说的不对") == Emotion.FRUSTRATED
        assert detect_emotion("错了") == Emotion.FRUSTRATED
        assert detect_emotion("乱说") == Emotion.FRUSTRATED
        assert detect_emotion("你根本没有去读文件") == Emotion.FRUSTRATED
        assert detect_emotion("这是幻觉") == Emotion.FRUSTRATED
        assert detect_emotion("不要猜") == Emotion.FRUSTRATED

        assert detect_emotion("你瞎说八道") == Emotion.FRUSTRATED
        assert detect_emotion("没帮助") == Emotion.FRUSTRATED
        assert detect_emotion("太差了") == Emotion.FRUSTRATED

        assert detect_emotion("没有去认真的读代码就开始胡说了") == Emotion.FRUSTRATED

        assert detect_emotion("灵克，你要自己学习自己成长哟") == Emotion.NEUTRAL

        assert detect_emotion("你能理解这个函数吗") == Emotion.NEUTRAL

    def test_satisfied(self) -> None:
        assert detect_emotion("谢谢") == Emotion.SATISFIED
        assert detect_emotion("好的，很好") == Emotion.SATISFIED
        assert detect_emotion("不错，解决了") == Emotion.SATISFIED
        assert detect_emotion("很棒") == Emotion.SATISFIED

    def test_confused(self) -> None:
        assert detect_emotion("什么意思") == Emotion.CONFUSED
        assert detect_emotion("不懂") == Emotion.CONFUSED
        assert detect_emotion("怎么回事？？") == Emotion.CONFUSED

    def test_urgent(self) -> None:
        assert detect_emotion("快点帮我") == Emotion.URGENT
        assert detect_emotion("很急") == Emotion.URGENT

    def test_neutral(self) -> None:
        assert detect_emotion("你好") == Emotion.NEUTRAL
        assert detect_emotion("请读取这个文件") == Emotion.NEUTRAL
        assert detect_emotion("帮我分析代码") == Emotion.NEUTRAL


class TestIntentAnalysis:
    def test_code_question(self) -> None:
        assert detect_intent("读一下你的代码") == Intent.CODE_QUESTION
        assert detect_intent("理解这个模块的实现") == Intent.CODE_QUESTION
        assert detect_intent("分析 main.py") == Intent.CODE_QUESTION
        assert detect_intent("grep 一下") == Intent.CODE_QUESTION
        assert detect_intent("这个文件怎么工作") == Intent.CODE_QUESTION
        assert detect_intent("看看 query_engine.py 的代码") == Intent.CODE_QUESTION

    def test_correction(self) -> None:
        assert detect_intent("你没有读代码就开始胡说了") == Intent.CORRECTION
        assert detect_intent("应该是这样的") == Intent.CORRECTION
        assert detect_intent("不对，其实是对的") == Intent.CORRECTION
        assert detect_intent("你说的不对") == Intent.CORRECTION
        assert detect_intent("你没去读文件") == Intent.CORRECTION

    def test_optimization_request(self) -> None:
        assert detect_intent("请优化这个代码") == Intent.OPTIMIZATION_REQUEST
        assert detect_intent("帮我改进") == Intent.OPTIMIZATION_REQUEST
        assert detect_intent("你需要自己学习自己成长") == Intent.OPTIMIZATION_REQUEST
        assert detect_intent("自我进化") == Intent.OPTIMIZATION_REQUEST

    def test_bug_report(self) -> None:
        assert detect_intent("这个bug") == Intent.BUG_REPORT
        assert detect_intent("运行失败") == Intent.BUG_REPORT
        assert detect_intent("出现异常") == Intent.BUG_REPORT

    def test_general_chat(self) -> None:
        assert detect_intent("你好") == Intent.GENERAL_CHAT
        assert detect_intent("今天天气怎么样") == Intent.GENERAL_CHAT


class TestIsToolIntent:
    def test_tool_intents(self) -> None:
        assert is_tool_intent(Intent.CODE_QUESTION) is True
        assert is_tool_intent(Intent.BUG_REPORT) is True
        assert is_tool_intent(Intent.GENERAL_CHAT) is False
        assert is_tool_intent(Intent.CORRECTION) is False
        assert is_tool_intent(Intent.OPTIMIZATION_REQUEST) is False


class TestBehaviorMetrics:
    def test_empty_metrics(self) -> None:
        m = BehaviorMetrics()
        assert m.tool_use_rate == 0.0
        assert m.hallucination_risk == 0.0
        assert m.frustration_rate == 0.0
        assert m.tool_error_rate == 0.0

        d = m.to_dict()
        assert d["total_turns"] == 0
        assert d["hallucination_risk"] == 0.0

        assert d["corrections_received"] == 0

    def test_tool_use_rate(self) -> None:
        m = BehaviorMetrics(total_turns=10, turns_with_tools=7)
        assert m.tool_use_rate == pytest.approx(0.7)

        assert m.hallucination_risk == 0.0
        assert m.tool_error_rate == 0.0

    def test_hallucination_risk(self) -> None:
        m = BehaviorMetrics(total_turns=10, turns_without_tools_but_needed=3)
        assert m.hallucination_risk == pytest.approx(0.3)

        m2 = BehaviorMetrics(total_turns=5, turns_without_tools_but_needed=5)
        assert m2.hallucination_risk == pytest.approx(1.0)

    def test_frustration_rate(self) -> None:
        m = BehaviorMetrics(total_turns=10, frustration_count=2)
        assert m.frustration_rate == pytest.approx(0.2)
        assert m.tool_use_rate == 0.0

    def test_tool_error_rate(self) -> None:
        m = BehaviorMetrics(tool_call_count=10, tool_error_count=3)
        assert m.tool_error_rate == pytest.approx(0.3)

        m2 = BehaviorMetrics(tool_call_count=0)
        assert m2.tool_error_rate == 0.0

    def test_to_dict(self) -> None:
        m = BehaviorMetrics(
            total_turns=5,
            turns_with_tools=3,
            turns_without_tools_but_needed=1,
            tool_call_count=8,
            tool_error_count=1,
            corrections_received=2,
            frustration_count=1,
        )
        d = m.to_dict()
        assert d["total_turns"] == 5
        assert d["tool_use_rate"] == pytest.approx(0.6)
        assert d["hallucination_risk"] == pytest.approx(0.2)
        assert d["frustration_rate"] == pytest.approx(0.2)
        assert d["tool_error_rate"] == pytest.approx(0.125)
        assert d["corrections_received"] == 2


