# tests/test_plan_c_v4.py
"""方案C v4 插片测试：seam 订阅 / plugin_lifecycle / quota_governance /
context_engine / repair_card / hooks 扩容。

全部测试不依赖网络与外部服务（E7 门禁：可重复、可离线）。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# 稳健导入路径：显式把项目根目录加入 sys.path。
# 防「直接 python tests/test_plan_c_v4.py」或换 pytest 根目录推断时挂掉。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from lingclaude.core.seam import SeamRegistry, SeamType
from lingclaude.core.plugin_lifecycle import (
    LifecycleManager,
    LifecycleState,
    _reset_lifecycle_manager,
)
from lingclaude.model.quota_governance import (
    QuotaWindow,
    QuotaWindowPool,
    extract_window_from_error,
    _reset_quota_pool,
)
from lingclaude.core.context_engine import (
    DefaultContextEngine,
    extract_summary_framing,
    get_context_engine,
    make_framing,
    set_context_engine,
    _reset_context_engine,
)
from lingclaude.core.repair_card import (
    RepairBoard,
    RepairEvidenceError,
    RepairState,
    RepairTransitionError,
    _reset_repair_board,
)
from lingclaude.core.hooks import HookContext, HookManager, HookResult, HookType


@pytest.fixture(autouse=True)
def _clean_singletons():
    """每测清空全部模块级单例与注册表（测试隔离）。"""
    SeamRegistry.reset()
    _reset_lifecycle_manager()
    _reset_quota_pool()
    _reset_context_engine()
    _reset_repair_board()
    yield
    SeamRegistry.reset()
    _reset_lifecycle_manager()
    _reset_quota_pool()
    _reset_context_engine()
    _reset_repair_board()


class _Thing:
    pass


# ---------------- seam 订阅钩子 ----------------

def test_seam_subscribe_register_and_unregister_events():
    events = []
    SeamRegistry.subscribe_change(lambda a, t, n: events.append((a, t.value, n)))
    SeamRegistry.register(SeamType.AGENT, "x1", _Thing())
    assert events == [("register", "agent", "x1")]
    SeamRegistry.unregister(SeamType.AGENT, "x1")
    assert events[-1] == ("unregister", "agent", "x1")


def test_seam_noop_unregister_does_not_broadcast():
    events = []
    cb = lambda a, t, n: events.append(a)  # noqa: E731
    SeamRegistry.subscribe_change(cb)
    assert SeamRegistry.unregister(SeamType.AGENT, "ghost") is False
    assert events == []
    SeamRegistry.unsubscribe_change(cb)


def test_seam_subscriber_exception_does_not_break_register():
    def boom(action, seam_type, name):
        raise RuntimeError("subscriber boom")

    events = []
    SeamRegistry.subscribe_change(boom)
    SeamRegistry.subscribe_change(lambda a, t, n: events.append(a))
    SeamRegistry.register(SeamType.TOOL, "t1", _Thing())  # boom 被吞、其余照播
    assert events == ["register"]


def test_seam_reset_clears_subscribers():
    events = []
    SeamRegistry.subscribe_change(lambda a, t, n: events.append(a))
    SeamRegistry.reset()
    SeamRegistry.register(SeamType.TOOL, "t2", _Thing())
    assert events == []


# ---------------- plugin_lifecycle ----------------

def test_lifecycle_pending_to_active_via_seam_event():
    made = []
    lcm = LifecycleManager()
    lcm.ensure_subscribed()
    lcm.attach("p", lambda: made.append(1) or "inst",
               inject=[(SeamType.PROVIDER, "glm")])
    assert lcm.status()["p"]["state"] == "pending"
    SeamRegistry.register(SeamType.PROVIDER, "glm", _Thing())  # 事件驱动激活
    assert lcm.status()["p"]["state"] == "active"
    assert lcm.get("p") == "inst" and len(made) == 1


def test_lifecycle_epoch_noop_on_unchanged_deps():
    calls = []
    lcm = LifecycleManager()
    lcm.ensure_subscribed()
    lcm.attach("p", lambda: calls.append(1) or "inst",
               inject=[(SeamType.PROVIDER, "glm")])
    SeamRegistry.register(SeamType.PROVIDER, "glm", _Thing())
    before = len(calls)
    lcm.refresh("p")  # 无变化 → no-op
    assert len(calls) == before and lcm.status()["p"]["state"] == "active"


def test_lifecycle_dependency_loss_unloads_and_returns():
    disposed = []
    lcm = LifecycleManager()
    lcm.ensure_subscribed()
    lcm.attach("p", lambda: ("inst", lambda: disposed.append(1)),
               inject=[(SeamType.PROVIDER, "glm")])
    SeamRegistry.register(SeamType.PROVIDER, "glm", _Thing())
    assert lcm.status()["p"]["state"] == "active"
    SeamRegistry.unregister(SeamType.PROVIDER, "glm")  # 依赖消失 → 自动卸载
    assert lcm.status()["p"]["state"] == "inactive" and disposed == [1]
    SeamRegistry.register(SeamType.PROVIDER, "glm", _Thing())  # 回归 → 再激活
    assert lcm.status()["p"]["state"] == "active"


def test_hot_swap_success_bypasses_epoch_guard():
    lcm = LifecycleManager()
    lcm.ensure_subscribed()
    lcm.attach("p", lambda: "v1", inject=[(SeamType.PROVIDER, "glm")])
    SeamRegistry.register(SeamType.PROVIDER, "glm", _Thing())
    assert lcm.get("p") == "v1"
    assert lcm.hot_swap("p", lambda: "v2") is True
    assert lcm.get("p") == "v2"  # epoch 未变也必须切换成功


def test_hot_swap_rollback_on_activate_failure():
    lcm = LifecycleManager()
    lcm.ensure_subscribed()
    lcm.attach("p", lambda: "v1", inject=[(SeamType.PROVIDER, "glm")])
    SeamRegistry.register(SeamType.PROVIDER, "glm", _Thing())

    def bad_factory():
        raise RuntimeError("second call boom")

    assert lcm.hot_swap("p", bad_factory) is False
    assert lcm.get("p") == "v1" and lcm.status()["p"]["state"] == "active"


# ---------------- quota_governance ----------------

def test_extract_window_from_real_glm_1308_error():
    err = ("GLM 错误: 已达到 5 小时的使用上限 (code 1308)，"
           "重置时间 2026-09-21 23:59:00 后恢复")
    w = extract_window_from_error("glm", err)
    assert w is not None and w.kind == "5h"
    assert w.reset_at == datetime(2026, 9, 21, 23, 59, 0)


def test_extract_window_fallback_and_non_quota():
    w = extract_window_from_error("glm", "quota exceeded")
    assert w is not None  # 无时间戳 → 兜底窗口
    assert extract_window_from_error("glm", "connection timeout") is None
    assert extract_window_from_error("glm", "") is None


def test_pool_decide_defer_allow_stale_clear():
    pool = QuotaWindowPool()
    assert pool.decide_from_windows("glm")["action"] == "allow"
    pool.record(QuotaWindow(provider="glm",
                            reset_at=datetime.now() + timedelta(hours=2)))
    assert pool.decide_from_windows("glm")["action"] == "defer"
    pool.record(QuotaWindow(provider="glm",
                            reset_at=datetime.now() - timedelta(seconds=30)))
    d = pool.decide_from_windows("glm")  # 宽限期内 → allow + stale
    assert d["action"] == "allow" and d["stale"] is True
    assert pool.clear("glm") == 1
    assert pool.decide_from_windows("glm")["action"] == "allow"


def test_pool_record_from_error_noop_on_plain_error():
    pool = QuotaWindowPool()
    assert pool.record_from_error("glm", "timeout") is None
    assert pool.snapshot() == {}


# ---------------- context_engine ----------------

def test_framing_roundtrip():
    f = make_framing(21, "- 决策：单源解析\n- 证据：cbff31f")
    text = f.render()
    assert text.startswith("## 压缩摘要（前 21 轮对话）")
    assert text.endswith("<!-- summary-end -->")
    f2 = extract_summary_framing(text)
    assert f2 is not None and f2.dropped_count == 21 and "单源解析" in f2.body


def test_is_summary_message_str_and_dict():
    eng = DefaultContextEngine()
    text = make_framing(3, "body").render()
    assert eng.is_summary_message(text) is True
    assert eng.is_summary_message({"role": "user", "content": text}) is True
    assert eng.is_summary_message("普通消息") is False


def test_never_slice_semantics():
    eng = DefaultContextEngine()
    text = make_framing(3, "body").render()
    assert eng.retain_whole(text, 10_000) is True
    assert eng.retain_whole(text, 5) is False  # 整条丢弃，绝不切半


def test_engine_injection_rejected_none():
    class MyEngine(DefaultContextEngine):
        pass

    set_context_engine(MyEngine())
    assert isinstance(get_context_engine(), MyEngine)
    with pytest.raises(ValueError):
        set_context_engine(None)


# ---------------- repair_card ----------------

def test_repair_card_fail_closed_resolve_requires_evidence():
    b = RepairBoard()
    c = b.open_card("f.py", "broken")
    with pytest.raises(RepairEvidenceError):
        b.resolve(c.card_id, "", None)
    with pytest.raises(RepairEvidenceError):
        b.resolve(c.card_id, "cmd", None)
    r = b.resolve(c.card_id, "pytest -q", "1 passed")
    assert r.state is RepairState.RESOLVED
    assert b.blocks("f.py") is False


def test_repair_card_blocks_until_resolved():
    b = RepairBoard()
    c = b.open_card("g.py", "broken")
    assert b.blocks("g.py") is True
    b.fail(c.card_id, "unfixable")
    assert b.blocks("g.py") is False  # FAILED 不阻塞（已升级人工）


def test_repair_card_max_attempts_terminal():
    b = RepairBoard(max_attempts=1)
    c = b.open_card("h.py", "broken")
    b.start_repair(c.card_id)  # attempt#1，正常进入 REPAIRING
    b.start_repair(c.card_id)  # attempt#2 超限 → 自动转入 FAILED（升级人工）
    assert b.get(c.card_id).state is RepairState.FAILED
    with pytest.raises(RepairTransitionError):
        b.start_repair(c.card_id)  # FAILED 是终态，不可再修


# ---------------- hooks 扩容 ----------------

def test_hooks_session_resume_type_and_context_fields():
    m = HookManager()
    seen = []
    m.register("note", HookType.SESSION_RESUME,
               lambda ctx: seen.append(ctx.resumed_from_snapshot) or None)
    ctx = HookContext(hook_type=HookType.SESSION_RESUME, session_id="s",
                      resumed=True, resumed_from_snapshot="snap-1")
    result = m.trigger(ctx)
    assert seen == ["snap-1"] and result.error == ""


def test_hooks_hot_swap_types_registered():
    m = HookManager()
    m.register("pre", HookType.PRE_HOT_SWAP, lambda ctx: None)
    m.register("post", HookType.POST_HOT_SWAP, lambda ctx: None)
    assert m.has_hooks(HookType.PRE_HOT_SWAP)
    assert m.has_hooks(HookType.POST_HOT_SWAP)


def test_hooks_result_storage_hint_backward_compatible():
    h = HookResult(modified_context=None, storage_hint="PERMANENT")
    h2 = HookResult(modified_context=None)
    assert h.storage_hint == "PERMANENT" and h2.storage_hint == ""


def test_hooks_old_context_construction_unchanged():
    old = HookContext(hook_type=HookType.PRE_TASK, session_id="s2")
    assert old.resumed is False and old.resumed_from_snapshot == ""
