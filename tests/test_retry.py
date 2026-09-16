"""Tests for lingclaude.model.retry"""
from __future__ import annotations

import unittest

from lingclaude.model.retry import (
    GlmRetryPolicy,
    RetrySnapshot,
    handle_429,
    is_hard_quota_error,
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


class TestIsHardQuotaError(unittest.TestCase):
    """2026-09-16 GLM 1308 5h 限额事故回归：硬配额必须与普通限流区分。"""

    def test_glm_1308_real_error(self):
        # 真实事故报文体（含 code + 重置时间戳语义）
        err = '{"error":{"code":"1308","message":"已达到 5 小时的使用上限，请于 2026-09-16 23:59 重置后重试"}}'
        self.assertTrue(is_hard_quota_error(err))

    def test_usage_limit_en(self):
        self.assertTrue(is_hard_quota_error("usage limit reached for org"))

    def test_quota_exceeded(self):
        self.assertTrue(is_hard_quota_error("billing_hard_limit_reached"))

    def test_combo_form(self):
        # code 变化时的组合形态：已达到 + 上限 + 重置
        self.assertTrue(is_hard_quota_error("额度已达到套餐上限，将于稍后重置"))

    def test_not_hard_quota_429(self):
        # 普通限流不能误判为硬配额（否则退避重试被跳过）
        self.assertFalse(is_hard_quota_error("HTTP 429 Too Many Requests"))

    def test_not_hard_quota_busy(self):
        self.assertFalse(is_hard_quota_error("模型访问量过大，请稍后再试"))

    def test_not_hard_quota_other(self):
        self.assertFalse(is_hard_quota_error("Connection refused"))

    def test_not_hard_quota_empty(self):
        self.assertFalse(is_hard_quota_error(""))


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
        p = GlmRetryPolicy(models=["glm-5.3-flash", "glm-5.1"], primary_retry_limit=1)
        p.record_failure()
        p.degrade()
        self.assertIsNone(p.get_next_model())

    def test_reset_to_primary(self):
        p = GlmRetryPolicy(models=["glm-5.3-flash", "glm-5.1", "glm-5"], primary_retry_limit=1)
        p.record_failure()
        p.degrade()
        self.assertTrue(p.is_degraded)
        model = p.reset_to_primary()
        self.assertEqual(model, "glm-5.3-flash")
        self.assertTrue(p.is_primary)

    def test_should_retry_primary_by_count(self):
        p = GlmRetryPolicy(
            models=["glm-5.3-flash", "glm-5.1"],
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

    def test_configure_primary_non_glm(self):
        """2026-09-11: 守卫放宽后非 glm 模型也可置主（修复 /model deepseek-v4-flash）"""
        p = GlmRetryPolicy()
        old_first = p.models[0]
        p.configure_primary("not-glm")
        # 行为已变更: 非 glm 也可成为主模型
        self.assertEqual(p.models[0], "not-glm")
        self.assertNotEqual(p.models[0], old_first)

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
    def test_degrade_blocked_for_non_glm_primary(self):
        """P0-C: 非 GLM 主模型禁止假降级（deepseek 平台无 glm fallback）"""
        p = GlmRetryPolicy()
        p.configure_primary("deepseek-v4-flash")
        p.record_failure()
        p.record_failure()
        p.record_failure()
        result = p.degrade()
        self.assertIsNone(result)
        self.assertTrue(p.is_primary)
        self.assertEqual(p.current_model, "deepseek-v4-flash")

    def test_degrade_allowed_for_glm_primary(self):
        """P0-C: GLM 主模型正常降级"""
        p = GlmRetryPolicy()
        p.configure_primary("glm-5.3-flash")
        p.record_failure()
        p.record_failure()
        p.record_failure()
        result = p.degrade()
        self.assertIsNotNone(result)
        self.assertFalse(p.is_primary)
        self.assertEqual(p.current_model, "glm-5.1")

    def test_primary_model_tracked(self):
        """P0-C: configure_primary 记录 primary_model 供 degrade 判定"""
        p = GlmRetryPolicy()
        p.configure_primary("deepseek-v4-flash")
        self.assertEqual(p.primary_model, "deepseek-v4-flash")

    def test_degrade_on_primary(self):
        p = GlmRetryPolicy(models=["glm-5.3-flash", "glm-5.1"], primary_retry_limit=1)
        result = handle_429(p, 0)
        self.assertEqual(result, "glm-5.1")
        self.assertTrue(p.is_degraded)

    def test_circuit_open_returns_none(self):
        p = GlmRetryPolicy(circuit_failure_threshold=1, circuit_cooldown=300.0)
        p.record_failure(is_rate_limit=True)
        result = handle_429(p, 1)
        self.assertIsNone(result)

    def test_retry_primary_from_degraded(self):
        p = GlmRetryPolicy(
            models=["glm-5.3-flash", "glm-5.1"],
            primary_retry_limit=1,
            degraded_call_threshold=0,
        )
        p.record_failure()
        p.degrade()
        result = handle_429(p, 0)
        self.assertEqual(result, "glm-5.3-flash")


if __name__ == "__main__":
    unittest.main()
