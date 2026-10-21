"""触发器口收敛测试（2026-10-22）。

背景：activity 消费方实证 watch 四天 1015/1020 轮走 structure 触发
（输入=代码库静态属性恒真）且完全绕过装在 _check_time 单点的 headroom
闸 → 纯空转。本批收敛：闸提升为 check_all_conditions 统一层，覆盖全部
自动触发；pass_headroom 语义从绝对总分改为尺度相对（1-score/best_ever），
否则回放代价类指标（真实尺度 ~775 > 总分 100）结构性恒 0，闸统一后会
把一切自动触发永久压死。
"""

from __future__ import annotations

import pytest

from lingclaude.self_optimizer.headroom import compute_headroom
from lingclaude.self_optimizer.trigger import OptimizationTrigger, TriggerConfig


def _trigger() -> OptimizationTrigger:
    return OptimizationTrigger(TriggerConfig())


def test_structure_alwaystrue_blocked_by_unified_gate():
    """structure 恒真触发 + headroom 无空间 → 统一层拦截（原：绕过闸触发）。"""
    hv = compute_headroom(
        benchmark_score=775.8, best_ever_score=775.8, rollback_rate_30d=1 / 90
    )
    assert hv.marginal_benefit_positive() is False
    assert _trigger().check_all_conditions(
        {"avg_complexity": 99, "large_classes_count": 48, "headroom": hv}
    ) == (False, None)


def test_user_triggered_bypasses_gate():
    """人工单跑明确要跑 → 不受闸（闸之前 user 分支已 return）。"""
    hv = compute_headroom(
        benchmark_score=775.8, best_ever_score=775.8, rollback_rate_30d=0.0
    )
    ok, info = _trigger().check_all_conditions(
        {"avg_complexity": 99, "user_triggered": True, "headroom": hv}
    )
    assert ok is True
    assert info is not None and info.type == "user"


def test_missing_vector_degrades_to_legacy():
    """向量缺失 → 不设闸（防首轮死锁，退化=旧行为）。"""
    ok, info = _trigger().check_all_conditions({"avg_complexity": 99})
    assert ok is True
    assert info is not None and info.type == "structure"


def test_pass_headroom_cost_scale_relative():
    """pass 分量代价型语义：劣化越多空间越大，追平/优于最优 → 0。"""
    # 当前比历史最优劣化 30% → 有空间（代价型：该花钱找回最优）
    hv = compute_headroom(benchmark_score=1300.0, best_ever_score=1000.0)
    assert hv.pass_headroom == pytest.approx(0.30)
    assert hv.marginal_benefit_positive() is True
    # 追平最优 → 0 空间
    hv2 = compute_headroom(benchmark_score=1000.0, best_ever_score=1000.0)
    assert hv2.pass_headroom == 0.0
    # 优于最优（尺度漂移前兆）→ 截 0，交 F6 卫兵处理
    hv3 = compute_headroom(benchmark_score=500.0, best_ever_score=1000.0)
    assert hv3.pass_headroom == 0.0


def test_pass_headroom_fallback_absolute():
    """best_ever 缺失 → 回退绝对口径 min(1, score/total)（违规占比）。"""
    hv = compute_headroom(benchmark_score=80.0)
    assert hv.pass_headroom == pytest.approx(0.80)


def test_real_scale_not_structurally_zero():
    """回归病根：真实尺度（~775>100）不再结构性恒 0；劣化被判有空间。"""
    # 旧行为：775.8/100 → pass 恒 0（闸统一后压死一切自动触发）
    drifted = compute_headroom(
        benchmark_score=1551.6, best_ever_score=775.8  # 劣化一倍
    )
    assert drifted.pass_headroom == pytest.approx(1.0)
    assert drifted.marginal_benefit_positive() is True


def test_time_check_gate_still_works():
    """_check_time 原有闸保留（双保险，语义不变）。"""
    hv = compute_headroom(
        benchmark_score=100.0, best_ever_score=100.0, rollback_rate_30d=0.0
    )
    info = _trigger()._check_time(
        {"headroom": hv, "last_optimization_time": None}
    )
    assert info is None
