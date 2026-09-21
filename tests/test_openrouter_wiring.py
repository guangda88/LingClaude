# tests/test_openrouter_wiring.py
"""P1-8 接线测试：TaskRouter api_key 热更新 + F12b 惰性自愈 + /openrouter 命令离线面。

钉住本批三个行为契约：
1. refresh_api_keys：env 注入后既有实例感知（返回变化数，幂等）；
2. F12b 自愈：env 后到（晚于 router 构造）时路由不再跳过该 provider；
3. /openrouter 补全词注册 + 插片契约常量稳定。
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from lingclaude.model.task_router import TaskRouter, _PROVIDER_ENV_KEY_MAP


def _write_cfg(path: Path) -> None:
    path.write_text(json.dumps({"routing": {
        "default_target": "openrouter",
        "providers": {
            "openrouter": {
                "type": "openai",
                "base_url": "https://openrouter.ai/api/v1",
                "model": "a/b:free",
                "models": ["a/b:free"],
                "api_key": "",
            },
            "glm": {
                "type": "openai",
                "base_url": "https://open.bigmodel.cn/api/coding/paas/v4",
                "model": "glm-x",
                "models": ["glm-x"],
                "api_key": "${GLM_TEST_KEY_X}",
            },
        },
        "task_routes": {"coding": {"models": [{"provider": "openrouter", "model": "a/b:free"}]}, "fast_response": {"models": [{"provider": "openrouter", "model": "a/b:free"}]}},
    }}), encoding="utf-8")


class TestRefreshApiKeys(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__file__).parent.parent / "data" / "tmp_test_or_wiring"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.cfg = self.tmp / "cfg.json"
        _write_cfg(self.cfg)
        # 探活关闭（resolve 路径不许打真网）+ env 隔离
        self._env_patcher = mock.patch.dict(
            os.environ, {"LINGCLAUDE_PROBE_DISABLE": "1"}, clear=False)
        self._env_patcher.start()
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ.pop("GLM_TEST_KEY_X", None)

    def tearDown(self):
        self._env_patcher.stop()
        try:
            self.cfg.unlink()
        except OSError:
            pass

    def test_refresh_after_env_injection(self):
        r = TaskRouter(config_path=self.cfg)
        self.assertEqual(r._providers["openrouter"].api_key, "")  # 构造时无 key

        os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-hot"
        changed = r.refresh_api_keys()
        self.assertEqual(changed, 1)
        self.assertEqual(r._providers["openrouter"].api_key, "sk-or-v1-hot")
        # 幂等：再刷不报变化
        self.assertEqual(r.refresh_api_keys(), 0)

    def test_refresh_ignores_empty_and_other_providers(self):
        r = TaskRouter(config_path=self.cfg)
        # 只注入 openrouter，glm 无 env → 不变化
        os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-2"
        self.assertEqual(r.refresh_api_keys(), 1)
        # 清空 env → 不回退已有 key（空解析不覆盖）
        os.environ.pop("OPENROUTER_API_KEY")
        r.refresh_api_keys()
        self.assertEqual(r._providers["openrouter"].api_key, "sk-or-v1-2")

    def test_f12b_selfheal_route_no_longer_skipped(self):
        """env 后到场景：构造时无 key，resolve 内部 F12b 自愈口自行救活。

        resolve 返回 (ModelConfig, route_key) 元组（签名实测 task_router.py:433）。
        """
        r = TaskRouter(config_path=self.cfg)
        os.environ["OPENROUTER_API_KEY"] = "hot-key-late-injected"
        from lingclaude.model.task_router import TaskType
        cfg_model, route_key = r.resolve("test", TaskType.CODE_GENERATION)
        self.assertEqual(route_key, "coding")
        self.assertEqual(cfg_model.api_key, "hot-key-late-injected")
        self.assertEqual(cfg_model.model, "a/b:free")

    def test_env_map_contains_openrouter(self):
        self.assertEqual(_PROVIDER_ENV_KEY_MAP.get("openrouter"), "OPENROUTER_API_KEY")


class TestOpenrouterContract(unittest.TestCase):
    """插片契约常量 + 命令注册（wiring_gate 同款消费，非死 import）。"""

    def test_module_contract(self):
        from lingclaude.model import openrouter_oauth as orx
        self.assertEqual(orx.ENV_KEY_NAME, "OPENROUTER_API_KEY")
        self.assertTrue(orx.OPENROUTER_EXCHANGE_URL.startswith(
            "https://openrouter.ai/api/v1/auth/keys"))
        self.assertEqual(orx._CALLBACK_TIMEOUT_S, 300.0)
        v, c = orx.make_pkce_pair()
        self.assertTrue(v and c)

    def test_slash_word_registered(self):
        from lingclaude.cli.commands import SLASH_COMPLETER_WORDS
        self.assertIn("/openrouter", SLASH_COMPLETER_WORDS)


if __name__ == "__main__":
    unittest.main()
