"""Tests for lingclaude.model.retry"""
from __future__ import annotations

import unittest

from lingclaude.model.retry import (
    GlmRetryPolicy,
    RetrySnapshot,
    handle_429,
    is_rate_limit_error,
)


class TestIsRateLimitError(unittest.TestCase):
    def test_429(self):
        self.assertTrue(is_rate_limit_error("HTTP 429 Too Many Requests"))

    def test_rate_limit(self):
        self.assertTrue(is_rate_limit_error("rate_limit exceeded"))

    def test_chinese_busy(self):
        self.assertTrue(is_rate_limit_error("模型访问量过大"))

    def test_normal_error(self):
        self.assertFalse(is_rate_limit_error("Connection refused"))

    def test_empty(self):
        self.assertFalse(is_rate_limit_error(""))


class TestGlmRetryPolicy(unittest.TestCase):
    def test_initial_state(self):
        p = GlmRetryPolicy()
        self.assertTrue(p.is_primary)
        self.assertFalse(p.is_degraded)
        self.assertEqual(p.current_model, p.models[0])

    def test_record_success_resets(self):
        p = GlmRetryPolicy()
        p.record_failure()
        p.record_success()
        self.assertTrue(p.is_primary)

    def test_degrade_after_failures(self):
        p = GlmRetryPolicy(primary_retry_limit=2)
        p.record_failure()
        self.assertFalse(p.should_degrade())
        p.record_failure()
        self.assertTrue(p.should_degrade())
        degraded = p.degrade()
        self.assertIsNotNone(degraded)
        self.assertTrue(p.is_degraded)

    def test_degrade_exhausted(self):
        p = GlmRetryPolicy(models=["a", "b"], primary_retry_limit=1)
        p.record_failure()
        p.degrade()
        self.assertIsNone(p.get_next_model())

    def test_reset_to_primary(self):
        p = GlmRetryPolicy(models=["a", "b", "c"], primary_retry_limit=1)
        p.record_failure()
        p.degrade()
        self.assertTrue(p.is_degraded)
        model = p.reset_to_primary()
        self.assertEqual(model, "a")
        self.assertTrue(p.is_primary)

    def test_should_retry_primary_by_count(self):
        p = GlmRetryPolicy(
            models=["a", "b"],
            primary_retry_limit=1,
            degraded_call_threshold=3,
        )
        p.record_failure()
        p.degrade()
        for _ in range(3):
            p.record_failure()
        self.assertTrue(p.should_retry_primary())

    def test_backoff(self):
        p = GlmRetryPolicy(backoff_base=5.0, backoff_max=30.0)
        self.assertAlmostEqual(p.get_backoff(0), 5.0)
        self.assertAlmostEqual(p.get_backoff(1), 10.0)
        self.assertAlmostEqual(p.get_backoff(10), 30.0)

    def test_circuit_breaker(self):
        p = GlmRetryPolicy(circuit_failure_threshold=2, circuit_cooldown=60.0)
        p.record_failure(is_rate_limit=True)
        self.assertFalse(p.circuit_open)
        p.record_failure(is_rate_limit=True)
        self.assertTrue(p.circuit_open)

    def test_circuit_cooldown_expires(self):
        p = GlmRetryPolicy(circuit_failure_threshold=1, circuit_cooldown=0.0)
        p.record_failure(is_rate_limit=True)
        self.assertFalse(p.circuit_open)

    def test_record_rpm(self):
        p = GlmRetryPolicy()
        count = p.record_rpm()
        self.assertEqual(count, 1)
        count = p.record_rpm()
        self.assertEqual(count, 2)

    def test_configure_primary(self):
        p = GlmRetryPolicy()
        p.configure_primary("glm-custom")
        self.assertEqual(p.models[0], "glm-custom")

    def test_configure_primary_ignored(self):
        p = GlmRetryPolicy()
        old = p.models[:]
        p.configure_primary("not-glm")
        self.assertEqual(p.models, old)

    def test_get_snapshot(self):
        p = GlmRetryPolicy()
        snap = p.get_snapshot()
        self.assertIsInstance(snap, RetrySnapshot)
        self.assertTrue(snap.is_degraded is False)

    def test_reset(self):
        p = GlmRetryPolicy(models=["a", "b"], primary_retry_limit=1)
        p.record_failure()
        p.degrade()
        p.reset()
        self.assertTrue(p.is_primary)


class TestHandle429(unittest.TestCase):
    def test_degrade_on_primary(self):
        p = GlmRetryPolicy(models=["a", "b"], primary_retry_limit=1)
        result = handle_429(p, 0)
        self.assertEqual(result, "b")
        self.assertTrue(p.is_degraded)

    def test_circuit_open_returns_none(self):
        p = GlmRetryPolicy(circuit_failure_threshold=1, circuit_cooldown=300.0)
        p.record_failure(is_rate_limit=True)
        result = handle_429(p, 1)
        self.assertIsNone(result)

    def test_retry_primary_from_degraded(self):
        p = GlmRetryPolicy(
            models=["a", "b"],
            primary_retry_limit=1,
            degraded_call_threshold=0,
        )
        p.record_failure()
        p.degrade()
        result = handle_429(p, 0)
        self.assertEqual(result, "a")


if __name__ == "__main__":
    unittest.main()
