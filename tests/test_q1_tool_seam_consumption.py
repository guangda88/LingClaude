"""Q1 (2026-09-14): 主干热路径走 seam —— ToolRegistry.execute 优先查 SeamRegistry。

灵元尺子：主干不依赖插片、热拔插 = 同名覆盖即生效。
此前 SeamRegistry.TOOL 只有 register（登记处）没有 get 消费（影子表）；
本测试锁定「注册表换血后 execute 真正走到新实例」。

契约：
1. 同名覆盖（外部代理）→ execute 走新实例（热拔插生效）
2. 未注册（miss）→ 回退内部 handler（graceful degrade）
3. 自注册影子（指向本 registry）→ 不递归死循环，走内部 handler
4. unregister 外部代理后 → 回退内部 handler
"""
from __future__ import annotations

import pytest

from lingclaude.core.seam import SeamRegistry, SeamType
from lingclaude.engine.tools import ToolDefinition, ToolRegistry


@pytest.fixture(autouse=True)
def _clean_seam():
    """每个测试前清空 TOOL 槽位，避免跨测试泄漏。"""
    for name in list(SeamRegistry.list_names(SeamType.TOOL)):
        SeamRegistry.unregister(SeamType.TOOL, name)
    yield
    for name in list(SeamRegistry.list_names(SeamType.TOOL)):
        SeamRegistry.unregister(SeamType.TOOL, name)


class _ExternalProxy:
    """外部换血代理：不指向任何 ToolRegistry，独立可调用。"""

    name = "echo"

    def __init__(self, marker: str = "external") -> None:
        self.marker = marker

    def execute(self, **kwargs: Any) -> str:
        return f"{self.marker}:{kwargs.get('text', '')}"


def _make_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        ToolDefinition(
            name="echo",
            description="echo text",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}},
            handler=lambda text="": f"internal:{text}",
            handler_name="echo",
        )
    )
    reg.register_handler("echo", lambda text="": f"internal:{text}")
    return reg


def test_same_name_overwrite_goes_through_seam_proxy():
    """契约1：外部代理同名覆盖 → execute 走新实例（热拔插生效）。"""
    reg = _make_registry()
    # 未覆盖前走内部 handler
    assert reg.execute("echo", text="hi").data == "internal:hi"

    # 外部换血：注册同名代理
    SeamRegistry.register(SeamType.TOOL, "echo", _ExternalProxy("v2"))
    assert reg.execute("echo", text="hi").data == "v2:hi"


def test_miss_falls_back_to_internal_handler():
    """契约2：seam miss → 回退内部 handler。"""
    reg = _make_registry()
    assert reg.execute("echo", text="x").data == "internal:x"


def test_self_shadow_proxy_does_not_recursion():
    """契约3：自注册影子（指向本 registry）不递归死循环，走内部 handler。"""
    reg = _make_registry()
    # register() 已自动注册指向自身的 _ToolSeamProxy —— execute 不应因此死循环
    assert reg.execute("echo", text="y").data == "internal:y"


def test_unregister_external_proxy_falls_back():
    """契约4：unregister 外部代理后 → 回退内部 handler。"""
    reg = _make_registry()
    SeamRegistry.register(SeamType.TOOL, "echo", _ExternalProxy("v3"))
    assert reg.execute("echo", text="z").data == "v3:z"
    SeamRegistry.unregister(SeamType.TOOL, "echo")
    assert reg.execute("echo", text="z").data == "internal:z"
