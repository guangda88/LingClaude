"""Tests for lingclaude.model.intelligent_router"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lingclaude.model.intelligent_router import (
    GLMModel,
    IntelligentRouter,
    RoutingDecision,
    RoutingStats,
    TaskComplexity,
    TaskType,
)


class TestTaskType(unittest.TestCase):
    def test_code_generation(self):
        self.assertEqual(TaskType.from_query("写一个函数"), TaskType.CODE_GENERATION)

    def test_code_generation_en(self):
        self.assertEqual(TaskType.from_query("implement a function"), TaskType.CODE_GENERATION)

    def test_code_analysis(self):
        self.assertEqual(TaskType.from_query("analyze code review"), TaskType.CODE_ANALYSIS)

    def test_debugging(self):
        self.assertEqual(TaskType.from_query("修复这个bug"), TaskType.DEBUGGING)

    def test_search(self):
        self.assertEqual(TaskType.from_query("grep all files"), TaskType.SEARCH)

    def test_optimization(self):
        self.assertEqual(TaskType.from_query("优化性能"), TaskType.OPTIMIZATION)

    def test_testing(self):
        self.assertEqual(TaskType.from_query("运行测试验证"), TaskType.TESTING)

    def test_documentation(self):
        self.assertEqual(TaskType.from_query("add documentation"), TaskType.DOCUMENTATION)

    def test_refactoring(self):
        self.assertEqual(TaskType.from_query("refactor this class"), TaskType.CODE_REFACTORING)

    def test_analysis(self):
        self.assertEqual(TaskType.from_query("分析这个数据"), TaskType.ANALYSIS)

    def test_other_fallback(self):
        self.assertEqual(TaskType.from_query("你好"), TaskType.OTHER)


class TestTaskComplexity(unittest.TestCase):
    def test_weights(self):
        self.assertAlmostEqual(TaskComplexity.SIMPLE.get_weight(), 1.0)
        self.assertAlmostEqual(TaskComplexity.MEDIUM.get_weight(), 1.5)
        self.assertAlmostEqual(TaskComplexity.COMPLEX.get_weight(), 2.0)


class TestGLMModel(unittest.TestCase):
    def test_cost_multipliers(self):
        self.assertAlmostEqual(GLMModel.GLM_4_7.get_cost_multiplier(), 1.0)
        self.assertAlmostEqual(GLMModel.GLM_5_1.get_cost_multiplier(), 2.0)
        self.assertAlmostEqual(GLMModel.GLM_5.get_cost_multiplier(), 3.0)


class TestRoutingStats(unittest.TestCase):
    def test_glm47_ratio_zero(self):
        s = RoutingStats()
        self.assertAlmostEqual(s.get_glm_4_7_ratio(), 0.0)

    def test_glm47_ratio(self):
        s = RoutingStats(total_routed=10, glm_4_7_count=8)
        self.assertAlmostEqual(s.get_glm_4_7_ratio(), 0.8)


class TestIntelligentRouter(unittest.TestCase):
    def _make(self, tmp: Path) -> IntelligentRouter:
        return IntelligentRouter(stats_path=tmp / "stats.json")

    def test_simple_query_routes_to_glm47(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._make(Path(td))
            d = r.route("hello")
            self.assertEqual(d.model, GLMModel.GLM_4_7)
            self.assertEqual(d.complexity, TaskComplexity.SIMPLE)

    def test_complex_query_routes_to_glm51(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._make(Path(td))
            d = r.route(
                "设计一个分布式缓存系统架构，包括并发控制、数据结构优化和性能优化方案"
                * 5,
                context={"files": list(range(15))},
            )
            self.assertEqual(d.model, GLMModel.GLM_5_1)
            self.assertEqual(d.complexity, TaskComplexity.COMPLEX)

    def test_route_updates_stats(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._make(Path(td))
            r.route("hello")
            r.route("fix bug")
            stats = r.get_stats()
            self.assertEqual(stats.total_routed, 2)

    def test_stats_persist(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "stats.json"
            r1 = IntelligentRouter(stats_path=p)
            r1.route("hello")
            r2 = IntelligentRouter(stats_path=p)
            self.assertEqual(r2.get_stats().total_routed, 1)

    def test_reset_stats(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._make(Path(td))
            r.route("hello")
            r.reset_stats()
            self.assertEqual(r.get_stats().total_routed, 0)

    def test_decision_fields(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._make(Path(td))
            d = r.route("写一个函数")
            self.assertIsInstance(d, RoutingDecision)
            self.assertIsInstance(d.model, GLMModel)
            self.assertIsInstance(d.complexity, TaskComplexity)
            self.assertIsInstance(d.task_type, TaskType)
            self.assertIsInstance(d.reason, str)
            self.assertGreater(d.confidence, 0)
            self.assertLessEqual(d.confidence, 1.0)

    def test_evaluate_complexity_simple(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._make(Path(td))
            c = r._evaluate_complexity("hi", {})
            self.assertEqual(c, TaskComplexity.SIMPLE)

    def test_evaluate_complexity_medium(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._make(Path(td))
            c = r._evaluate_complexity("帮我写一个函数和类模块配置调试", {})
            self.assertEqual(c, TaskComplexity.MEDIUM)

    def test_evaluate_complexity_large_files(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._make(Path(td))
            c = r._evaluate_complexity(
                "architecture distributed concurrent",
                {"files": list(range(12))},
            )
            self.assertIn(c, (TaskComplexity.COMPLEX, TaskComplexity.MEDIUM))

    def test_load_corrupt_stats(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "stats.json"
            p.write_text("{bad json")
            r = IntelligentRouter(stats_path=p)
            self.assertEqual(r.get_stats().total_routed, 0)


if __name__ == "__main__":
    unittest.main()
