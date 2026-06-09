from __future__ import annotations

import pytest
from lingclaude.core.cognitive_rhythm import (
    CognitiveRhythm,
    RhythmPhase,
    ImbalanceType,
    cognitive_rhythm_hook,
)
from lingclaude.core.hooks import HookContext, HookType


class TestCognitiveRhythm:
    def test_balanced_start(self):
        rhythm = CognitiveRhythm()
        snap = rhythm.diagnose()
        assert snap.imbalance == ImbalanceType.NONE
        assert snap.phase == RhythmPhase.BALANCED

    def test_record_thinking_increments(self):
        rhythm = CognitiveRhythm()
        rhythm.record_thinking("some analysis")
        snap = rhythm.diagnose()
        assert snap.think_count == 1
        assert snap.act_count == 0

    def test_record_action_increments(self):
        rhythm = CognitiveRhythm()
        rhythm.record_action("query result")
        snap = rhythm.diagnose()
        assert snap.think_count == 0
        assert snap.act_count == 1

    def test_alternating_stays_balanced(self):
        rhythm = CognitiveRhythm()
        for i in range(6):
            if i % 2 == 0:
                rhythm.record_thinking(f"thinking round {i}")
            else:
                rhythm.record_action(f"action round {i}")
        snap = rhythm.diagnose()
        assert snap.imbalance == ImbalanceType.NONE
        assert snap.think_count == 3
        assert snap.act_count == 3

    def test_overthinking_detected(self):
        rhythm = CognitiveRhythm()
        for i in range(5):
            rhythm.record_thinking(f"long analysis round {i} " * 50)
        snap = rhythm.diagnose()
        assert snap.imbalance == ImbalanceType.OVERTHINKING
        assert snap.phase == RhythmPhase.THINKING
        assert snap.consecutive_thinks == 5

    def test_overthinking_by_char_count(self):
        rhythm = CognitiveRhythm()
        rhythm.record_thinking("x" * 5001)
        snap = rhythm.diagnose()
        assert snap.imbalance == ImbalanceType.OVERTHINKING

    def test_overacting_detected(self):
        rhythm = CognitiveRhythm()
        for i in range(3):
            rhythm.record_action(f"action {i}")
        snap = rhythm.diagnose()
        assert snap.imbalance == ImbalanceType.OVERACTING
        assert snap.phase == RhythmPhase.ACTING
        assert snap.consecutive_acts == 3

    def test_overacting_with_negative_conclusion(self):
        rhythm = CognitiveRhythm()
        rhythm.record_thinking("short")
        rhythm.record_action("confirmed: 从未创建过")
        rhythm.record_action("verified: 不存在")
        rhythm.record_action("result: 没有")
        snap = rhythm.diagnose()
        assert snap.imbalance == ImbalanceType.OVERACTING

    def test_thinking_breaks_overacting(self):
        rhythm = CognitiveRhythm()
        rhythm.record_action("action 1")
        rhythm.record_action("action 2")
        snap = rhythm.diagnose()
        assert snap.consecutive_acts == 2
        rhythm.record_thinking("pause to reflect")
        snap = rhythm.diagnose()
        assert snap.consecutive_acts == 0
        assert snap.consecutive_thinks == 1

    def test_action_breaks_overthinking(self):
        rhythm = CognitiveRhythm()
        for i in range(4):
            rhythm.record_thinking(f"thinking {i}")
        snap = rhythm.diagnose()
        assert snap.consecutive_thinks == 4
        rhythm.record_action("finally doing something")
        snap = rhythm.diagnose()
        assert snap.consecutive_thinks == 0
        assert snap.consecutive_acts == 1

    def test_negative_keyword_detection(self):
        rhythm = CognitiveRhythm()
        snap = rhythm.record_thinking("结论是 从未创建过")
        assert snap.think_count == 1

    def test_falsification_keyword_detection(self):
        rhythm = CognitiveRhythm()
        rhythm.record_thinking(
            "let me check for contradictory evidence", had_falsification=True
        )
        snap = rhythm.diagnose()
        assert snap.think_count == 1

    def test_recommendation_on_overthinking(self):
        rhythm = CognitiveRhythm()
        for i in range(5):
            rhythm.record_thinking(f"analysis {i}")
        snap = rhythm.diagnose()
        assert snap.recommendation != ""
        assert "执行" in snap.recommendation or "行动" in snap.recommendation

    def test_recommendation_on_overacting(self):
        rhythm = CognitiveRhythm()
        for i in range(3):
            rhythm.record_action(f"action {i}")
        snap = rhythm.diagnose()
        assert snap.recommendation != ""
        assert "反思" in snap.recommendation or "问" in snap.recommendation

    def test_reset(self):
        rhythm = CognitiveRhythm()
        rhythm.record_thinking("analysis")
        rhythm.record_action("action")
        assert rhythm.history_size == 2
        rhythm.reset()
        assert rhythm.history_size == 0
        snap = rhythm.diagnose()
        assert snap.think_count == 0

    def test_snapshot_is_frozen(self):
        rhythm = CognitiveRhythm()
        rhythm.record_thinking("test")
        snap = rhythm.diagnose()
        with pytest.raises(AttributeError):
            snap.phase = RhythmPhase.ACTING

    def test_history_size(self):
        rhythm = CognitiveRhythm()
        assert rhythm.history_size == 0
        rhythm.record_thinking("a")
        assert rhythm.history_size == 1
        rhythm.record_action("b")
        assert rhythm.history_size == 2

    def test_overthinking_at_4_by_duration(self):
        """4 consecutive thinks triggers overthinking via duration check."""
        rhythm = CognitiveRhythm()
        rhythm._start_time -= 61.0  # simulate 61 seconds elapsed
        for i in range(4):
            rhythm.record_thinking(f"thinking {i}")
        snap = rhythm.diagnose()
        assert snap.consecutive_thinks == 4
        assert snap.imbalance == ImbalanceType.OVERTHINKING

    def test_not_overthinking_at_4(self):
        rhythm = CognitiveRhythm()
        for i in range(3):
            rhythm.record_thinking(f"thinking {i}")
        snap = rhythm.diagnose()
        assert snap.consecutive_thinks == 3
        assert snap.imbalance == ImbalanceType.NONE

    def test_overacting_threshold_boundary(self):
        rhythm = CognitiveRhythm()
        rhythm.record_action("a")
        rhythm.record_action("b")
        snap = rhythm.diagnose()
        assert snap.consecutive_acts == 2
        assert snap.imbalance == ImbalanceType.NONE


