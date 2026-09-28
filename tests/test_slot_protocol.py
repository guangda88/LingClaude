"""P1 红→绿验收：Slot / SlotHandle / SlotManager 统一 swap 协议（M6 行为测试）。

对应主方案 §四 swap 语义 + §六 M6 守卫：
  config 变 → 实例换 → 在途不炸 → record 落账

覆盖：
1. 先建后换：swap 后新 lease 解析到新实例（换手即生效）
2. 在途排空：swap 瞬间已持有的旧 lease 仍解析到旧实例，release 后才 drain
3. 先建后换 fail-soft：factory 抛错 → rebuild 传播异常，旧实例原地不动
4. config_hash 判据：needs_rebuild 只在 digest 变化时为真
5. 槽数预算守卫：第 9 个注册抛红（防槽地狱）
6. SlotHandle 惰性解析：持柄后 swap，handle.instance 拿到新实例（根治值快照）
7. 未知槽访问抛 KeyError（不静默返回 None）
8. C4 台账：swap_history 记录可审计；lingmemory 缺席时降级进程内 list 不炸
"""

from __future__ import annotations

import pytest

from lingclaude.core.slot import (
    MAX_SLOTS,
    SlotHandle,
    SlotManager,
    config_digest,
)


class _FakeProvider:
    """假 provider：带名字区分实例代际。"""

    def __init__(self, name: str) -> None:
        self.name = name

    def complete(self, prompt: str) -> str:
        return f"{self.name}:{prompt}"


# ---------------------------------------------------------------------------
# 1-2. swap 基本语义 + 在途排空
# ---------------------------------------------------------------------------
class TestSwapSemantics:
    def test_swap_new_lease_gets_new_instance(self) -> None:
        mgr = SlotManager()
        h = mgr.register("model_provider", initial=_FakeProvider("v1"))
        assert h.instance().name == "v1"
        mgr.rebuild("model_provider", lambda: _FakeProvider("v2"), reason="config")
        assert h.instance().name == "v2"  # 换手即生效

    def test_inflight_lease_keeps_old_instance_until_release(self) -> None:
        mgr = SlotManager()
        h = mgr.register("model_provider", initial=_FakeProvider("v1"))
        old_lease = h.checkout()
        assert old_lease.instance.name == "v1"

        mgr.rebuild("model_provider", lambda: _FakeProvider("v2"), reason="config")

        # 在途 lease 仍解析到旧实例（快照正确性：不是错，是要管理的生命周期）
        assert old_lease.instance.name == "v1"
        # 新 lease 走新实例
        assert h.checkout().instance.name == "v2"
        # 旧 lease 未 release → drain 回调未触发
        old_lease.release()

    def test_lease_context_manager_auto_releases(self) -> None:
        mgr = SlotManager()
        h = mgr.register("model_provider", initial=_FakeProvider("v1"))
        with h.checkout() as lease:
            assert lease.instance.name == "v1"
        assert mgr.slot("model_provider").outstanding() == 0
        # release 后旧实例应被 drain（本测试无 on_drain 回调，验证不炸即可）

    def test_lease_instance_resolves_at_checkout_time(self) -> None:
        mgr = SlotManager()
        h = mgr.register("model_provider", initial=_FakeProvider("v1"))
        lease = h.checkout()
        assert lease.instance.name == "v1"
        lease.release()
        mgr.rebuild("model_provider", lambda: _FakeProvider("v2"), reason="config")
        # 新 lease 解析到新实例
        assert h.checkout().instance.name == "v2"

    def test_drain_callback_fires_after_last_lease(self) -> None:
        drained: list[str] = []
        mgr = SlotManager()
        h = mgr.register(
            "model_provider",
            initial=_FakeProvider("v1"),
            on_drain=lambda old: drained.append(old.name),
        )
        lease1 = h.checkout()
        lease2 = h.checkout()
        mgr.rebuild("model_provider", lambda: _FakeProvider("v2"), reason="config")
        lease1.release()
        assert drained == []  # 还有 lease2 在途
        lease2.release()
        assert drained == ["v1"]  # 租约归零 → 旧实例 drain


