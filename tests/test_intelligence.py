from __future__ import annotations

from datetime import datetime, timezone, timedelta

from lingclaude.core.prior_verifier import PriorVerifier, AssertionLevel, VerificationResult
from lingclaude.core.meta_cognition import (
    MetaCognition, Domain, ConfidenceLevel, ConfidenceCalibrator, BlindSpotDetector,
)
from lingclaude.core.layered_memory import (
    LayeredMemory, Experience, EmotionIntensity,
    CommonKnowledge, WorkingMemory, InMemoryExperienceStore, ebbinghaus_weight,
)
import pytest


# ── PriorVerifier ──────────────────────────────────────────

class TestPriorVerifier:
    def test_clean_text_passes(self):
        pv = PriorVerifier()
        result = pv.analyze("今天天气不错", used_tools=False)
        assert result.verified is True
        assert result.corrected_text == ""
        assert len(result.assertions) == 0

    def test_code_claim_without_tools_flagged(self):
        pv = PriorVerifier()
        result = pv.analyze("在文件 foo.py 中函数 bar 返回 True", used_tools=False)
        assert result.verified is False
        assert any(a.level == AssertionLevel.HARD_FACT for a in result.assertions)
        assert result.corrected_text != ""

    def test_code_claim_with_tools_ok(self):
        pv = PriorVerifier()
        result = pv.analyze("在文件 foo.py 中函数 bar 返回 True", used_tools=True)
        hard = [a for a in result.assertions if a.level == AssertionLevel.HARD_FACT]
        assert len(hard) == 0

    def test_inference_markers_detected(self):
        pv = PriorVerifier()
        result = pv.analyze("这应该是一个bug")
        assert any(a.level == AssertionLevel.SOFT_INFERENCE for a in result.assertions)

    def test_unsupported_markers_detected(self):
        pv = PriorVerifier()
        result = pv.analyze("肯定是这个原因导致的")
        assert any(a.level == AssertionLevel.UNSUPPORTED for a in result.assertions)
        assert "过度自信" in result.corrected_text

    def test_strict_mode_warnings(self):
        pv = PriorVerifier(strict_mode=True)
        result = pv.analyze("函数 foo 调用了 bar", used_tools=False)
        assert len(result.warnings) > 0

    def test_should_trigger_re_verification(self):
        pv = PriorVerifier()
        result = pv.analyze("肯定函数 foo 和变量 bar 的值是 true", used_tools=False)
        assert pv.should_trigger_re_verification(result) is True

    def test_should_not_trigger_re_verification(self):
        pv = PriorVerifier()
        result = pv.analyze("今天天气不错")
        assert pv.should_trigger_re_verification(result) is False

    def test_mark_inferences(self):
        pv = PriorVerifier()
        text = "这大概是一个问题"
        marked = pv.mark_inferences(text)
        assert "*" in marked

    def test_verification_result_frozen(self):
        vr = VerificationResult(
            original="test", assertions=(), verified=True,
        )
        assert vr.original == "test"


# ── MetaCognition ──────────────────────────────────────────

class TestConfidenceCalibrator:
    def test_initial_accuracy_zero(self):
        cc = ConfidenceCalibrator()
        assert cc.get_accuracy(Domain.CODE_UNDERSTANDING) == 0.0

    def test_record_success(self):
        cc = ConfidenceCalibrator()
        cc.record_outcome(Domain.CODE_UNDERSTANDING, correct=True)
        assert cc.get_accuracy(Domain.CODE_UNDERSTANDING) == 1.0

    def test_mixed_outcomes(self):
        cc = ConfidenceCalibrator()
        cc.record_outcome(Domain.DEBUGGING, correct=True)
        cc.record_outcome(Domain.DEBUGGING, correct=True)
        cc.record_outcome(Domain.DEBUGGING, correct=False)
        assert cc.get_accuracy(Domain.DEBUGGING) == pytest.approx(2 / 3)

    def test_calibration_score_empty(self):
        cc = ConfidenceCalibrator()
        assert cc.calibration_score() == 0.5

    def test_calibration_score_with_data(self):
        cc = ConfidenceCalibrator()
        cc.record_outcome(Domain.CODE_UNDERSTANDING, correct=True)
        cc.record_outcome(Domain.CODE_UNDERSTANDING, correct=False)
        assert cc.calibration_score() == 0.5


class TestBlindSpotDetector:
    def test_no_blind_spots_initially(self):
        bsd = BlindSpotDetector()
        assert bsd.detect_blind_spots() == ()

    def test_detect_blind_spot_after_threshold(self):
        bsd = BlindSpotDetector()
        for _ in range(3):
            bsd.record_error(Domain.SECURITY, "missed vuln")
        spots = bsd.detect_blind_spots(threshold=2)
        assert "security" in spots

    def test_error_summary(self):
        bsd = BlindSpotDetector()
        bsd.record_error(Domain.DEBUGGING, "off-by-one")
        bsd.record_error(Domain.DEBUGGING, "null pointer")
        summary = bsd.get_error_summary("debugging")
        assert len(summary) == 2


