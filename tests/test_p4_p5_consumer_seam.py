"""P4: LingBus 消费者模块化 + P5: SeamRegistry 接入真实注册表。

覆盖:
  P4-1  coordination/bus_consumer.py 存在且 start/stop 语义正确（不真启动线程，
        用 monkeypatch 替换 BusResponder 验证循环可被 stop_event 停止）
  P4-2  repl_turn.start_bus_responder_background 兼容壳转发（行为不变）
  P4-3  api._start_bus_consumer_if_enabled env 门控（0 关闭 / 未设开启 / fail-soft）
  P5-1  ProviderRegistry.register → SeamRegistry.get(PROVIDER, name) 可见
  P5-2  ProviderRegistry.reset → SeamRegistry PROVIDER 槽位同步清空
  P5-3  ToolRegistry.register → SeamRegistry.get(TOOL, name) 可见
  P5-4  ToolRegistry.unregister → SeamRegistry 同步注销
  P5-5  ToolRegistry.reset → SeamRegistry TOOL 槽位同步清空
"""
from __future__ import annotations

import threading

import pytest

from lingclaude.core.seam import SeamRegistry, SeamType
from lingclaude.engine.tools import ToolDefinition, ToolRegistry
from lingclaude.model.provider_registry import ProviderRegistry
from lingclaude.model.types import ModelConfig, ModelProvider


# ---------------------------------------------------------------------------
# P4: bus_consumer 模块化
# ---------------------------------------------------------------------------


class _FakeBusResponder:
    """可控 BusResponder 桩：计数 poll，不真连 LingBus。"""

    poll_count = 0

    def poll_and_respond(self) -> None:
        _FakeBusResponder.poll_count += 1


def test_p4_bus_consumer_start_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """start_bus_consumer_background 启动线程并可用 stop_event 协作停止。"""
    monkeypatch.setattr(
        "lingclaude.coordination.bus_consumer.BusResponder",
        _FakeBusResponder,
    )
    from lingclaude.coordination.bus_consumer import (
        start_bus_consumer_background,
        stop_bus_consumer,
    )

    _FakeBusResponder.poll_count = 0
    stop_event = start_bus_consumer_background(
        interval=0.01,
        thread_name="test-bus-consumer",
    )
    assert isinstance(stop_event, threading.Event)

    # 给线程至少一次 poll 机会（interval=0.01 极短）
    stop_event.wait(timeout=0.05)  # 等一次轮询周期
    stop_bus_consumer(stop_event)

    # 线程退出（daemon + stop_event set）
    # 因 poll 为同步调用，此处仅断言 stop_event 已 set 且轮询确实发生过
    assert stop_event.is_set()
    assert _FakeBusResponder.poll_count >= 1


def test_p4_bus_consumer_stop_noop() -> None:
    """stop_bus_consumer(None) 幂等 no-op。"""
    from lingclaude.coordination.bus_consumer import stop_bus_consumer

    stop_bus_consumer(None)  # 不抛即通过


def test_p4_repl_turn_compat_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """repl_turn 兼容壳转发到 bus_consumer（行为不变，线程名沿用原值）。"""
    captured: dict[str, object] = {}

    def _fake_start(interval: float, thread_name: str) -> threading.Event:
        captured["interval"] = interval
        captured["thread_name"] = thread_name
        return threading.Event()

    monkeypatch.setattr(
        "lingclaude.coordination.bus_consumer.start_bus_consumer_background",
        _fake_start,
    )
    from lingclaude.cli.repl_turn import start_bus_responder_background

    ev = start_bus_responder_background(interval=12.0)
    assert isinstance(ev, threading.Event)
    assert captured["interval"] == 12.0
    assert captured["thread_name"] == "lingclaude-bus-responder"


def test_p4_api_consumer_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """LINGCLAUDE_BUS_LISTENER=0 时 api 不启动消费者（与 conftest 对齐）。"""
    import lingclaude.api as api_mod

    monkeypatch.setenv("LINGCLAUDE_BUS_LISTENER", "0")
    started: list[str] = []

    def _fake_start(**_kw: object) -> threading.Event:
        started.append("started")
        return threading.Event()

    monkeypatch.setattr(api_mod, "start_bus_consumer_background", _fake_start)
    api_mod._BUS_CONSUMER_STOP = None
    api_mod._start_bus_consumer_if_enabled()
    assert started == []
    assert api_mod._BUS_CONSUMER_STOP is None


def test_p4_api_consumer_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """未设置 env（默认开）时 api 启动消费者。"""
    import lingclaude.api as api_mod

    monkeypatch.delenv("LINGCLAUDE_BUS_LISTENER", raising=False)
    started: list[str] = []

    def _fake_start(**_kw: object) -> threading.Event:
        started.append("started")
        return threading.Event()

    monkeypatch.setattr(api_mod, "start_bus_consumer_background", _fake_start)
    api_mod._BUS_CONSUMER_STOP = None
    api_mod._start_bus_consumer_if_enabled()
    assert started == ["started"]
    assert api_mod._BUS_CONSUMER_STOP is not None


