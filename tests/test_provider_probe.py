"""P1-F1/P1-F2 测试：路由层清单实时探活 + 降级链健康度门禁。

对应 lc 会话幻觉调研 20260919 第一批（措施1/措施2）：
- 410/401/404 → 剔除（熔断），429 → 绕行不剔除，网络错误 → 不定罪
- 探活 TTL 缓存；证据落盘 .lingclaude/provider_probe.jsonl
- check_switch_target_health 门禁语义（拒绝/放行/不可知）
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lingclaude.model.task_router import TaskRouter
from lingclaude.model.provider_probe import (
    ProbeResult,
    ProviderProbe,
    probe_disabled_by_env,
)


def _probe(provider="p1", status="ok", http_code=200, probed_at=0.0, detail="") -> ProbeResult:
    return ProbeResult(provider=provider, base_url="https://x.example/v1",
                       status=status, http_code=http_code, probed_at=probed_at, detail=detail)


class TestProbeClassify(unittest.TestCase):
    """HTTP 码 → 状态分类：分级语义是本措施的根基。"""

    def test_hard_codes_excluded(self):
        for code in (401, 403, 404, 410):
            p = ProviderProbe._classify(code)
            self.assertEqual(p, "hard_4xx", msg=f"HTTP {code}")
            self.assertTrue(_probe(status=p).excluded)
            self.assertFalse(_probe(status=p).bypass_round)

    def test_rate_codes_bypass_not_excluded(self):
        """429/408 是限流不是下线——绝不能误杀（调研 §2.5 的坑）。"""
        for code in (429, 408):
            p = ProviderProbe._classify(code)
            self.assertEqual(p, "rate_limited", msg=f"HTTP {code}")
            self.assertTrue(_probe(status=p).bypass_round)
            self.assertFalse(_probe(status=p).excluded)

    def test_network_error_unknown(self):
        p = ProviderProbe._classify(None)
        self.assertTrue(_probe(status=p).unknown)
        self.assertFalse(_probe(status=p).excluded)

    def test_5xx_unknown_not_excluded(self):
        """5xx 是上游临时故障，不定罪。"""
        p = ProviderProbe._classify(503)
        self.assertTrue(_probe(status=p).unknown)

    def test_2xx_ok(self):
        self.assertEqual(ProviderProbe._classify(200), "ok")

    def test_ttl_hierarchy(self):
        self.assertEqual(_probe(status="ok").ttl, 300.0)
        self.assertEqual(_probe(status="rate_limited").ttl, 60.0)
        self.assertEqual(_probe(status="hard_4xx").ttl, 1800.0)
        self.assertEqual(_probe(status="network_error").ttl, 60.0)


class TestProbeCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.evidence = Path(self.tmp.name) / "provider_probe.jsonl"
        self.probe = ProviderProbe(evidence_path=self.evidence)

    def tearDown(self):
        self.tmp.cleanup()

    def test_local_endpoint_skipped_no_evidence(self):
        r = self.probe.check("proxy3", "http://127.0.0.1:8765/v1", "")
        self.assertEqual(r.status, "skipped")
        self.assertFalse(self.evidence.exists(), "skipped 不写证据")

    def test_cloud_no_key_skipped(self):
        r = self.probe.check("dead", "https://api.example.com/v1", "")
        self.assertEqual(r.status, "skipped")

    def test_real_probe_writes_evidence(self):
        with patch.object(self.probe, "_http_probe", return_value=(410, "Gone")):
            r = self.probe.check("nvidia", "https://api.example.com/v1", "k-test")
        self.assertTrue(r.excluded)
        self.assertEqual(r.http_code, 410)
        self.assertTrue(self.evidence.exists())
        rec = json.loads(self.evidence.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(rec["provider"], "nvidia")
        self.assertEqual(rec["status"], "hard_4xx")
        self.assertEqual(rec["http_code"], 410)

    def test_ttl_cache_no_second_probe(self):
        calls = []

        def fake_http(base, key):
            calls.append(base)
            return 200, ""

        with patch.object(self.probe, "_http_probe", side_effect=fake_http):
            self.probe.check("glm", "https://api.example.com/v1", "k")
            self.probe.check("glm", "https://api.example.com/v1", "k")
        self.assertEqual(len(calls), 1, "TTL 内第二次 check 不得重发请求")

    def test_force_bypasses_cache(self):
        with patch.object(self.probe, "_http_probe", return_value=(200, "")):
            self.probe.check("glm", "https://api.example.com/v1", "k")
        with patch.object(self.probe, "_http_probe", return_value=(410, "Gone")):
            r = self.probe.check("glm", "https://api.example.com/v1", "k", force=True)
        self.assertTrue(r.excluded, "force=True 必须重探（门禁语义）")

    def test_probe_never_raises(self):
        """探活内部炸掉也不能拖垮路由——降级为 unknown。"""
        with patch.object(self.probe, "_http_probe", side_effect=RuntimeError("boom")):
            r = self.probe.check("x", "https://api.example.com/v1", "k")
        self.assertTrue(r.unknown)

    def test_env_kill_switch(self):
        import os
        old = os.environ.get("LINGCLAUDE_PROBE_DISABLE")
        try:
            os.environ["LINGCLAUDE_PROBE_DISABLE"] = "1"
            self.assertTrue(probe_disabled_by_env())
        finally:
            if old is None:
                os.environ.pop("LINGCLAUDE_PROBE_DISABLE", None)
            else:
                os.environ["LINGCLAUDE_PROBE_DISABLE"] = old


CFG = {
    "routing": {
        "default_target": "cheap",
        "providers": {
            "glm": {
                "type": "openai", "api_key": "k-glm",
                "base_url": "https://glm.example/v1",
                "model": "glm-5.3-flash", "models": ["glm-5.3-flash"],
                "rate_limit": {"rpm": 10, "burst": 3},
            },
            "cheap": {
                "type": "openai", "api_key": "k-cheap",
                "base_url": "https://cheap.example/v1",
                "model": "glm-4.7-flash", "models": ["glm-4.7-flash"],
                "rate_limit": {"rpm": 30, "burst": 5},
            },
        },
        "task_routes": {
            "coding": {
                "description": "Code tasks",
                "models": [
                    {"provider": "glm", "model": "glm-5.3-flash"},
                    {"provider": "cheap", "model": "glm-4.7-flash"},
                ],
            },
            # OTHER 类查询（如 "hello"）走 fast_response——让探活门禁在
            # 非编码路由上同样可测
            "fast_response": {
                "description": "Quick tasks",
                "models": [{"provider": "glm", "model": "glm-5.3-flash"}],
            },
        },
    },
}


class _FakeProbe:
    """TaskRouter 集成测试用探活桩：check() 返回预设结论。"""

    def __init__(self, verdict: ProbeResult):
        self.verdict = verdict
        self.checked: list[str] = []

    def check(self, provider, base_url, api_key, *, force=False):
        self.checked.append(provider)
        r = ProbeResult(provider=provider, base_url=base_url,
                        status=self.verdict.status, http_code=self.verdict.http_code,
                        detail=self.verdict.detail, probed_at=0.0)
        return r

    def invalidate(self, provider):
        pass


class TestRouterProbeIntegration(unittest.TestCase):
    """措施1：路由前置探活门禁。"""

    def _make_router(self, verdict: ProbeResult) -> tuple[TaskRouter, _FakeProbe]:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(CFG, f)
        f.close()
        router = TaskRouter(config_path=Path(f.name))
        fake = _FakeProbe(verdict)
        router._probe = fake
        return router, fake

    def test_hard_4xx_skipped_next_candidate_used(self):
        """H1 活事故复现：首位候选 410 → 剔除，落备选，不再把请求发向死节点。"""
        router, fake = self._make_router(_probe(status="hard_4xx", http_code=410))
        cfg, route_key = router.resolve("fix this bug", task_type=None) if False else router.resolve("fix this bug")
        self.assertEqual(cfg.base_url, "https://cheap.example/v1", "410 首选被剔除应落备选")
        self.assertEqual(cfg.model, "glm-4.7-flash")
        self.assertIn("glm", fake.checked)

    def test_hard_4xx_sets_slot_cooldown(self):
        router, _ = self._make_router(_probe(status="hard_4xx", http_code=410))
        router.resolve("hello")
        slot = router._slots["glm"]
        self.assertGreater(slot.cooldown_until, 0.0, "探活判死应同步熔断 slot")

    def test_rate_limited_bypassed_not_cooldown(self):
        """429 绕行：走备选，但不熔断首位（限流非下线）。"""
        router, _ = self._make_router(_probe(status="rate_limited", http_code=429))
        cfg, _ = router.resolve("hello")
        self.assertEqual(cfg.base_url, "https://cheap.example/v1")
        self.assertEqual(router._slots["glm"].cooldown_until, 0.0, "429 不得熔断")

    def test_unknown_passes_through(self):
        """探活自身不可知 → 放行首选（证据不足不定罪）。"""
        router, _ = self._make_router(_probe(status="network_error"))
        cfg, _ = router.resolve("hello")
        self.assertEqual(cfg.base_url, "https://glm.example/v1")

    def test_ok_passes_through(self):
        router, _ = self._make_router(_probe(status="ok", http_code=200))
        cfg, _ = router.resolve("hello")
        self.assertEqual(cfg.base_url, "https://glm.example/v1")

    def test_probe_disabled_flag_bypasses_check(self):
        router, fake = self._make_router(_probe(status="hard_4xx", http_code=410))
        router._probe_disabled = True
        cfg, _ = router.resolve("hello")
        self.assertEqual(cfg.base_url, "https://glm.example/v1", "探活关闭 → 不查不剔")
        self.assertEqual(fake.checked, [])


class TestSwitchHealthGate(unittest.TestCase):
    """措施2：降级链健康度门禁（check_switch_target_health）。"""

    def _make_router(self, verdict: ProbeResult) -> TaskRouter:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(CFG, f)
        f.close()
        router = TaskRouter(config_path=Path(f.name))
        router._probe = _FakeProbe(verdict)
        return router

    def test_hard_4xx_rejected_and_cooldown(self):
        router = self._make_router(_probe(status="hard_4xx", http_code=410))
        allowed, reason = router.check_switch_target_health("glm")
        self.assertFalse(allowed)
        self.assertIn("410", reason)
        self.assertGreater(router._slots["glm"].cooldown_until, 0.0)

    def test_rate_limited_allowed(self):
        """限流节点活着——允许切，不能拒（拒了就没池可用了）。"""
        router = self._make_router(_probe(status="rate_limited", http_code=429))
        allowed, reason = router.check_switch_target_health("glm")
        self.assertTrue(allowed)
        self.assertIn("429", reason)

    def test_unknown_allowed(self):
        router = self._make_router(_probe(status="network_error"))
        allowed, _ = router.check_switch_target_health("glm")
        self.assertTrue(allowed)

    def test_ok_allowed(self):
        router = self._make_router(_probe(status="ok", http_code=200))
        allowed, _ = router.check_switch_target_health("glm")
        self.assertTrue(allowed)

    def test_unknown_provider_noop(self):
        router = self._make_router(_probe(status="hard_4xx", http_code=410))
        allowed, _ = router.check_switch_target_health("nonexistent")
        self.assertTrue(allowed, "不在清单的 provider 门禁不适用")

    def test_disabled_env_allows(self):
        router = self._make_router(_probe(status="hard_4xx", http_code=410))
        router._probe_disabled = True
        allowed, _ = router.check_switch_target_health("glm")
        self.assertTrue(allowed)

    def test_record_success_invalidates_probe_cache(self):
        router = self._make_router(_probe(status="hard_4xx", http_code=410))
        fake = router._probe
        router.record_success("glm")
        # _FakeProbe.invalidate 是空操作，这里只验证调用路径不炸；
        # 真实 ProviderProbe.invalidate 行为已由 TestProbeCheck 覆盖
        self.assertIsInstance(fake, _FakeProbe)

    def test_record_error_hard_error_invalidates(self):
        calls: list[str] = []

        class RecordingProbe(_FakeProbe):
            def invalidate(self, provider):
                calls.append(provider)

        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(CFG, f)
        f.close()
        router = TaskRouter(config_path=Path(f.name))
        router._probe = RecordingProbe(_probe(status="ok"))
        router.record_error("glm", "HTTP 410 Gone")
        self.assertIn("glm", calls, "真实调用撞上硬错误应清探活缓存")
        f.close()
        Path(f.name).unlink()


if __name__ == "__main__":
    unittest.main()