class TestMetaCognition:
    def test_record_success_and_failure(self):
        mc = MetaCognition()
        mc.record_success(Domain.CODE_GENERATION)
        mc.record_failure(Domain.DEBUGGING, error_description="wrong fix")
        snap = mc.get_snapshot()
        assert len(snap.boundaries) == 2

    def test_snapshot_summary(self):
        mc = MetaCognition()
        mc.record_success(Domain.CODE_GENERATION)
        snap = mc.get_snapshot()
        assert "校准" in snap.summary

    def test_system_prompt_empty_when_confident(self):
        mc = MetaCognition()
        for _ in range(5):
            mc.record_success(Domain.CODE_GENERATION)
        assert mc.get_system_prompt_injection() == ""

    def test_system_prompt_injection_with_blind_spots(self):
        mc = MetaCognition()
        for _ in range(3):
            mc.record_failure(Domain.SECURITY, error_description="missed vuln")
        text = mc.get_system_prompt_injection()
        assert "盲区" in text

    def test_classify_confidence_unknown_for_small_samples(self):
        mc = MetaCognition()
        mc.record_success(Domain.CODE_GENERATION)
        snap = mc.get_snapshot()
        gen = [b for b in snap.boundaries if b.domain == Domain.CODE_GENERATION]
        assert gen[0].confidence == ConfidenceLevel.UNKNOWN


# ── LayeredMemory ──────────────────────────────────────────

class TestExperience:
    def test_create_defaults(self):
        exp = Experience.create(problem="test problem")
        assert exp.problem == "test problem"
        assert exp.recall_count == 0
        assert exp.emotion == EmotionIntensity.NONE
        assert exp.weight == 1.0

    def test_create_with_params(self):
        exp = Experience.create(
            problem="p", hypothesis="h", action="a",
            result="r", reflection="ref",
            emotion=EmotionIntensity.HIGH,
            associations=("tag1", "tag2"),
        )
        assert exp.hypothesis == "h"
        assert exp.associations == ("tag1", "tag2")

    def test_frozen(self):
        exp = Experience.create(problem="p")
        try:
            exp.problem = "changed"
            assert False, "Should be frozen"
        except AttributeError:
            pass


class TestEbbinghausWeight:
    def test_recent_high_weight(self):
        now = datetime.now(timezone.utc)
        w = ebbinghaus_weight(
            now, now, recall_count=0, deny_count=0,
            emotion=EmotionIntensity.NONE, association_count=0,
        )
        assert w == pytest.approx(1.0)

    def test_decay_over_time(self):
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=10)
        w = ebbinghaus_weight(
            old, old, recall_count=0, deny_count=0,
            emotion=EmotionIntensity.NONE, association_count=0,
        )
        assert w < 0.5

    def test_repetition_boosts_weight(self):
        now = datetime.now(timezone.utc)
        w_none = ebbinghaus_weight(
            now, now, recall_count=0, deny_count=0,
            emotion=EmotionIntensity.NONE, association_count=0,
        )
        w_rep = ebbinghaus_weight(
            now, now, recall_count=5, deny_count=0,
            emotion=EmotionIntensity.NONE, association_count=0,
        )
        assert w_rep > w_none

    def test_deny_penalty(self):
        now = datetime.now(timezone.utc)
        w_ok = ebbinghaus_weight(
            now, now, recall_count=0, deny_count=0,
            emotion=EmotionIntensity.NONE, association_count=0,
        )
        w_denied = ebbinghaus_weight(
            now, now, recall_count=0, deny_count=3,
            emotion=EmotionIntensity.NONE, association_count=0,
        )
        assert w_denied < w_ok

    def test_emotion_boost(self):
        now = datetime.now(timezone.utc)
        w_low = ebbinghaus_weight(
            now, now, recall_count=0, deny_count=0,
            emotion=EmotionIntensity.LOW, association_count=0,
        )
        w_high = ebbinghaus_weight(
            now, now, recall_count=0, deny_count=0,
            emotion=EmotionIntensity.HIGH, association_count=0,
        )
        assert w_high > w_low


class TestCommonKnowledge:
    def test_lookup_existing(self):
        ck = CommonKnowledge()
        result = ck.lookup("灵克")
        assert result is not None
        assert result["en"] == "LingClaude"

    def test_lookup_missing(self):
        ck = CommonKnowledge()
        assert ck.lookup("不存在") is None

    def test_search_by_en(self):
        ck = CommonKnowledge()
        results = ck.search("LingResearch")
        assert len(results) >= 1

    def test_search_by_role(self):
        ck = CommonKnowledge()
        results = ck.search("编程")
        assert len(results) >= 1

    def test_to_prompt_text(self):
        ck = CommonKnowledge()
        text = ck.to_prompt_text()
        assert "灵克" in text
        assert "LingClaude" in text

    def test_extra_facts(self):
        ck = CommonKnowledge(extra={"测试": {"en": "Test", "alias": "测试,Test", "role": "测试角色"}})
        assert ck.lookup("测试") is not None


