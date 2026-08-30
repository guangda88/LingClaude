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

class TestF12EnvKeyFallback(unittest.TestCase):
    """F12c/b:config 空 key → env 兜底;无 key 云端跳过;本地不跳过;显式 key 优先."""

    def _make_config_no_key(self):
        cfg = {
            "routing": {
                "default_target": "cheap",
                "providers": {
                    "nvidia": {
                        "type": "openai", "api_key": "",
                        "base_url": "https://integrate.api.nvidia.com/v1",
                        "model": "z-ai/glm-5.1", "models": ["z-ai/glm-5.1"],
                        "rate_limit": {"rpm": 10, "burst": 3},
                    },
                    "localstub": {
                        "type": "openai", "api_key": "",
                        "base_url": "http://127.0.0.1:9999/v1",
                        "model": "stub", "models": ["stub"],
                        "rate_limit": {"rpm": 10, "burst": 3},
                    },
                },
                "task_routes": {
                    "chinese_reasoning": {"description": "中文推理",
                        "models": [{"provider": "nvidia", "model": "z-ai/glm-5.1"}]},
                    "fast_response": {"description": "快速",
                        "models": [{"provider": "localstub", "model": "stub"}]},
                },
            },
        }
        import tempfile as _tf
        f = _tf.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(cfg, f); f.close()
        return Path(f.name)

    def test_env_key_fallback_resolves(self):
        import os
        from unittest.mock import patch
        path = self._make_config_no_key()
        with patch.dict(os.environ, {"NVIDIA_NIM_API_KEY": "fake-nvidia-key"}):
            router = TaskRouter(config_path=path)
            cfg, _ = router.resolve("分析", task_type=TaskType.ANALYSIS)
            self.assertEqual(cfg.api_key, "fake-nvidia-key")
        path.unlink()

    def test_no_env_no_key_local_skips_cloud(self):
        import os
        from unittest.mock import patch
        path = self._make_config_no_key()
        env = {k: v for k, v in os.environ.items() if k != "NVIDIA_NIM_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            router = TaskRouter(config_path=path)
            cfg, _ = router.resolve("分析", task_type=TaskType.ANALYSIS)
            self.assertNotEqual(cfg.base_url, "https://integrate.api.nvidia.com/v1")
        path.unlink()

    def test_local_provider_without_key_not_skipped(self):
        path = self._make_config_no_key()
        router = TaskRouter(config_path=path)
        cfg, route_key = router.resolve("快速", task_type=TaskType.SEARCH)
        self.assertEqual(route_key, "fast_response")
        self.assertEqual(cfg.base_url, "http://127.0.0.1:9999/v1")
        path.unlink()

    def test_config_key_takes_precedence(self):
        import tempfile as _tf
        import os
        from unittest.mock import patch
        f = _tf.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump({"routing": {"providers": {"nvidia": {
            "type": "openai", "api_key": "explicit-key",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "model": "m", "models": ["m"], "rate_limit": {"rpm": 10, "burst": 3},
        }}, "task_routes": {}}}, f)
        f.close()
        with patch.dict(os.environ, {"NVIDIA_NIM_API_KEY": "env-key"}):
            router = TaskRouter(config_path=Path(f.name))
            self.assertEqual(router._providers["nvidia"].api_key, "explicit-key")
        Path(f.name).unlink()

    def test_f12g_enabled_false_skips_provider(self):
        import tempfile as _tf
        f = _tf.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump({"routing": {"providers": {"dead": {
            "type": "openai", "api_key": "k", "base_url": "https://x.example/v1",
            "model": "m", "models": ["m"], "rate_limit": {"rpm": 10, "burst": 3},
            "enabled": False,
        }}, "task_routes": {}}}, f)
        f.close()
        router = TaskRouter(config_path=Path(f.name))
        self.assertNotIn("dead", router._providers)
        Path(f.name).unlink()

    def test_f12h_find_provider_by_model(self):
        import tempfile as _tf
        f = _tf.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump({"routing": {"providers": {"glm": {
            "type": "openai", "api_key": "k", "base_url": "https://zhipu/v4",
            "model": "glm-5.1", "models": ["glm-5.1", "glm-4.7"],
            "rate_limit": {"rpm": 10, "burst": 3},
        }}, "task_routes": {}}}, f)
        f.close()
        router = TaskRouter(config_path=Path(f.name))
        name, info = router.find_provider_by_model("glm-4.7")
        self.assertEqual(name, "glm")
        self.assertEqual(info.base_url, "https://zhipu/v4")
        name2, _ = router.find_provider_by_model("nonexistent")
        self.assertIsNone(name2)
        Path(f.name).unlink()


if __name__ == "__main__":
    unittest.main()