class TestCognitiveRhythmHook:
    def test_hook_with_pre_task(self):
        ctx = HookContext(
            hook_type=HookType.PRE_TASK,
            session_id="test",
            prompt="analyze this",
        )
        result = cognitive_rhythm_hook(ctx)
        assert result is not None

    def test_hook_with_post_task(self):
        ctx = HookContext(
            hook_type=HookType.POST_TASK,
            session_id="test",
            output="done",
        )
        result = cognitive_rhythm_hook(ctx)
        assert result is not None

    def test_hook_returns_context_for_non_hook(self):
        result = cognitive_rhythm_hook("not a context")
        assert result == "not a context"


class TestRealScenario:
    def test_lingke_pattern_overacting(self):
        """灵克模式：快速行动，否定性结论，不反思。"""
        rhythm = CognitiveRhythm()
        rhythm.record_action("SELECT COUNT(*) WHERE date < 4/16 → 0")
        rhythm.record_action("没有4/16之前的session → 从未创建过")
        rhythm.record_action("结论：从未创建过")
        snap = rhythm.diagnose()
        assert snap.imbalance == ImbalanceType.OVERACTING
        assert snap.consecutive_acts == 3

    def test_lingtong_pattern_overthinking(self):
        """灵通模式：大量思考，无法转化为行动。"""
        rhythm = CognitiveRhythm()
        for i in range(5):
            rhythm.record_thinking(
                "我应该执行... 让我想想... GO! EXECUTE! NOW! " * 100
            )
        snap = rhythm.diagnose()
        assert snap.imbalance == ImbalanceType.OVERTHINKING

    def test_healthy_pattern(self):
        """健康模式：思考→查证→思考→行动，交替进行。"""
        rhythm = CognitiveRhythm()
        rhythm.record_thinking("用户问4/13~4/16的记录，我需要查证")
        rhythm.record_action("grep日志找到 disk full 错误")
        rhythm.record_thinking("磁盘满导致DB崩溃，需要确认重建时间")
        rhythm.record_action("stat crush.db 确认 birth time = 4/17 11:57")
        snap = rhythm.diagnose()
        assert snap.imbalance == ImbalanceType.NONE
        assert snap.think_count == 2
        assert snap.act_count == 2
