"""Regression tests: P0 实证门禁 参数→分数 耦合通道（2026-09-17）。

原缺陷：阈值硬编码 + adopt 通道缺失 → benchmark 分数与 best_params
零耦合，daemon 门禁恒等分死门（atomcode B2 / opencode #9）。
"""
from lingclaude.self_optimizer.benchmark import BenchmarkEvaluator


def _make_evaluator() -> BenchmarkEvaluator:
    return BenchmarkEvaluator(target_path="tests")  # 小目录, 快


def test_adopt_params_changes_score() -> None:
    ev = _make_evaluator()
    base = ev.run()
    ev.adopt_params({"max_class_size": 1, "max_method_count": 1,
                     "max_complexity": 1, "coupling_limit": 0.0})
    after = ev.run()
    assert after.score < base.score, "劣化参数必须拉低分数（门禁活性的前提）"


def test_adopt_params_loose_not_lower() -> None:
    ev = _make_evaluator()
    base = ev.run()
    ev.adopt_params({"max_class_size": 100000, "max_method_count": 100000,
                     "max_complexity": 100, "coupling_limit": 1.0})
    after = ev.run()
    assert after.score >= base.score, "放宽阈值不应降分"


def test_adopt_params_no_module_constant_pollution() -> None:
    """浅拷贝隐患回归: adopt_params 不得污染 _BUILTIN_CHECKS。"""
    from lingclaude.self_optimizer.benchmark import _BUILTIN_CHECKS

    ev = _make_evaluator()
    ev.adopt_params({"max_class_size": 1})
    assert _BUILTIN_CHECKS[0]["check"] == "max_class_lines<=600"


def test_adopt_params_unknown_key_ignored() -> None:
    ev = _make_evaluator()
    base = ev.run()
    ev.adopt_params({"not_a_param": 123})
    assert ev.run().score == base.score
