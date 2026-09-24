"""插片风暴熔断闸测试（建闸期 ①，synthesis-20260925 评审合议 #2）。

覆盖：三层独立熔断 / half-open 单探测纪律 / 探测失败重开 /
hot_swap 修复通道 / 白名单 / LifecycleManager 集成（refresh 准入）。
"""
from __future__ import annotations

import time

import pytest

from lingclaude.core.plugin_lifecycle import (
    BreakerLayer,
    CircuitBreaker,
    LifecycleManager,
    LifecycleState,
)
from lingclaude.core.seam import SeamRegistry, SeamType


class _Thing:
    pass


# ---------------- 纯闸单元 ----------------

def test_fiber_threshold_trips_and_denies():
    b = CircuitBreaker(cooldown_seconds=60.0)
    for _ in range(3):
        b.record_failure("gov/x", error="boom")
    ok, denial = b.allow("gov/x")
    assert ok is False
    assert denial["layer"] == "fiber" and denial["state"] == "open"
    assert denial["cooldown_remaining"] > 0


def test_below_threshold_allows():
    b = CircuitBreaker(cooldown_seconds=60.0)
    b.record_failure("gov/x")
    b.record_failure("gov/x")
    assert b.allow("gov/x") == (True, None)


def test_sliding_window_old_failures_not_counted():
    """滑动窗口语义：窗口管「何时跳闸」，冷却管「何时恢复」。

    窗口外的旧失败不计入新的跳闸判定（但已跳的闸仍走冷却→探测恢复）。
    """
    b = CircuitBreaker(cooldown_seconds=60.0, window_seconds=0.05)
    b.record_failure("gov/x")
    b.record_failure("gov/x")
    time.sleep(0.06)  # 窗口滑走
    b.record_failure("gov/x")  # 窗口内只有 1 次 → 不跳闸
    assert b.allow("gov/x") == (True, None)
    snap = b.snapshot()
    assert snap["fibers"]["gov/x"]["failures"] == 1

    # 已跳的闸不受窗口影响：3 连败跳闸后窗口滑走，闸仍 OPEN（等冷却探测）
    b2 = CircuitBreaker(cooldown_seconds=60.0, window_seconds=0.05)
    for _ in range(3):
        b2.record_failure("gov/x")
    time.sleep(0.06)
    ok, denial = b2.allow("gov/x")
    assert ok is False and denial["state"] == "open"


def test_domain_layer_trips_independently():
    b = CircuitBreaker(cooldown_seconds=60.0)  # fiber 阈 3 / domain 阈 4
    b.record_failure("gov/x")
    b.record_failure("gov/y")
    b.record_failure("gov/z")
    assert b.allow("gov/w") == (True, None)  # 每个 fiber 各 1 次，未达任何阈值
    b.record_failure("gov/w")  # domain gov 计 4 → 域熔断；gov/w fiber 计 1
    ok, denial = b.allow("gov/w")
    assert ok is False and denial["layer"] == "domain"


def test_global_layer_aggregates_all_domains():
    b = CircuitBreaker(global_threshold=3, cooldown_seconds=60.0)
    b.record_failure("gov/x")
    b.record_failure("agent/y")
    b.record_failure("cap/z")
    ok, denial = b.allow("hw/q")
    assert ok is False and denial["layer"] == "global" and denial["target"] is None


def test_whitelist_bypasses_open_gate():
    b = CircuitBreaker(whitelist={"gov/core"}, cooldown_seconds=60.0)
    for _ in range(5):
        b.record_failure("gov/core")
    assert b.allow("gov/core") == (True, None)
    assert b.allow("gov/other")[0] is False  # 他人不豁免


def test_half_open_single_probe_per_gate():
    b = CircuitBreaker(cooldown_seconds=0.05)
    for _ in range(3):
        b.record_failure("gov/x")  # fiber gov/x + domain gov（4 不够，fiber 先开）
    time.sleep(0.06)  # 冷却期满
    ok1, _ = b.allow("gov/x")    # 探测者 1
    ok2, d2 = b.allow("gov/x")   # 同 fiber 二次请求 → probe_in_flight
    assert ok1 is True
    assert ok2 is False and d2["state"] == "half_open"
    assert d2["reason"] == "probe_in_flight" and d2["probe_holder"] == "gov/x"


def test_probe_failure_reopens_gate():
    events = []
    b = CircuitBreaker(cooldown_seconds=0.05, event_sink=events.append)
    for _ in range(3):
        b.record_failure("gov/x")
    time.sleep(0.06)
    assert b.allow("gov/x")[0] is True          # 探测放行
    b.record_failure("gov/x", error="probe boom")  # 探测失败
    ok, denial = b.allow("gov/x")
    assert ok is False and denial["state"] == "open"
    assert denial["cooldown_remaining"] > 0     # 冷却重新计时
    # 第 4 个事件：探测失败是真实失败 → 计入域窗口（gov 达域阈值 4）→ 域闸跳闸
    assert [e["event"] for e in events] == [
        "breaker_open", "probe_allowed", "probe_failed", "breaker_open"]
    assert events[-1]["layer"] == "domain" and events[-1]["target"] == "gov"


