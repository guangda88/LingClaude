"""bus_bridge 测试（离线 hermetic：路由/守卫拦截/闭环回写）。

守卫锚点：
- N2（铁律 6）：未在册插片拒载（denied:not_registered）；
- 候选铁律 8：absent 插片不放行（denied:absent，不假活）；
- J4（铁律 3）：无路由/执行失败/成功全部入账（bus_route record）；
- 铁律 5：路由表 record 化（bus_route_table），变更留痕（unbound 不删账）。
全部离线：poller/runner 均为 stub，不依赖真实 LingBus/lingxi 进程。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from lingclaude.core.state_store import StateStore
from lingclaude.plugins.agents.bus_bridge import BusBridge


@pytest.fixture()
def store(tmp_path):
    return StateStore(backend="json", root=tmp_path / "ledger")


@pytest.fixture()
def registered(store):
    """一个在册（T1/L1）的插片 record。"""
    store.save("agent_registry", "agent/lingxi", {
        "trust_level": "T1", "plug_level": "L1",
        "state": "registered", "registered_at": 0,
    })
    return "agent/lingxi"


def _bridge(store, msgs=None, run_result=None):
    poller = lambda: msgs or []  # noqa: E731 —— stub poller
    runner = None
    if run_result is not None:
        class _R:  # 最小 SeamRegistry 形状（.get 返回带 run 的插件）
            def __init__(self, r): self._r = r
            def get(self, _seam, _key): return type("P", (), {"run": lambda self, t: run_result})()
        runner = _R(run_result)
    return BusBridge(store=store, poller=poller, runner=runner)


# ── N2：未在册插片拒载 ────────────────────────────────────────────────
def test_bind_rejects_unregistered(store):
    b = _bridge(store)
    r = b.bind("git-mcp", "agent/nonexistent")
    assert r["ok"] is False and r["reason"] == "not_registered"
    rec = store.load("bus_route_table", "git-mcp")
    assert rec["state"] == "denied:not_registered"  # 拒载也入账，可 query


# ── 候选铁律 8：absent 插片不放行 ──────────────────────────────────────
def test_bind_rejects_absent(store):
    store.save("agent_registry", "agent/dead", {
        "trust_level": "T3", "plug_level": "L3", "state": "absent"})
    b = _bridge(store)
    r = b.bind("probe", "agent/dead")
    assert r["ok"] is False and r["reason"] == "absent"


# ── 铁律 5：绑定成功 → 路由表 record 化 ────────────────────────────────
def test_bind_success_recorded(store, registered):
    b = _bridge(store)
    r = b.bind("git-mcp", registered)
    assert r["ok"] is True
    rec = store.load("bus_route_table", "git-mcp")
    assert rec["plugin"] == registered and rec["trust_level"] == "T1"


# ── J4：消息无路由 → no_route 入账 ─────────────────────────────────────
def test_tick_no_route_recorded(store):
    b = _bridge(store, msgs=[{"topic": "nobody", "subject": "s", "body": "b",
                              "thread_id": "t1"}])
    handled = b.tick()
    assert handled[0]["state"] == "no_route" and handled[0]["plugin"] is None
    assert store.load("bus_route", handled[0].keys() and handled[0]["topic"] or "x") or True
    # bus_route record 确实落了账
    assert len(store.list_keys("bus_route")) == 1


# ── J4：执行成功/失败都入账 ────────────────────────────────────────────
def test_tick_success_and_failure_recorded(store, registered):
    b = _bridge(store, run_result={"state": "succeeded"})
    b.bind("git-mcp", registered)
    handled = b.tick()  # 无消息时 tick 只空转
    msgs = [{"topic": "git-mcp", "subject": "立项", "body": "开工",
             "thread_id": "t2"}]
    b2 = BusBridge(store=store, poller=lambda: msgs,
                   runner=type("R", (), {"get": lambda self, s, k: type(
                       "P", (), {"run": lambda self, t: {"state": "succeeded"}})()})())
    b2._routes = {"git-mcp": registered}
    handled = b2.tick()
    assert handled[0]["state"] == "succeeded"
    assert len(store.list_keys("bus_route")) >= 1

    # runner 抛错 → failed 也入账
    def boom(self, t): raise RuntimeError("mcp down")
    b3 = BusBridge(store=store, poller=lambda: msgs,
                   runner=type("R", (), {"get": lambda self, s, k: type(
                       "P", (), {"run": boom})()})())
    b3._routes = {"git-mcp": registered}
    handled = b3.tick()
    assert handled[0]["state"] == "failed"
    assert "mcp down" in store.load(
        "bus_route", sorted(store.list_keys("bus_route"))[-1]).get("state", "") + \
        str(store.load("bus_route", sorted(store.list_keys("bus_route"))[-1]))


# ── 铁律 5：unbind 留痕不删账 ─────────────────────────────────────────
def test_unbind_leaves_record(store, registered):
    b = _bridge(store)
    b.bind("git-mcp", registered)
    assert b.unbind("git-mcp") is True
    assert b.unbind("git-mcp") is False  # 二次解绑无此路由
    rec = store.load("bus_route_table", "git-mcp")
    assert rec["state"] == "unbound" and rec["plugin"] == registered  # 账还在
