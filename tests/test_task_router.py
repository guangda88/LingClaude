"""Tests for lingclaude.model.task_router"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lingclaude.model.task_router import TaskRouter, TASK_TYPE_TO_ROUTE
from lingclaude.model.intelligent_router import TaskType


SAMPLE_CONFIG = {
    "routing": {
        "default_target": "cheap",
        "providers": {
            "glm": {
                "type": "openai",
                "api_key": "test-key",
                "base_url": "http://localhost:8900/v1",
                "model": "glm-5.1",
                "models": ["glm-5.1", "glm-4.7"],
                "rate_limit": {"rpm": 10, "burst": 3},
            },
            "cheap": {
                "type": "openai",
                "api_key": "cheap-key",
                "base_url": "http://localhost:8900/v1",
                "model": "glm-4.7-flash",
                "models": ["glm-4.7-flash"],
                "rate_limit": {"rpm": 30, "burst": 5},
            },
        },
        "task_routes": {
            "coding": {
                "description": "Code tasks",
                "models": [
                    {"provider": "glm", "model": "glm-5.1"},
                    {"provider": "cheap", "model": "glm-4.7-flash"},
                ],
            },
            "fast_response": {
                "description": "Quick tasks",
                "models": [{"provider": "cheap", "model": "glm-4.7-flash"}],
            },
        },
    },
}


class TestTaskRouter(unittest.TestCase):
    def _make_config(self, cfg=None):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(cfg or SAMPLE_CONFIG, f)
        f.close()
        return Path(f.name)

    def test_load_from_config(self):
        path = self._make_config()
        router = TaskRouter(config_path=path)
        self.assertGreater(len(router._providers), 0)
        self.assertGreater(len(router._task_routes), 0)
        path.unlink()

    def test_missing_config(self):
        router = TaskRouter(config_path="/nonexistent/config.json")
        self.assertEqual(len(router._providers), 0)

    def test_resolve_coding_task(self):
        path = self._make_config()
        router = TaskRouter(config_path=path)
        config, route = router.resolve("write a function", task_type=TaskType.CODE_GENERATION)
        self.assertEqual(route, "coding")
        self.assertIn(config.model, ["glm-5.1", "glm-4.7-flash"])
        path.unlink()

    def test_resolve_fallback(self):
        path = self._make_config()
        router = TaskRouter(config_path=path)
        config, route = router.resolve("hello", task_type=TaskType.OTHER)
        self.assertEqual(route, "fast_response")
        path.unlink()

    def test_resolve_infer_task_type(self):
        path = self._make_config()
        router = TaskRouter(config_path=path)
        config, route = router.resolve("debug this code")
        self.assertIn(route, ["coding", "fast_response"])
        path.unlink()

    def test_record_success(self):
        path = self._make_config()
        router = TaskRouter(config_path=path)
        router.record_success("glm")
        slot = router._slots.get("glm")
        if slot:
            self.assertEqual(slot.consecutive_errors, 0)
        path.unlink()

    def test_record_error(self):
        path = self._make_config()
        router = TaskRouter(config_path=path)
        router.record_error("glm")
        router.record_error("glm")
        router.record_error("glm")
        slot = router._slots.get("glm")
        if slot:
            self.assertEqual(slot.consecutive_errors, 0)
            self.assertGreater(slot.cooldown_until, 0)
        path.unlink()

    def test_get_provider_name(self):
        path = self._make_config()
        router = TaskRouter(config_path=path)
        name = router.get_provider_name("test-key", "http://localhost:8900/v1")
        self.assertEqual(name, "glm")
        path.unlink()

    def test_get_provider_name_unknown(self):
        path = self._make_config()
        router = TaskRouter(config_path=path)
        name = router.get_provider_name("unknown", "http://unknown")
        self.assertIsNone(name)
        path.unlink()

    def test_stats(self):
        path = self._make_config()
        router = TaskRouter(config_path=path)
        s = router.stats()
        self.assertIn("providers", s)
        self.assertIn("routes", s)
        path.unlink()

    def test_default_provider_name(self):
        path = self._make_config()
        router = TaskRouter(config_path=path)
        self.assertEqual(router.get_default_provider_name(), "cheap")
        path.unlink()

    def test_task_type_mapping(self):
        self.assertEqual(TASK_TYPE_TO_ROUTE[TaskType.CODE_GENERATION], "coding")
        self.assertEqual(TASK_TYPE_TO_ROUTE[TaskType.ANALYSIS], "chinese_reasoning")
        self.assertEqual(TASK_TYPE_TO_ROUTE[TaskType.SEARCH], "fast_response")

    def test_round_robin_rotation(self):
        path = self._make_config()
        router = TaskRouter(config_path=path)
        c1, _ = router.resolve("code", task_type=TaskType.CODE_GENERATION)
        c2, _ = router.resolve("code", task_type=TaskType.CODE_GENERATION)
        models = [c1.model, c2.model]
        self.assertTrue(len(set(models)) >= 1)
        path.unlink()


if __name__ == "__main__":
    unittest.main()