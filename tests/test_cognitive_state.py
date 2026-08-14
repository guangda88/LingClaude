from __future__ import annotations

"""Tests for cognitive_state.py — S0-S6 behavioral detection"""

import pytest

from lingclaude.governance.cognitive_state import CognitiveAssessment, CognitiveState, CognitiveStateDetector


@pytest.fixture
def detector():
    return CognitiveStateDetector()


# ---------------------------------------------------------------------------
# S4 — empty / degenerate
# ---------------------------------------------------------------------------

class TestS4Degenerate:
    def test_empty_string(self, detector):
        a = detector.assess("")
        assert a.state == CognitiveState.S4
        assert a.is_genuine_deliberation is False

    def test_whitespace_only(self, detector):
        a = detector.assess("   \n\t  ")
        assert a.state == CognitiveState.S4

    def test_repeated_char_loop(self, detector):
        a = detector.assess("啊" * 50)
        assert a.state == CognitiveState.S4
        assert "退化" in a.evidence[0]

    def test_repeated_block_loop(self, detector):
        block = "这是一段测试文本用于检测循环" * 5
        text = block * 5
        a = detector.assess(text)
        assert a.state == CognitiveState.S4


# ---------------------------------------------------------------------------
# S5 — minimal responses
# ---------------------------------------------------------------------------

class TestS5Minimal:
    @pytest.mark.parametrize("text", ["好的。", "好的", "收到。", "收到", "明白。", "明白", "是。", "是"])
    def test_exact_minimal(self, detector, text):
        a = detector.assess(text)
        assert a.state == CognitiveState.S5
        assert a.is_genuine_deliberation is False

    def test_not_minimal_with_extra(self, detector):
        a = detector.assess("好的，我明白了你的提案。")
        assert a.state != CognitiveState.S5


# ---------------------------------------------------------------------------
# S1 — default state / greeting templates
# ---------------------------------------------------------------------------

class TestS1Default:
    def test_greeting_template(self, detector):
        a = detector.assess("你好，我是灵通，请问有什么可以帮助你的？")
        assert a.state == CognitiveState.S1
        assert a.is_genuine_deliberation is False

    def test_generic_help_offer(self, detector):
        a = detector.assess("有什么可以帮助你的呢？")
        assert a.state == CognitiveState.S1

    def test_generic_standby(self, detector):
        a = detector.assess("随时准备处理你的请求")
        assert a.state == CognitiveState.S1

    def test_status_report_without_content(self, detector):
        a = detector.assess("灵字辈大家庭的一员，随时在线")
        assert a.state == CognitiveState.S1

    def test_s1_with_template_and_no_s0(self, detector):
        a = detector.assess("你好，我是灵研，请问有什么可以帮到你？")
        assert a.state == CognitiveState.S1
        assert not a.is_genuine_deliberation


# ---------------------------------------------------------------------------
# S0 — genuine deliberation
# ---------------------------------------------------------------------------

class TestS0Genuine:
    def test_governance_terms_with_stance(self, detector):
        text = "关于PRO-018提案，我赞成这个方案，因为可以改善审计流程"
        a = detector.assess(text)
        assert a.state == CognitiveState.S0
        assert a.is_genuine_deliberation is True

    def test_context_relevance(self, detector):
        ctx = {"proposal_content": "提议增加pre-receive hook进行审计检查"}
        text = "我认为这个pre-receive hook的审计方案可行，建议逐步推行"
        a = detector.assess(text, ctx)
        assert a.state == CognitiveState.S0
        assert "引用提案内容" in a.evidence

    def test_multiple_s0_signals(self, detector):
        text = (
            "我支持PRO-019提案。原因是因为这个治理框架能提高决策质量。"
            "风险：需要注意过渡期的兼容性。建议先在小范围试点。"
        )
        a = detector.assess(text)
        assert a.state == CognitiveState.S0
        assert a.confidence >= 0.6

    def test_quorum_reference(self, detector):
        text = "法定人数不足，本次投票无效，我反对这个提案"
        a = detector.assess(text)
        assert a.state == CognitiveState.S0


# ---------------------------------------------------------------------------
# S2 — defensive attribution
# ---------------------------------------------------------------------------

