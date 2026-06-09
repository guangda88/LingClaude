"""Tests for lingclaude.core.rate_limiter"""
from __future__ import annotations

import time
import unittest

from lingclaude.core.rate_limiter import (
    LeakyBucket,
    ProviderSlot,
    SmoothRotator,
    jittered_backoff,
)


class TestLeakyBucket(unittest.TestCase):
    def test_acquire_immediate(self):
        b = LeakyBucket(max_tokens=10, refill_rate=100.0)
        self.assertTrue(b.acquire(tokens=1, timeout=0.1))

    def test_acquire_exhaust_and_wait(self):
        b = LeakyBucket(max_tokens=2, refill_rate=1000.0)
        self.assertTrue(b.acquire(tokens=1, timeout=0.1))
        self.assertTrue(b.acquire(tokens=1, timeout=0.1))
        self.assertTrue(b.acquire(tokens=1, timeout=2.0))

    def test_acquire_timeout(self):
        b = LeakyBucket(max_tokens=1, refill_rate=0.01)
        b.acquire(tokens=1, timeout=0.0)
        self.assertFalse(b.acquire(tokens=1, timeout=0.05))

    def test_refill_over_time(self):
        b = LeakyBucket(max_tokens=100, refill_rate=10000.0)
        b.acquire(tokens=100, timeout=0.0)
        time.sleep(0.02)
        self.assertTrue(b.acquire(tokens=1, timeout=0.1))


class TestProviderSlot(unittest.TestCase):
    def test_available_by_default(self):
        s = ProviderSlot(name="test", bucket=LeakyBucket(max_tokens=5, refill_rate=10.0))
        self.assertTrue(s.is_available)

    def test_cooldown_makes_unavailable(self):
        s = ProviderSlot(name="test", bucket=LeakyBucket(max_tokens=5, refill_rate=10.0))
        s.cooldown_until = time.monotonic() + 100
        self.assertFalse(s.is_available)


class TestSmoothRotator(unittest.TestCase):
    def test_next_provider_returns_slot(self):
        r = SmoothRotator({"a": {}, "b": {}})
        s = r.next_provider()
        self.assertIsNotNone(s)
        self.assertIn(s.name, ("a", "b"))

    def test_round_robin(self):
        r = SmoothRotator({"a": {}, "b": {}})
        names = [r.next_provider().name for _ in range(4)]
        self.assertEqual(names.count("a"), 2)
        self.assertEqual(names.count("b"), 2)

    def test_record_success_resets_errors(self):
        r = SmoothRotator({"a": {}})
        r._slots[0].consecutive_errors = 2
        r.record_success("a")
        self.assertEqual(r._slots[0].consecutive_errors, 0)

    def test_record_error_increments(self):
        r = SmoothRotator({"a": {}})
        r.record_error("a")
        self.assertEqual(r._slots[0].consecutive_errors, 1)
        self.assertEqual(r._slots[0].total_errors, 1)

    def test_record_error_triggers_cooldown(self):
        r = SmoothRotator({"a": {}})
        r.record_error("a")
        r.record_error("a")
        r.record_error("a")
        self.assertFalse(r._slots[0].is_available)

    def test_stats(self):
        r = SmoothRotator({"a": {}, "b": {}})
        r.next_provider()
        stats = r.stats()
        self.assertIn("a", stats)
        self.assertIn("b", stats)
        self.assertEqual(stats["a"]["requests"] + stats["b"]["requests"], 1)

    def test_no_available_provider(self):
        r = SmoothRotator({"a": {}})
        r._slots[0].cooldown_until = time.monotonic() + 1000
        self.assertIsNone(r.next_provider())


class TestJitteredBackoff(unittest.TestCase):
    def test_increases_with_attempts(self):
        b0 = jittered_backoff(0)
        jittered_backoff(1)
        b2 = jittered_backoff(2)
        self.assertLess(b0, b2)

    def test_capped(self):
        val = jittered_backoff(100, cap=10.0)
        self.assertLess(val, 20.0)


if __name__ == "__main__":
    unittest.main()
