"""Tests for lingclaude.core.comfort_zone"""
from __future__ import annotations

import unittest

from lingclaude.core.comfort_zone import (
    ComfortCheckResult,
    ComfortZoneDetector,
    ConclusionRisk,
    comfort_check_hook,
)


class TestComfortZoneDetector(unittest.TestCase):
    def setUp(self):
        self.detector = ComfortZoneDetector()

    def test_low_risk_with_falsification(self):
        result = self.detector.check(
            conclusion="The function returns correct values",
            queries_executed=("search for contradictory evidence disproving the function",),
        )
        self.assertEqual(result.risk, ConclusionRisk.LOW)
        self.assertTrue(result.falsifiable)

    def test_high_risk_negative_without_investigation(self):
        result = self.detector.check(
            conclusion="不存在这样的文件",
        )
        self.assertEqual(result.risk, ConclusionRisk.HIGH)
        self.assertTrue(result.least_effort_bias)

    def test_medium_risk_no_falsification(self):
        result = self.detector.check(
            conclusion="All tests pass",
            queries_executed=("ran pytest",),
        )
        self.assertFalse(result.falsifiable)
        self.assertEqual(result.risk, ConclusionRisk.MEDIUM)

    def test_evidence_ignored_with_few_sources(self):
        result = self.detector.check(
            conclusion="从未出现过这种错误",
            evidence_examined=("one_source",),
        )
        self.assertTrue(result.evidence_ignored)

    def test_evidence_not_ignored_with_enough_sources(self):
        result = self.detector.check(
            conclusion="从未出现过这种错误",
            evidence_examined=("src1", "src2", "src3"),
            queries_executed=("grep logs",),
        )
        self.assertFalse(result.evidence_ignored)

    def test_generate_follow_up_empty_when_low(self):
        result = ComfortCheckResult(
            conclusion="ok", risk=ConclusionRisk.LOW,
            falsifiable=True, least_effort_bias=False, evidence_ignored=False,
        )
        follow_ups = self.detector.generate_follow_up(result)
        self.assertEqual(follow_ups, ())

    def test_generate_follow_up_least_effort(self):
        result = ComfortCheckResult(
            conclusion="不存在", risk=ConclusionRisk.HIGH,
            falsifiable=False, least_effort_bias=True, evidence_ignored=True,
        )
        follow_ups = self.detector.generate_follow_up(result)
        self.assertGreater(len(follow_ups), 0)

    def test_conclusion_truncated(self):
        long_conclusion = "x" * 500
        result = self.detector.check(conclusion=long_conclusion)
        self.assertLessEqual(len(result.conclusion), 200)


class TestComfortCheckHook(unittest.TestCase):
    def test_returns_context_unchanged_for_low_risk(self):
        from lingclaude.core.hooks import HookContext
        ctx = HookContext(
            hook_type="POST_TASK",
            session_id="s1",
            prompt="hello",
            output="The sky is blue.",
        )
        result = comfort_check_hook(ctx)
        self.assertIsInstance(result, HookContext)

    def test_adds_metadata_for_high_risk(self):
        from lingclaude.core.hooks import HookContext
        ctx = HookContext(
            hook_type="POST_TASK",
            session_id="s1",
            prompt="check",
            output="从未存在这样的bug",
        )
        result = comfort_check_hook(ctx)
        self.assertIn("comfort_check", result.metadata)

    def test_passes_through_non_hook_context(self):
        self.assertIsNone(comfort_check_hook(None))


if __name__ == "__main__":
    unittest.main()
