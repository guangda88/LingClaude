"""P1-4/P2-5 幻觉调研第二、三批 — 落地测试。

覆盖：
- flash 门禁：决策路由首位轻量模型顺延（LINGCLAUDE_FLASH_GATE_DISABLE 可关）
- 模型切换声明：switch/pin 后 _model_switch_note 置位 + builder 注入
- builder 切换声明注入内容（身份声明 + NOT_FOUND 纪律）
- health_ranking 评分/重排/死节点识别
- _BASE_PROMPT NOT_FOUND/外部知识纪律
- hallucination_bench Layer 1 全过（CI gate 同源）
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lingclaude.model.task_router import TaskRouter, TaskType, _FLASH_MODEL_RE

SAMPLE_CONFIG = {
    "routing": {
        "default_target": "glm",
        "providers": {
            "glm": {
                "type": "openai",
                "api_key": "glm-key",
                "base_url": "http://localhost:8900/v1",
                "model": "glm-5.3-flash",
                "models": ["glm-5.3-flash"],
                "rate_limit": {"rpm": 10, "burst": 3},
            },
            "kimi": {
                "type": "openai",
                "api_key": "kimi-key",
                "base_url": "http://localhost:8901/v1",
                "model": "Kimi-K3",
                "models": ["Kimi-K3"],
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
                    {"provider": "glm", "model": "glm-5.3-flash"},
                    {"provider": "kimi", "model": "Kimi-K3"},
                ],
            },
            "fast_response": {
                "description": "Quick tasks",
                "models": [{"provider": "cheap", "model": "glm-4.7-flash"}],
            },
        },
    },
}


def _make_config(cfg=None) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(cfg or SAMPLE_CONFIG, f)
    f.close()
    return Path(f.name)


class TestFlashGate(unittest.TestCase):
    """P1-4: flash 级禁入决策位。"""

    def setUp(self):
        self.path = _make_config()
        self.router = TaskRouter(config_path=self.path)
        self.router._probe_disabled = True  # 本地假端点，探活必失败，关掉

    def tearDown(self):
        import os
        os.environ.pop("LINGCLAUDE_FLASH_GATE_DISABLE", None)
        self.path.unlink()

    def test_flash_first_candidate_skipped(self):
        """决策路由首位是 flash → 顺延到 Kimi-K3。"""
        import os
        os.environ.pop("LINGCLAUDE_FLASH_GATE_DISABLE", None)
        cfg, route = self.router.resolve(
            "write code", task_type=TaskType.CODE_GENERATION,
        )
        self.assertEqual(route, "coding")
        self.assertEqual(cfg.model, "Kimi-K3")

    def test_flash_gate_disable_env(self):
        """LINGCLAUDE_FLASH_GATE_DISABLE=1 → 恢复配置序首位。"""
        import os
        os.environ["LINGCLAUDE_FLASH_GATE_DISABLE"] = "1"
        cfg, route = self.router.resolve(
            "write code", task_type=TaskType.CODE_GENERATION,
        )
        self.assertEqual(route, "coding")
        self.assertEqual(cfg.model, "glm-5.3-flash")

    def test_non_decision_route_unaffected(self):
        """非决策路由（fast_response）首位 flash 不受门禁影响。"""
        import os
        os.environ.pop("LINGCLAUDE_FLASH_GATE_DISABLE", None)
        cfg, route = self.router.resolve("hi", task_type=TaskType.OTHER)
        self.assertEqual(route, "fast_response")
        self.assertEqual(cfg.model, "glm-4.7-flash")

    def test_only_first_position_gated(self):
        """flash 降到 2+ 位作兜底不受影响（只挡首位）。"""
        import os
        from lingclaude.model.task_router import _DECISION_ROUTE_KEYS, _TaskRoute, _ModelRef
        # 决策路由 coding：首位 glm flash 被门禁跳过 → 选中第 2 位 Kimi（探活关闭）
        os.environ.pop("LINGCLAUDE_FLASH_GATE_DISABLE", None)
        cfg, _ = self.router.resolve("write code", task_type=TaskType.CODE_GENERATION)
        self.assertEqual(cfg.model, "Kimi-K3")
        # fast_response 不在决策路由集合 → flash 首位放行
        self.assertNotIn("fast_response", _DECISION_ROUTE_KEYS)
        route = self.router._task_routes["fast_response"]
        picked = self.router._pick_from_route("fast_response", route, 512, 0.7)
        self.assertIsNotNone(picked)
        self.assertEqual(picked.model, "glm-4.7-flash")


class TestModelSwitchNote(unittest.TestCase):
    """P1-4: 降级/手动切换后强制声明当前模型。"""

    def test_note_model_switch_state(self):
        from lingclaude.core.query_engine_model_mixin import QueryEngineModelMixin

        class Engine(QueryEngineModelMixin):
            pass

        e = Engine()
        self.assertIsNone(e._model_switch_note)
        e._note_model_switch("Kimi-K3", pinned=False)
        self.assertEqual(e._model_switch_note["model"], "Kimi-K3")
        self.assertFalse(e._model_switch_note["pinned"])
        e._note_model_switch("glm-5.3-flash", pinned=True)
        self.assertTrue(e._model_switch_note["pinned"])


class TestBuilderSwitchInjection(unittest.TestCase):
    """P1-4: builder 注入模型切换声明。"""

    def _build(self, note):
        from lingclaude.core.system_prompt_builder import build_adaptive_system_prompt

        class _BM:
            hallucination_risk = 0.0
            frustration_rate = 0.0
            tool_error_rate = 0.0
            corrections_received = 0
            total_turns = 0
            tool_use_rate = 1.0
            tool_error_count = 0
            auto_sub_agent_threshold = 0

        class _LM:
            def inject_common_to_prompt(self):
                return ""

            def build_context_injection(self, current_query=""):
                return ""

        class _MC:
            def get_system_prompt_injection(self):
                return ""

        class _DD:
            def diagnose(self):
                class _D:
                    intervention_prompt = ""
                return _D()

        return build_adaptive_system_prompt(
            behavior=_BM(), layered_memory=_LM(), meta_cognition=_MC(),
            messages=[], session_cache_hits=0, dementia_detector=_DD(),
            project_index=None, model_switch_note=note,
        )

    def test_switch_note_injected(self):
        prompt = self._build({"model": "Kimi-K3", "pinned": False, "at": 0.0})
        self.assertIn("模型切换声明", prompt)
        self.assertIn("Kimi-K3", prompt)
        self.assertIn("NOT_FOUND", prompt)

    def test_no_note_no_injection(self):
        prompt = self._build(None)
        self.assertNotIn("模型切换声明", prompt)

    def test_base_prompt_disciplines(self):
        """P2-5b: NOT_FOUND + 外部知识工具验证进常驻提示词。"""
        from lingclaude.core.system_prompt_builder import _BASE_PROMPT
        self.assertIn("NOT_FOUND", _BASE_PROMPT)
        self.assertIn("web_search", _BASE_PROMPT)
        self.assertIn("参数量", _BASE_PROMPT)


class TestHealthRanking(unittest.TestCase):
    """P2-5a: 实测化评分核心。"""

    def test_available_rate(self):
        from lingclaude.model.health_ranking import compute_available_rate
        self.assertEqual(compute_available_rate("ok", bypass_round=False), 1.0)
        self.assertEqual(compute_available_rate("rate_limited", bypass_round=True), 0.5)
        self.assertEqual(compute_available_rate("hard_4xx", bypass_round=False), 0.0)
        self.assertEqual(compute_available_rate("network_error", bypass_round=False), 0.0)

    def test_hallucination_rates_parsing(self):
        from lingclaude.model.health_ranking import hallucination_rates
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"per_provider": {
                "glm": {"total": 10, "hallucinated": 3},
                "kimi": {"total": 0, "hallucinated": 0},
            }}, f)
            p = Path(f.name)
        rates = hallucination_rates(p)
        p.unlink()
        self.assertAlmostEqual(rates["glm"], 0.3)
        self.assertNotIn("kimi", rates)
        self.assertEqual(hallucination_rates(None), {})
        self.assertEqual(hallucination_rates(Path("/nonexistent.json")), {})

    def test_build_report_reorder_and_dead(self):
        from lingclaude.model.health_ranking import build_report
        routes = {"coding": [
            {"provider": "glm", "model": "glm-5.3-flash"},
            {"provider": "kimi", "model": "Kimi-K3"},
        ]}
        probe_results = {
            "glm": {"status": "hard_4xx", "http_code": 410, "detail": "gone",
                    "available_rate": 0.0, "excluded": True},
            "kimi": {"status": "ok", "http_code": 200, "detail": "",
                     "available_rate": 1.0, "excluded": False},
        }
        report = build_report(routes, probe_results, {})
        self.assertEqual(report["providers"]["glm"]["score"], 0.0)
        self.assertEqual(report["providers"]["kimi"]["score"], 1.0)
        self.assertEqual(
            report["route_suggestions"]["coding"]["suggested_order"],
            ["kimi/Kimi-K3", "glm/glm-5.3-flash"],
        )
        self.assertTrue(report["route_suggestions"]["coding"]["reorder_needed"])
        self.assertEqual(len(report["dead_providers_in_routes"]), 1)
        self.assertEqual(report["dead_providers_in_routes"][0]["route"], "coding")


class TestHallucinationBenchLayer1(unittest.TestCase):
    """P2-5c: bench Layer 1 与 CI gate 同源，必须全过。"""

    def test_layer1_all_pass(self):
        from tests.hallucination_bench import run_layer1
        failures = run_layer1()
        self.assertEqual(failures, [], f"bench failures: {failures}")


if __name__ == "__main__":
    unittest.main()
