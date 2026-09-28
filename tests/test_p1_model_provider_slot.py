"""P1 红→绿验收：model_provider 入槽 + 主干 swap + 子代理 SlotHandle 惰性解析。

对应主方案 §八 P1 验收：M6 swap 测试绿；改 config model 不重启生效。

红因（改动前）：
- coding.py __init__ 裸持 provider 实例（self._model_provider = 值），swap 需重启进程；
- subagent_tools.py:33 ctx 持 provider 值快照，主干换实例后子代理仍用旧引用。

改动后以下测试必须绿。
"""

from __future__ import annotations

from typing import Any

import pytest

from lingclaude.core.config import lingclaudeConfig
from lingclaude.core.slot import SlotHandle, SlotManager
from lingclaude.engine.coding import CodingRuntime


class _FakeProvider:
    """假 provider：带名字区分实例代际。"""

    def __init__(self, name: str) -> None:
        self.name = name

    def complete(self, prompt: str) -> str:
        return f"{self.name}:{prompt}"


def _runtime_with_provider(provider: Any) -> CodingRuntime:
    return CodingRuntime(lingclaudeConfig(), model_provider=provider)


# ---------------------------------------------------------------------------
# 主干 model_provider 入槽
# ---------------------------------------------------------------------------
class TestModelProviderInSlot:
    def test_model_provider_is_slot_handle_not_instance(self) -> None:
        rt = _runtime_with_provider(_FakeProvider("v1"))
        # 主干持的是 SlotHandle（句柄），不是裸实例 —— 根治值快照
        assert isinstance(rt._model_provider, SlotHandle)

    def test_model_provider_resolves_to_instance(self) -> None:
        rt = _runtime_with_provider(_FakeProvider("v1"))
        provider = rt._model_provider.instance()
        assert isinstance(provider, _FakeProvider)
        assert provider.name == "v1"

    def test_slot_manager_exposes_provider_slot(self) -> None:
        rt = _runtime_with_provider(_FakeProvider("v1"))
        assert "model_provider" in rt._slot_manager.names()


# ---------------------------------------------------------------------------
# M6 swap 行为：config 变 → 实例换 → 在途不炸
# ---------------------------------------------------------------------------
class TestSwapBehavior:
    def test_swap_new_lease_gets_new_instance(self) -> None:
        rt = _runtime_with_provider(_FakeProvider("v1"))
        mgr: SlotManager = rt._slot_manager
        mgr.rebuild("model_provider", lambda: _FakeProvider("v2"), reason="config")
        assert rt._model_provider.instance().name == "v2"

    def test_inflight_lease_survives_swap(self) -> None:
        rt = _runtime_with_provider(_FakeProvider("v1"))
        lease = rt._model_provider.checkout()
        assert lease.instance.name == "v1"
        rt._slot_manager.rebuild("model_provider", lambda: _FakeProvider("v2"), reason="config")
        # 在途 lease 仍解析到旧实例（排空协议保护，不炸）
        assert lease.instance.name == "v1"
        lease.release()
        # 新请求走新实例
        assert rt._model_provider.instance().name == "v2"

    def test_failed_rebuild_leaves_old_instance(self) -> None:
        rt = _runtime_with_provider(_FakeProvider("v1"))

        def _boom() -> _FakeProvider:
            raise RuntimeError("provider 构建失败")

        with pytest.raises(RuntimeError):
            rt._slot_manager.rebuild("model_provider", _boom, reason="config")
        assert rt._model_provider.instance().name == "v1"  # fail-soft

    def test_swap_record_written(self) -> None:
        rt = _runtime_with_provider(_FakeProvider("v1"))
        rt._slot_manager.rebuild("model_provider", lambda: _FakeProvider("v2"), reason="config")
        history = rt._slot_manager.swap_history("model_provider")
        assert len(history) == 1
        assert history[0].reason == "config"


# ---------------------------------------------------------------------------
# 子代理 SlotHandle 惰性解析（根治 subagent_tools.py:33 快照）
# ---------------------------------------------------------------------------
class TestSubagentLazyResolution:
    def test_subagent_ctx_receives_handle_not_snapshot(self) -> None:
        rt = _runtime_with_provider(_FakeProvider("v1"))
        # subagent_tools 构造的 ctx.model_provider 应是 SlotHandle（self._model_provider）
        assert isinstance(rt._model_provider, SlotHandle)

    def test_handle_resolves_new_instance_after_swap(self) -> None:
        """持柄后 swap，句柄解析到新实例 —— 子代理不再拿旧 provider。"""
        rt = _runtime_with_provider(_FakeProvider("v1"))
        handle = rt._model_provider  # subagent ctx 拿到的就是这个
        rt._slot_manager.rebuild("model_provider", lambda: _FakeProvider("v2"), reason="config")
        # 句柄调用时解析 → 新实例（快照泄漏根治）
        assert handle.instance().name == "v2"

    def test_resolve_provider_compatible_with_bare_instance(self) -> None:
        """inprocess._resolve_provider 对裸实例向后兼容（非 SlotHandle 原样返回）。"""
        from lingclaude.engine.subagent.inprocess import _resolve_provider

        bare = _FakeProvider("bare")
        assert _resolve_provider(bare) is bare
        assert _resolve_provider(None) is None
