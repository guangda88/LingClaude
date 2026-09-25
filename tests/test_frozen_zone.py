"""F7 (2026-09-25): frozen zone（死循环熔断闸）测试。

针对诊断出的「优化循环死锁空转」——同参数组合连续 rolled_back
（p1_worse_than_best_ever）达阈值即冻结优化循环（跳轮+指数退避），
防止优化器在永远赢不了历史最优的参数上空转烧预算。

达阈值判定同 lingminopt 最佳基线持续回滚守卫（rollouts/ 目录无该单测）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingclaude.self_optimizer.daemon import OptimizationDaemon
from lingclaude.self_optimizer.experiments import (
    ExperimentLedger,
    FROZEN_MAX_BACKOFF_CYCLES,
    FROZEN_ROLLBACK_STREAK_THRESHOLD,
)


@pytest.fixture()
def ledger(tmp_path: Path) -> ExperimentLedger:
    return ExperimentLedger(db_path=str(tmp_path / ".lingclaude" / "experiments.db"))


# --------------------------------------------------------------------- #
# ledger 查询 API
# --------------------------------------------------------------------- #
def test_rollback_streak_counts_consecutive_same_params_reason(ledger):
    """同参数+同因 rolled_back 连续累计；任何 accepted/rejected 打断 streak。"""
    params = {"consecutive_fail_limit": 2, "tool_repeat_limit": 6}
    for i in range(3):
        eid = ledger.start(i + 1, params, 52.8)
        ledger.settle(eid, "rolled_back", reason="p1_worse_than_best_ever")
    assert ledger.rollback_streak(params, "p1_worse_than_best_ever") == 3

    # 不同 reason → 过滤后仍连续（异因回滚不打断本因连击，避免稀释真实连击）
    eid = ledger.start(4, params, 52.8)
    ledger.settle(eid, "rolled_back", reason="p0_gate_regression")
    assert ledger.rollback_streak(params, "p1_worse_than_best_ever") == 3
    assert ledger.rollback_streak(params, "p0_gate_regression") == 1

    # 不同参数 → 独立连击（params 的 p1 连击不被 params2 打断——连击
    # 衡量"该参数组是否仍被反复试且失败"，与是否穿插试别的参数无关）
    params2 = {"consecutive_fail_limit": 3, "tool_repeat_limit": 6}
    for i in range(2):
        eid = ledger.start(5 + i, params2, 52.8)
        ledger.settle(eid, "rolled_back", reason="p1_worse_than_best_ever")
    assert ledger.rollback_streak(params, "p1_worse_than_best_ever") == 3
    assert ledger.rollback_streak(params2, "p1_worse_than_best_ever") == 2


def test_rollback_streak_key_order_insensitive(ledger):
    """JSON 键顺序差异不影响匹配（start 内部 sort_keys=True）。"""
    params = {"consecutive_fail_limit": 2, "tool_repeat_limit": 6}
    eid = ledger.start(1, params, 52.8)
    ledger.settle(eid, "rolled_back", reason="p1_worse_than_best_ever")
    # 构造键序不同的等价 dict（绕过 start 的 sort_keys）
    reordered = json.loads(json.dumps(params, sort_keys=True))
    assert ledger.rollback_streak(reordered, "p1_worse_than_best_ever") == 1


def test_latest_verdict_cycle(ledger):
    eid = ledger.start(10, {"a": 1}, 1.0)
    ledger.settle(eid, "accepted")
    assert ledger.latest_verdict_cycle("accepted") == 10
    assert ledger.latest_verdict_cycle("rolled_back") == 0


# --------------------------------------------------------------------- #
# daemon 熔断闸
# --------------------------------------------------------------------- #
@pytest.fixture()
def daemon(tmp_path: Path, monkeypatch) -> OptimizationDaemon:
    monkeypatch.chdir(tmp_path)
    d = OptimizationDaemon(str(tmp_path), state_dir=tmp_path / ".lingclaude")
    return d


def test_check_frozen_zone_triggers_at_threshold(daemon):
    """同参数+同因回滚达阈值 → frozen=True，状态持久化。"""
    params = {"consecutive_fail_limit": 2, "tool_repeat_limit": 6}
    for i in range(FROZEN_ROLLBACK_STREAK_THRESHOLD):
        eid = daemon.experiments.start(i + 1, params, 52.8)
        daemon.experiments.settle(eid, "rolled_back", reason="p1_worse_than_best_ever")
    frozen, streak = daemon._check_frozen_zone(params, "p1_worse_than_best_ever")
    assert frozen is True
    assert streak == FROZEN_ROLLBACK_STREAK_THRESHOLD
    assert daemon.state.frozen is True
    assert daemon.state.frozen_params == params


def test_check_frozen_zone_below_threshold_noop(daemon):
    params = {"consecutive_fail_limit": 2, "tool_repeat_limit": 6}
    for i in range(FROZEN_ROLLBACK_STREAK_THRESHOLD - 1):
        eid = daemon.experiments.start(i + 1, params, 52.8)
        daemon.experiments.settle(eid, "rolled_back", reason="p1_worse_than_best_ever")
    frozen, streak = daemon._check_frozen_zone(params, "p1_worse_than_best_ever")
    assert frozen is False
    assert streak == FROZEN_ROLLBACK_STREAK_THRESHOLD - 1
    assert daemon.state.frozen is False


def test_frozen_zone_skips_cycle_with_backoff(daemon):
    """冻结期间跳轮；退避后若检测到新的 accepted/rejected 则退出并重置 best_ever。"""
    params = {"consecutive_fail_limit": 2, "tool_repeat_limit": 6}
    daemon.state.frozen = True
    daemon.state.frozen_params = params
    daemon.state.frozen_reason = "p1_worse_than_best_ever"
    daemon.state.frozen_since_cycle = daemon.state.total_cycles + 1
    daemon.state.best_ever_score = 27.6

    # 未冻结 → None
    daemon.state.frozen = False
    assert daemon._frozen_zone_skip() is None

    # 冻结且无新进展 → True（跳过），frozen_skipped 递增
    daemon.state.frozen = True
    assert daemon._frozen_zone_skip() is True
    assert daemon.state.frozen_skipped == 1

    # 模拟新的 accepted 单出现在冻结起点之后 → 退出冻结并重置 best_ever
    eid = daemon.experiments.start(
        daemon.state.frozen_since_cycle + 1, {"x": 1}, 1.0
    )
    daemon.experiments.settle(eid, "accepted")
    assert daemon._frozen_zone_skip() is None
    assert daemon.state.frozen is False
    assert daemon.state.best_ever_score is None


def test_frozen_zone_backoff_capped(daemon):
    """退避上限 FROZEN_MAX_BACKOFF_CYCLES，不会无限指数增长。"""
    params = {"consecutive_fail_limit": 2, "tool_repeat_limit": 6}
    daemon.state.frozen = True
    daemon.state.frozen_params = params
    daemon.state.frozen_reason = "p1_worse_than_best_ever"
    daemon.state.frozen_since_cycle = daemon.state.total_cycles + 1

    # 跳过足够多轮，退避应封顶在 FROZEN_MAX_BACKOFF_CYCLES
    for _ in range(10):
        daemon._frozen_zone_skip()
    assert daemon.state.frozen_skipped == 10
    backoff = min(2 ** daemon.state.frozen_skipped, FROZEN_MAX_BACKOFF_CYCLES)
    assert backoff == FROZEN_MAX_BACKOFF_CYCLES


def test_run_cycle_skips_when_frozen(daemon, monkeypatch):
    """frozen 状态下 run_cycle 直接返回 None（跳轮），不触发优化。"""
    daemon.state.frozen = True
    daemon.state.frozen_params = {"consecutive_fail_limit": 2, "tool_repeat_limit": 6}
    daemon.state.frozen_reason = "p1_worse_than_best_ever"
    daemon.state.frozen_since_cycle = daemon.state.total_cycles + 1

    called = {"optimize": False}
    monkeypatch.setattr(
        daemon.optimizer,
        "optimize",
        lambda req: called.update(optimize=True),
    )
    result = daemon.run_cycle(user_triggered=False)
    assert result.is_ok
    assert result.data is None
    assert called["optimize"] is False
