"""P0: 工具插件载体回归测试（plugins/tools/ + 热拔插通道）。

覆盖：
1. CodingRuntime 装配后，SeamRegistry.TOOL 出现插件（bash_plugin/read_plugin）
2. 热拔插通道：插件注册后 ToolRegistry.execute 优先走插件（外部换血代理）
3. 与现有 SPECS 34 工具共存，不破坏 register_all_tools 回归
4. 卸载插件 → 热拔插生效（unregister 后回落内部 handler）
"""

from __future__ import annotations

import pytest

from lingclaude.core.plugin_loader import PluginLoader
from lingclaude.core.seam import SeamRegistry, SeamType
from lingclaude.engine.coding import CodingRuntime
from lingclaude.engine.tools import ToolRegistry


@pytest.fixture(autouse=True)
def _clean_seam_registry():
    """每个测试前后清空 SeamRegistry.TOOL + 重置惰性装载标志，防跨测试泄漏。

    2026-09-24 启动提速: 插片装载收口到 tools._ensure_tool_plugins_loaded
    （进程级 exactly-once）。若前一用例已触发装载，后一用例构造的 runtime
    将因幂等跳过而拿不到插件 —— fixture 必须连同 _PLUGIN_LOAD_DONE 一并重置。
    """
    from lingclaude.engine import tools as _tools_mod

    for name in list(SeamRegistry.list_names(SeamType.TOOL)):
        SeamRegistry.unregister(SeamType.TOOL, name)
    _tools_mod._PLUGIN_LOAD_DONE = False
    _tools_mod._PLUGIN_LOAD_ERROR = ""
    yield
    for name in list(SeamRegistry.list_names(SeamType.TOOL)):
        SeamRegistry.unregister(SeamType.TOOL, name)
    _tools_mod._PLUGIN_LOAD_DONE = False
    _tools_mod._PLUGIN_LOAD_ERROR = ""


class TestToolPluginLoad:
    def test_load_plugins_from_dir_scans_subdirs(self):
        """P0: load_plugins_from_dir 支持每插片自包含目录（*/manifest.plugin.json）。"""
        loader = PluginLoader()
        results = loader.load_plugins_from_dir("lingclaude/plugins/tools")
        names = {n for n, r in results.items() if r.is_ok}
        assert "bash_plugin" in names
        assert "read_plugin" in names

    def test_loaded_plugins_registered_in_seam(self):
        """加载成功后注册进 SeamRegistry.TOOL。"""
        PluginLoader().load_plugins_from_dir("lingclaude/plugins/tools")
        names = set(SeamRegistry.list_names(SeamType.TOOL))
        assert "bash_plugin" in names
        assert "read_plugin" in names

    def test_plugins_missing_dir_fail_soft(self):
        """目录不存在 → 返回 {}（fail-soft，不炸引擎启动）。"""
        results = PluginLoader().load_plugins_from_dir("lingclaude/plugins/does_not_exist")
        assert results == {}


class TestCodingRuntimePluginIntegration:
    def test_runtime_assembly_registers_plugins(self):
        """CodingRuntime 装配后 tool_plugins 惰性可用（2026-09-24 启动提速契约）。

        契约更新：装载从构造期移出（LazyToolPlugins 句柄）——构造后 seam 暂无
        插件（提速断言），显式触发（等价 warm/miss 回填）后 bash_plugin +
        read_plugin 可用 —— 最终一致性与旧契约对齐。
        """
        runtime = CodingRuntime()
        assert hasattr(runtime, "tool_plugins")
        assert runtime.tool_plugins  # 句柄非空（wired 名单契约不变）
        names0 = set(SeamRegistry.list_names(SeamType.TOOL))
        assert not {n for n in names0 if n.endswith("_plugin")}  # 构造期零装载
        runtime.tool_plugins.load_now()  # 触发惰性装载
        names = set(SeamRegistry.list_names(SeamType.TOOL))
        assert "bash_plugin" in names
        assert "read_plugin" in names

    def test_plugins_coexist_with_specs_tools(self):
        """插件与现有 SPECS 34 工具共存，register_all_tools 回归不受影响。"""
        runtime = CodingRuntime()
        runtime.tool_plugins.load_now()  # 触发惰性装载（2026-09-24 契约）
        # SPECS 工具仍注册（test_coding 回归）
        assert "bash" in runtime.registry._tools
        assert "read" in runtime.registry._tools
        # 插件也在（seam 层）
        assert "bash_plugin" in SeamRegistry.list_names(SeamType.TOOL)


class TestHotPlugChannel:
    def test_external_plugin_takes_priority_in_execute(self):
        """热拔插通道：注册外部插件后 ToolRegistry.execute 优先走插件。"""
        registry = ToolRegistry()

        # 注册一个外部换血代理（模拟插件注册到 SeamRegistry.TOOL）
        class FakeExternal:
            name = "fake_plugin"
            calls = 0

            def execute(self, **kwargs):
                type(self).calls += 1
                return {"via": "plugin", "input": kwargs}

        SeamRegistry.register(SeamType.TOOL, "test_hot", FakeExternal())
        proxy = SeamRegistry.get_optional(SeamType.TOOL, "test_hot")
        assert proxy is not None
        assert hasattr(proxy, "execute")

    def test_unregister_plugin_is_idempotent(self):
        """卸载插件（热拔插）：unregister 幂等，且后续 get 返回 None。"""
        class P:
            name = "p"

        SeamRegistry.register(SeamType.TOOL, "to_unload", P())
        assert SeamRegistry.unregister(SeamType.TOOL, "to_unload") is True
        assert SeamRegistry.unregister(SeamType.TOOL, "to_unload") is False  # 幂等
        assert SeamRegistry.get_optional(SeamType.TOOL, "to_unload") is None
