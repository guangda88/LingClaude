"""P12 (2026-09-14): sandbox 后端同步到进程内 SeamRegistry（SeamType.SANDBOX 槽位）。

验收口径（灵元 P5 先例对称延伸）:
- create_default_sandbox_provider() 创建默认后端时，同步注册到
  lingclaude/core/seam.py 的进程内 SeamRegistry（SeamType.SANDBOX 槽位）
- SeamRegistry.get(SANDBOX, name) 可查到真实 sandbox 后端（bwrap 或 noop），
  且实例满足 SandboxPlugin 协议（available + wrap）
- 热拔插语义：unregister 后 get 抛 KeyError；再 register 同名可恢复（热更）
- bash.py 运行时路径不受影响（仍走 CapabilitySeam，本注册是查询视图补充）
"""
from __future__ import annotations

import pytest

from lingclaude.core.seam import SeamRegistry, SeamType


@pytest.fixture(autouse=True)
def _reset_seam(monkeypatch: pytest.MonkeyPatch):
    SeamRegistry.reset()
    yield
    SeamRegistry.reset()


def test_default_provider_registers_to_sandbox_slot() -> None:
    """create_default_sandbox_provider() 后，SANDBOX 槽位有且仅有一个后端可查。"""
    from lingclaude.engine.sandbox_provider import create_default_sandbox_provider

    provider = create_default_sandbox_provider()

    # 统一查询视图：进程内 SeamRegistry 可查真实后端（bwrap 或 noop）
    names = SeamRegistry.list_names(SeamType.SANDBOX)
    assert len(names) == 1
    assert names[0] == provider.name

    seam = SeamRegistry.get(SeamType.SANDBOX, provider.name)
    assert seam is not None
    assert seam.name == provider.name


def test_sandbox_seam_satisfies_protocol() -> None:
    """注册的 sandbox 后端必须满足 SandboxPlugin 协议（available + wrap）。"""
    from lingclaude.engine.sandbox_provider import create_default_sandbox_provider

    provider = create_default_sandbox_provider()

    seam = SeamRegistry.get(SeamType.SANDBOX, provider.name)
    # 结构性协议检查（seam.py 的 SandboxPlugin）
    assert SeamRegistry.check_protocol(SeamType.SANDBOX, seam) == []
    # 实例可直接调用（消费方无感）
    assert callable(seam.wrap)


def test_unregister_hot_swap_semantics() -> None:
    """热拔插：unregister 后 get 抛 KeyError；再 register 同名恢复（热更语义）。"""
    from lingclaude.engine.sandbox_provider import NoopSandboxProvider

    # 先造一个可控实例（不依赖环境探测）
    noop = NoopSandboxProvider()
    SeamRegistry.register(SeamType.SANDBOX, "noop", noop)

    # 可查
    assert SeamRegistry.get(SeamType.SANDBOX, "noop") is noop

    # 热拔：unregister 只影响后续 get，已持引用不受影响
    assert SeamRegistry.unregister(SeamType.SANDBOX, "noop") is True
    with pytest.raises(KeyError):
        SeamRegistry.get(SeamType.SANDBOX, "noop")
    # 已持引用仍可调用
    assert noop.available() is True

    # 热插：同名覆盖恢复
    SeamRegistry.register(SeamType.SANDBOX, "noop", noop)
    assert SeamRegistry.get(SeamType.SANDBOX, "noop") is noop

    # 幂等：未注册名 unregister no-op
    assert SeamRegistry.unregister(SeamType.SANDBOX, "not_exists") is False


def test_reset_clears_sandbox_slot() -> None:
    """reset 清空 SANDBOX 槽位（与 provider/tool 对称，防跨测试泄漏）。"""
    from lingclaude.engine.sandbox_provider import NoopSandboxProvider

    SeamRegistry.register(SeamType.SANDBOX, "noop", NoopSandboxProvider())
    SeamRegistry.reset()
    assert SeamRegistry.list_names(SeamType.SANDBOX) == []