def test_p4_api_consumer_fail_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    """消费者启动异常只告警不阻断（fail-soft）。"""
    import lingclaude.api as api_mod

    monkeypatch.delenv("LINGCLAUDE_BUS_LISTENER", raising=False)

    def _boom(**_kw: object) -> threading.Event:
        raise RuntimeError("bus unavailable")

    monkeypatch.setattr(api_mod, "start_bus_consumer_background", _boom)
    api_mod._BUS_CONSUMER_STOP = None
    # 不抛即通过（内部 except 捕获）
    api_mod._start_bus_consumer_if_enabled()
    assert api_mod._BUS_CONSUMER_STOP is None


# ---------------------------------------------------------------------------
# P5: SeamRegistry 接入真实注册表
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_seam_and_provider_registries():
    """每个用例前后清空共享注册表（类级 SeamRegistry + ProviderRegistry）。"""
    SeamRegistry.reset()
    ProviderRegistry.reset()
    yield
    SeamRegistry.reset()
    ProviderRegistry.reset()


def _mk_tool(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="test",
        parameters={},
        handler_name=name,
    )


def test_p5_provider_register_syncs_to_seam() -> None:
    """ProviderRegistry.register → SeamRegistry.get(PROVIDER, name) 可见。"""

    class _FakeProvider(ModelProvider):
        def complete(self, *a, **kw):
            from lingclaude.core.types import Result

            return Result.ok(None)

        async def acomplete(self, *a, **kw):
            from lingclaude.core.types import Result

            return Result.ok(None)

        def count_tokens(self, text: str) -> int:
            return 0

    ProviderRegistry.register("fake_provider", _FakeProvider)
    assert SeamRegistry.has(SeamType.PROVIDER, "fake_provider")
    got = SeamRegistry.get(SeamType.PROVIDER, "fake_provider")
    assert got is _FakeProvider


def test_p5_provider_reset_clears_seam() -> None:
    """ProviderRegistry.reset → SeamRegistry PROVIDER 槽位同步清空。"""

    class _FakeProvider(ModelProvider):
        def complete(self, *a, **kw):
            from lingclaude.core.types import Result

            return Result.ok(None)

        async def acomplete(self, *a, **kw):
            from lingclaude.core.types import Result

            return Result.ok(None)

        def count_tokens(self, text: str) -> int:
            return 0

    ProviderRegistry.register("fake_provider", _FakeProvider)
    assert SeamRegistry.has(SeamType.PROVIDER, "fake_provider")
    ProviderRegistry.reset()
    assert not SeamRegistry.has(SeamType.PROVIDER, "fake_provider")


def test_p5_tool_register_syncs_to_seam() -> None:
    """ToolRegistry.register → SeamRegistry.get(TOOL, name) 可见（可执行代理）。"""
    reg = ToolRegistry()
    reg.register(_mk_tool("p5_tool"))
    assert SeamRegistry.has(SeamType.TOOL, "p5_tool")
    got = SeamRegistry.get(SeamType.TOOL, "p5_tool")
    assert got.name == "p5_tool"
    # proxy.execute 委托回 registry（handler 未绑定时走 Result.fail，而非 AttributeError）
    result = got.execute()
    assert result.is_error  # 未注册 handler → NO_HANDLER
    assert result.code == "NO_HANDLER"


def test_p5_tool_unregister_syncs_to_seam() -> None:
    """ToolRegistry.unregister → SeamRegistry 同步注销。"""
    reg = ToolRegistry()
    reg.register(_mk_tool("p5_tool"))
    assert SeamRegistry.has(SeamType.TOOL, "p5_tool")
    assert reg.unregister("p5_tool") is None  # ToolRegistry.unregister 返回 None
    assert not SeamRegistry.has(SeamType.TOOL, "p5_tool")


def test_p5_tool_reset_clears_seam() -> None:
    """ToolRegistry.reset → SeamRegistry TOOL 槽位同步清空。"""
    reg = ToolRegistry()
    reg.register(_mk_tool("p5_tool_a"))
    reg.register(_mk_tool("p5_tool_b"))
    assert SeamRegistry.has(SeamType.TOOL, "p5_tool_a")
    assert SeamRegistry.has(SeamType.TOOL, "p5_tool_b")
    reg.reset()
    assert not SeamRegistry.has(SeamType.TOOL, "p5_tool_a")
    assert not SeamRegistry.has(SeamType.TOOL, "p5_tool_b")


def test_p5_tool_protocol_check_passes() -> None:
    """SeamRegistry.get(TOOL) 的代理实体通过 ToolPlugin 结构性检查（name + execute）。"""
    reg = ToolRegistry()
    reg.register(_mk_tool("p5_tool"))
    got = SeamRegistry.get(SeamType.TOOL, "p5_tool")
    missing = SeamRegistry.check_protocol(SeamType.TOOL, got)
    assert missing == []