class TestWorkingMemory:
    def test_append_and_get(self):
        wm = WorkingMemory(capacity=5)
        wm.append("user", "hello")
        wm.append("assistant", "hi")
        recent = wm.get_recent(2)
        assert len(recent) == 2
        assert recent[0] == ("user", "hello")

    def test_ring_buffer_overflow(self):
        wm = WorkingMemory(capacity=3)
        for i in range(5):
            wm.append("user", f"msg{i}")
        assert wm.size == 3
        recent = wm.get_recent(1)
        assert recent[0] == ("user", "msg4")

    def test_clear(self):
        wm = WorkingMemory()
        wm.append("user", "test")
        wm.clear()
        assert wm.size == 0


class TestInMemoryExperienceStore:
    def test_store_and_recall(self):
        store = InMemoryExperienceStore()
        exp = Experience.create(problem="python import error", reflection="check sys.path")
        store.store(exp)
        results = store.recall("import")
        assert len(results) == 1
        assert results[0].id == exp.id

    def test_record_recall(self):
        store = InMemoryExperienceStore()
        exp = Experience.create(problem="test")
        store.store(exp)
        store.record_recall(exp.id)
        results = store.recall("test")
        assert results[0].recall_count == 1

    def test_record_deny(self):
        store = InMemoryExperienceStore()
        exp = Experience.create(problem="test")
        store.store(exp)
        store.record_deny(exp.id)
        results = store.recall("test")
        assert results[0].deny_count == 1
        assert results[0].weight < exp.weight

    def test_decay_removes_low_weight(self):
        store = InMemoryExperienceStore()
        exp = Experience.create(problem="old stuff")
        store.store(exp)
        old_exp = store._experiences[exp.id]
        decayed = Experience(
            id=old_exp.id, problem=old_exp.problem, hypothesis=old_exp.hypothesis,
            action=old_exp.action, result=old_exp.result, reflection=old_exp.reflection,
            created_at=old_exp.created_at - timedelta(days=365),
            last_recalled=old_exp.last_recalled - timedelta(days=365),
            recall_count=0, deny_count=5, emotion=EmotionIntensity.NONE,
            associations=(), weight=0.01,
        )
        store._experiences[exp.id] = decayed
        store.decay_all()
        assert exp.id not in store._experiences

    def test_get_stats(self):
        store = InMemoryExperienceStore()
        store.store(Experience.create(problem="a"))
        store.store(Experience.create(problem="b"))
        stats = store.get_stats()
        assert stats["total_experiences"] == 2


class TestLayeredMemory:
    def test_inject_common_to_prompt(self):
        lm = LayeredMemory()
        text = lm.inject_common_to_prompt()
        assert "灵克" in text

    def test_record_and_recall_experience(self):
        lm = LayeredMemory()
        exp = Experience.create(
            problem="import error in flask",
            reflection="check requirements.txt",
            associations=("flask", "import"),
        )
        lm.record_experience(exp)
        results = lm.recall_experience("flask")
        assert len(results) == 1

    def test_meta_facts(self):
        lm = LayeredMemory()
        lm.record_meta("boundary_debugging", "weak at async debugging")
        assert lm.get_meta("boundary_debugging") == "weak at async debugging"

    def test_shared_facts(self):
        lm = LayeredMemory()
        lm.record_shared("consensus_style", "use pathlib not os.path")
        assert lm.get_shared("consensus_style") == "use pathlib not os.path"

    def test_build_context_injection(self):
        lm = LayeredMemory()
        lm.record_experience(Experience.create(
            problem="flask route error",
            reflection="check decorator order",
        ))
        ctx = lm.build_context_injection("flask")
        assert "灵克" in ctx
        assert "flask" in ctx

    def test_decay(self):
        lm = LayeredMemory()
        lm.record_experience(Experience.create(problem="test"))
        count = lm.decay()
        assert count == 1

    def test_close(self):
        lm = LayeredMemory()
        lm.close()


# ── Integration: QueryEngine with intelligence modules ─────

class TestQueryEngineIntelligence:
    def test_engine_has_intelligence_properties(self):
        from lingclaude.core.query_engine import QueryEngine
        engine = QueryEngine()
        assert engine.prior_verifier is not None
        assert engine.meta_cognition is not None
        assert engine.layered_memory is not None

    def test_system_prompt_includes_common_knowledge(self):
        from lingclaude.core.query_engine import QueryEngine
        engine = QueryEngine()
        prompt = engine._build_adaptive_system_prompt()
        assert "灵克" in prompt
        assert "LingClaude" in prompt

    def test_reset_clears_working_memory(self):
        from lingclaude.core.query_engine import QueryEngine
        engine = QueryEngine()
        engine._layered_memory.working.append("user", "test")
        engine.reset()
        assert engine._layered_memory.working.size == 0

    def test_imports_from_core_init(self):
        from lingclaude.core import (
            PriorVerifier, MetaCognition, LayeredMemory,
            Experience, EmotionIntensity, CommonKnowledge,
        )
        assert PriorVerifier is not None
        assert MetaCognition is not None
        assert LayeredMemory is not None
        assert Experience is not None
        assert EmotionIntensity is not None
        assert CommonKnowledge is not None

