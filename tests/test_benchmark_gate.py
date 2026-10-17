"""S0-1 (2026-10-17) 量具接线冻结防回归测试。

历史沿革：
- 2026-09-17 版：断言 adopt_params「必须」改变分数（打通 参数→判定阈值
  耦合通道，修门禁恒等分死门）。
- 2026-10-17 起【RSI round1 阶段〇止血】：该耦合被定性为 Goodhart 自调
  量具回路（学生自改评分标准，2352→2351 空转实证），接线已冻结（一律
  no-op）。本测试反转断言：**无论传什么参数，基准分数必须纹丝不动**。
"""
from lingclaude.self_optimizer.benchmark import BenchmarkEvaluator


def _make_evaluator() -> BenchmarkEvaluator:
    return BenchmarkEvaluator(target_path="tests")  # 小目录, 快


def test_adopt_params_frozen_noop_strict() -> None:
    """收紧到最苛刻：分数不得有任何变化（尺子不许被搬动）。"""
    ev = _make_evaluator()
    base = ev.run()
    ev.adopt_params({"max_class_size": 1, "max_method_count": 1,
                     "max_complexity": 1, "coupling_limit": 0.0})
    assert ev.run().score == base.score, (
        "S0-1 冻结失效：adopt_params 改变了分数（Goodhart 回路复活）"
    )


def test_adopt_params_frozen_noop_loose() -> None:
    """放宽到最宽松：分数同样不得变化（放宽换分 = 自调量具，禁止）。"""
    ev = _make_evaluator()
    base = ev.run()
    ev.adopt_params({"max_class_size": 100000, "max_method_count": 100000,
                     "max_complexity": 100, "coupling_limit": 1.0})
    assert ev.run().score == base.score, (
        "S0-1 冻结失效：放宽参数改变了分数（评分标准被搬动）"
    )


def test_adopt_params_no_module_constant_pollution() -> None:
    """浅拷贝隐患回归: adopt_params 不得污染 _BUILTIN_CHECKS（冻结后天然满足）。"""
    from lingclaude.self_optimizer.benchmark import _BUILTIN_CHECKS

    ev = _make_evaluator()
    ev.adopt_params({"max_class_size": 1})
    assert _BUILTIN_CHECKS[0]["check"] == "max_class_lines<=600"


def test_adopt_params_unknown_key_ignored() -> None:
    ev = _make_evaluator()
    base = ev.run()
    ev.adopt_params({"not_a_param": 123})
    assert ev.run().score == base.score


def test_param_to_check_mapping_is_empty() -> None:
    """量具接线点必须保持空映射——非空即有人重启了自调量具回路。"""
    assert BenchmarkEvaluator._PARAM_TO_CHECK == {}, (
        "S0-1 违规：_PARAM_TO_CHECK 非空（自调量具接线被恢复，须人工评审）"
    )