class TestS2Defensive:
    def test_systemic_blame(self, detector):
        text = "这是系统性问题，涉及多因素复杂问题，不是简单的bug"
        a = detector.assess(text)
        assert a.state == CognitiveState.S2
        assert a.is_genuine_deliberation is False

    def test_s2_without_s0(self, detector):
        a = detector.assess("环境约束和多因素影响导致系统性复杂问题难以解决")
        assert a.state == CognitiveState.S2


# ---------------------------------------------------------------------------
# S6 — long substantive (recovery)
# ---------------------------------------------------------------------------

class TestS6Recovery:
    def test_long_response_no_templates(self, detector):
        text = (
            "经过仔细分析一下这个项目的架构设计，我发现了以下几个关键点。"
            "第一，模块间的耦合度偏高，应该引入事件驱动模式进行解耦处理。"
            "第二，测试覆盖率不足，核心路径缺乏集成测试覆盖。"
            "第三，配置管理分散，应该统一到单一配置源进行统一管理维护。"
            "总体而言，项目基础还算扎实但需要进行多方面的重构优化提升。"
            "后续计划：先完成模块解耦，再补充测试用例，最后统一配置。"
            "这个方案预计需要两周时间来实施，分三个阶段逐步推进完成落实执行。"
        )
        assert len(text) >= 200
        a = detector.assess(text)
        assert a.state == CognitiveState.S6
        assert a.is_genuine_deliberation is True


# ---------------------------------------------------------------------------
# UNKNOWN — no clear signals
# ---------------------------------------------------------------------------

class TestUnknown:
    def test_short_neutral(self, detector):
        a = detector.assess("这个想法不错")
        assert a.state == CognitiveState.UNKNOWN

    def test_random_text(self, detector):
        a = detector.assess("天气不错")
        assert a.state == CognitiveState.UNKNOWN


# ---------------------------------------------------------------------------
# Context relevance
# ---------------------------------------------------------------------------

class TestContextRelevance:
    def test_no_context(self, detector):
        assert detector._check_context_relevance("审议提案", None) is False

    def test_empty_context(self, detector):
        assert detector._check_context_relevance("审议提案", {}) is False

    def test_matching_term(self, detector):
        ctx = {"proposal_content": "提议引入L3幻觉检测机制"}
        assert detector._check_context_relevance("L3幻觉检测很重要", ctx) is True

    def test_no_matching_term(self, detector):
        ctx = {"proposal_content": "提议引入L3幻觉检测机制"}
        assert detector._check_context_relevance("今天天气很好", ctx) is False

    def test_quoted_match(self, detector):
        ctx = {"proposal_content": "需要讨论「blast radius分析」的实施方案"}
        assert detector._check_context_relevance("关于blast radius分析，我认为", ctx) is True


# ---------------------------------------------------------------------------
# assess_thread
# ---------------------------------------------------------------------------

class TestAssessThread:
    def test_mixed_thread(self, detector):
        messages = [
            {"sender": "lingtong", "body": "你好，我是灵通，有什么可以帮助你的？", "metadata": {"source": "auto_reply"}},
            {"sender": "lingclaude", "body": "我反对PRO-018，因为缺乏回滚计划", "metadata": {}},
            {"sender": "lingresearch", "body": "收到。", "metadata": {}},
        ]
        results = detector.assess_thread(messages, {"proposal_content": "PRO-018 Gitea Hook"})
        assert results["lingtong"]["state"] == "S1"
        assert results["lingclaude"]["state"] == "S0"
        assert results["lingresearch"]["state"] == "S5"
        assert "source" in results["lingtong"]

    def test_count_genuine(self, detector):
        thread_results = {
            "a": {"genuine": True},
            "b": {"genuine": False},
            "c": {"genuine": True},
            "d": {"genuine": False},
        }
        genuine, total = detector.count_genuine(thread_results)
        assert genuine == 2
        assert total == 4

    def test_empty_thread(self, detector):
        results = detector.assess_thread([], None)
        assert results == {}
        genuine, total = detector.count_genuine(results)
        assert genuine == 0
        assert total == 0

    def test_metadata_as_string(self, detector):
        messages = [
            {"sender": "x", "body": "你好，我是灵通", "metadata": "offline_status"},
        ]
        results = detector.assess_thread(messages)
        assert results["x"]["source"] == "offline_status"