# ---------------------------------------------------------------------------
# 3. fail-soft：factory 异常旧实例不动
# ---------------------------------------------------------------------------
class TestFailSoft:
    def test_failed_factory_leaves_old_instance(self) -> None:
        mgr = SlotManager()
        h = mgr.register("model_provider", initial=_FakeProvider("v1"))

        def _boom() -> _FakeProvider:
            raise RuntimeError("provider 构建失败")

        with pytest.raises(RuntimeError):
            mgr.rebuild("model_provider", _boom, reason="config")
        # 旧实例原地不动
        assert h.instance().name == "v1"


# ---------------------------------------------------------------------------
# 4. config_hash 判据
# ---------------------------------------------------------------------------
class TestConfigDigest:
    def test_same_config_no_rebuild(self) -> None:
        mgr = SlotManager()
        mgr.register("s", initial=None)  # 初始无实例 → digest None
        assert mgr.needs_rebuild("s", {"a": 1}) is True
        mgr.rebuild("s", lambda: {"a": 1}, reason="config")
        assert mgr.needs_rebuild("s", {"a": 1}) is False
        assert mgr.needs_rebuild("s", {"a": 2}) is True

    def test_digest_stable_for_key_order(self) -> None:
        assert config_digest({"a": 1, "b": 2}) == config_digest({"b": 2, "a": 1})


# ---------------------------------------------------------------------------
# 5. 槽数预算守卫
# ---------------------------------------------------------------------------
class TestSlotBudget:
    def test_ninth_slot_raises(self) -> None:
        mgr = SlotManager()
        for i in range(MAX_SLOTS):
            mgr.register(f"slot_{i}", initial=i)
        with pytest.raises(ValueError, match="槽数预算"):
            mgr.register("slot_overflow", initial=99)

    def test_duplicate_slot_raises(self) -> None:
        mgr = SlotManager()
        mgr.register("a", initial=1)
        with pytest.raises(ValueError, match="已注册"):
            mgr.register("a", initial=2)


# ---------------------------------------------------------------------------
# 6-7. SlotHandle 惰性解析 + 未知槽
# ---------------------------------------------------------------------------
class TestSlotHandle:
    def test_handle_lazy_resolution_no_snapshot(self) -> None:
        """根治 subagent_tools.py:33 值快照：持柄后 swap，解析到新实例。"""
        mgr = SlotManager()
        h = mgr.register("model_provider", initial=_FakeProvider("v1"))
        handle = mgr.handle("model_provider")
        mgr.rebuild("model_provider", lambda: _FakeProvider("v2"), reason="config")
        # .instance 是方法（惰性解析入口），不是属性 —— 调用它拿到当前实例
        assert handle.instance().name == "v2"

    def test_unknown_slot_keyerror(self) -> None:
        mgr = SlotManager()
        with pytest.raises(KeyError):
            mgr.get("nonexistent")
        with pytest.raises(KeyError):
            mgr.handle("nonexistent")


# ---------------------------------------------------------------------------
# 8. C4 swap 台账
# ---------------------------------------------------------------------------
class TestSwapLedger:
    def test_swap_recorded_and_queryable(self) -> None:
        mgr = SlotManager()
        mgr.register("model_provider", initial=_FakeProvider("v1"))
        mgr.rebuild("model_provider", lambda: _FakeProvider("v2"), reason="config")
        history = mgr.swap_history("model_provider")
        assert len(history) == 1
        rec = history[0]
        assert rec.slot_name == "model_provider"
        assert rec.reason == "config"
        assert rec.epoch == 1
        d = rec.as_dict()
        assert d["type"] == "slot_swap"
        assert "new_digest" in d and d["new_digest"]