def test_probe_success_closes_gate():
    b = CircuitBreaker(cooldown_seconds=0.05)
    for _ in range(3):
        b.record_failure("gov/x")
    time.sleep(0.06)
    assert b.allow("gov/x")[0] is True
    b.record_success("gov/x")
    assert b.allow("gov/x") == (True, None)
    assert b.snapshot()["fibers"]["gov/x"]["open"] is False


def test_non_probe_success_cannot_close_others_gate():
    """单探测纪律：无关 fiber 的成功不能关闭别人触发的闸。"""
    b = CircuitBreaker(cooldown_seconds=0.05)
    for _ in range(4):
        b.record_failure("gov/x")  # domain gov 达阈值 4
    time.sleep(0.06)
    # gov/x 先探测（domain 闸）但探测在途
    assert b.allow("gov/x")[0] is True
    # 另一 fiber（无故障历史）成功——不得关闭 domain 闸
    b.record_success("gov/innocent")
    assert b.allow("gov/innocent")[0] is False  # domain 仍 OPEN（非探测者被拒）
    snap = b.snapshot()
    assert snap["domains"]["gov"]["open"] is True


def test_hot_swap_repair_channel_closes_own_fiber_gate():
    """修复通道：闸开着时换上修好的工厂直接激活成功 → 关自己的 fiber 闸。"""
    b = CircuitBreaker(cooldown_seconds=60.0)
    for _ in range(3):
        b.record_failure("gov/z")
    assert b.allow("gov/z")[0] is False
    b.record_success("gov/z")  # hot_swap 修复，激活成功
    assert b.allow("gov/z") == (True, None)
    assert b.snapshot()["fibers"]["gov/z"]["open"] is False


def test_layer_semantics_and_reset():
    b = CircuitBreaker()
    assert b.allow("plain") [0] is True          # 无前缀 → app 域
    assert b.allow("gov/x")[0] is True
    b.record_failure("gov/x")
    b.reset("gov/x")
    snap = b.snapshot()
    assert snap["fibers"].get("gov/x", {}).get("failures", 0) == 0
    b.reset()  # 全清不抛
    b2 = CircuitBreaker()
    assert BreakerLayer.FIBER.value == "fiber"


def test_event_sink_exceptions_never_break_breaker():
    def bad_sink(rec):
        raise RuntimeError("sink down")

    b = CircuitBreaker(event_sink=bad_sink)
    b.record_failure("gov/x")  # sink 抛异常必须被吞
    assert b.allow("gov/x") == (True, None)


# ---------------- LifecycleManager 集成 ----------------

def test_manager_refresh_denies_when_gate_open():
    """3 连败 → fiber 闸开 → refresh 拒绝激活，error=circuit_open。"""
    lcm = LifecycleManager(
        breaker=CircuitBreaker(fiber_threshold=3, cooldown_seconds=60.0))

    def bad_factory():
        raise RuntimeError("perma-boom")

    lcm.attach("gov/x", bad_factory, inject=[(SeamType.PROVIDER, "glm")])
    SeamRegistry.register(SeamType.PROVIDER, "glm", _Thing())
    for _ in range(3):
        lcm.refresh("gov/x")
        assert lcm.status()["gov/x"]["state"] == "failed"
    # 第 4 次：闸已开 → INACTIVE + circuit_open（不再是 failed）
    st = lcm.refresh("gov/x")
    assert st is LifecycleState.INACTIVE
    assert "circuit_open" in lcm.status()["gov/x"]["error"]


def test_manager_keeps_serving_instance_on_gate_denial():
    """闸拒绝时不得卸载在服实例（蓝绿纪律：闸只挡激活，不拆现役）。"""
    lcm = LifecycleManager(
        breaker=CircuitBreaker(fiber_threshold=2, cooldown_seconds=60.0))
    lcm.ensure_subscribed()
    lcm.attach("gov/x", lambda: "good", inject=[(SeamType.PROVIDER, "glm")])
    SeamRegistry.register(SeamType.PROVIDER, "glm", _Thing())
    assert lcm.get("gov/x") == "good"

    # 人工塞 2 次失败（模拟闸已开，实例仍在服）
    lcm.breaker.record_failure("gov/x")
    lcm.breaker.record_failure("gov/x")
    lcm.refresh("gov/x")
    assert lcm.get("gov/x") == "good"  # 旧代续服
    assert lcm.status()["gov/x"]["state"] == "active"


def test_manager_status_exposes_breaker_snapshot():
    lcm = LifecycleManager()
    snap = lcm.status()["_breaker"]
    assert set(snap.keys()) >= {"global", "domains", "fibers", "whitelist"}


def test_default_manager_breaker_active():
    """默认构造即带闸（评审定案：不能靠记得开）。"""
    lcm = LifecycleManager()
    assert isinstance(lcm.breaker, CircuitBreaker)
    assert lcm.breaker._fiber_threshold >= 2
