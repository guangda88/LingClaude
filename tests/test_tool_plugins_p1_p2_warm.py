"""P1/P2 追加回归：ast 插件 + warm 接线（plugin_runner → mcp_proxy stdio 连接池）。

覆盖：
- ast_plugin 加载 + list_functions / ast_replace 委托 engine/ast_edit
- register_plugin_server / call_plugin_server（warm 级：子进程隔离 + 连接池）
- 幂等注册（同 key 不重复）
- call_plugin_server 对未注册工具的 fail-fast
"""

from __future__ import annotations

import pytest

from lingclaude.core.plugin_loader import PluginLoader
from lingclaude.core.seam import SeamRegistry, SeamType
from lingclaude.engine.plugin_runner import (
    call_plugin_server,
    register_plugin_server,
    run_plugin_subprocess,
)

TOOLS_DIR = "lingclaude/plugins/tools"


@pytest.fixture(autouse=True)
def _clean_seam_registry():
    for name in list(SeamRegistry.list_names(SeamType.TOOL)):
        SeamRegistry.unregister(SeamType.TOOL, name)
    yield
    for name in list(SeamRegistry.list_names(SeamType.TOOL)):
        SeamRegistry.unregister(SeamType.TOOL, name)


class TestAstPlugin:
    """P1: ast 工具组插件（ast_replace / list_functions）。"""

    @pytest.fixture()
    def plugin(self):
        loader = PluginLoader()
        results = loader.load_plugins_from_dir(TOOLS_DIR)
        assert "ast_plugin" in results, results
        assert results["ast_plugin"].is_ok, results["ast_plugin"].error
        return results["ast_plugin"].instance

    def test_loads_in_dir(self):
        """ast_plugin 随目录批量加载（6 个插件并存）。"""
        results = PluginLoader().load_plugins_from_dir(TOOLS_DIR)
        assert "ast_plugin" in results
        assert results["ast_plugin"].is_ok

    def test_list_functions_delegates(self, plugin):
        """list_functions 委托 engine/ast_edit，返回函数清单。"""
        r = plugin.execute(name="list_functions", file_path="lingclaude/plugins/tools/ast/plugin.py")
        assert "functions" in r, r
        assert any(f["name"] == "AstPlugin.execute" for f in r["functions"])

    def test_ast_replace_delegates(self, plugin, tmp_path, monkeypatch):
        """ast_replace 真实替换函数体（AST 级，非文本匹配）。"""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "s.py"
        f.write_text("def foo():\n    return 1\n", encoding="utf-8")
        r = plugin.execute(name="ast_replace", file_path=str(f),
                           function_name="foo", new_body="    return 2\n")
        assert "error" not in r, r
        assert r["success"] is True
        assert "return 2" in f.read_text(encoding="utf-8")

    def test_ast_replace_missing_file(self, plugin):
        """文件不存在 → 返回 error（不抛异常）。"""
        r = plugin.execute(name="ast_replace", file_path="no/such/file.py",
                           function_name="foo", new_body="    pass\n")
        assert "error" in r


class TestWarmPluginServer:
    """P2 (warm 接线): 插件 → stdio MCP server → mcp_proxy 连接池。"""

    def test_register_and_call_read(self):
        """register_plugin_server + call_plugin_server 端到端（子进程隔离）。"""
        key = register_plugin_server(f"{TOOLS_DIR}/read/manifest.plugin.json")
        assert key == "plugin:read_plugin"
        r = call_plugin_server(key, "read", {
            "path": "lingclaude/plugins/tools/read/plugin.py",
            "offset": 1, "limit": 2,
        })
        assert r["ok"] is True, r.get("error")

    def test_register_idempotent(self):
        """同 key 重复注册 → 返回同 key，不重复注册。"""
        key1 = register_plugin_server(f"{TOOLS_DIR}/read/manifest.plugin.json")
        key2 = register_plugin_server(f"{TOOLS_DIR}/read/manifest.plugin.json")
        assert key1 == key2 == "plugin:read_plugin"

    def test_register_missing_manifest_returns_none(self):
        """manifest 不存在 → 返回 None（不炸）。"""
        assert register_plugin_server("no/such/manifest.plugin.json") is None

    def test_call_unregistered_tool_fails_fast(self):
        """未注册工具 → 返回 error（不挂死）。"""
        r = call_plugin_server("plugin:ghost", "ghost_tool", {})
        assert r["ok"] is False
        assert "未注册" in r["error"]

    def test_ast_plugin_subprocess(self):
        """ast_plugin 子进程执行（warm 骨架与 stdio 双通道并存）。"""
        r = run_plugin_subprocess(
            f"{TOOLS_DIR}/ast/manifest.plugin.json", "execute",
            {"name": "list_functions", "file_path": "lingclaude/plugins/tools/ast/plugin.py"},
        )
        assert r["ok"] is True, r.get("error")
        inner = r["data"]
        assert inner["ok"] is True
        assert "functions" in inner["data"]


class TestToolAliasHotplug:
    """P2 深化 (2026-09-14): provides 工具名别名代理 → 热拔插通道接通工具执行路径。

    此前 PluginLoader 只注册 manifest.name（read_plugin），ToolRegistry.execute(name)
    按工具名（read）查 seam 永远 miss —— 热拔插在工具执行路径上从未真正生效。
    修复后：工具名注册为 _PluginAliasProxy，插件加载即接管对应工具，卸载即回退主干。
    """

    def _build_registry(self):
        from lingclaude.engine.tools import ToolDefinition, ToolRegistry

        reg = ToolRegistry()
        reg.register(ToolDefinition(name="read", description="", parameters={},
                                    handler_name="h_read"))
        reg.register(ToolDefinition(name="git_status", description="", parameters={},
                                    handler_name="h_gs"))
        reg.register_handler("h_read", lambda **kw: {"via": "main"})
        reg.register_handler("h_gs", lambda **kw: {"via": "main"})
        return reg

    def test_provides_aliases_registered(self):
        """provides 工具名注册为 seam TOOL 槽位（read/bash/git_status 等）。"""
        PluginLoader().load_plugins_from_dir(TOOLS_DIR)
        names = set(SeamRegistry.list_names(SeamType.TOOL))
        assert "read" in names          # read_plugin 的 provides
        assert "bash" in names          # bash_plugin 的 provides
        assert "git_status" in names    # git_plugin 的 provides
        assert "read_plugin" in names   # manifest.name 本体

    def test_execute_goes_through_plugin(self):
        """主干先注册 → 插件后加载 → ToolRegistry.execute 走插件（非主干）。"""
        reg = self._build_registry()
        PluginLoader().load_plugins_from_dir(TOOLS_DIR)
        r = reg.execute("read", path="lingclaude/plugins/tools/read/plugin.py",
                        offset=1, limit=2)
        assert r.is_ok, r.error
        text = str(r.data.to_dict() if hasattr(r.data, "to_dict") else r.data)
        assert "lines" in text      # read_plugin 结果
        assert "via" not in text    # 非主干占位

    def test_unload_falls_back_to_core(self):
        """卸载插件 → 工具名别名清理 → execute 回退主干。"""
        reg = self._build_registry()
        loader = PluginLoader()
        loader.load_plugins_from_dir(TOOLS_DIR)
        mf = loader._loaded["read_plugin"].manifest
        assert loader.unload_plugin(mf)
        # 别名已清理
        assert SeamRegistry.get_optional(SeamType.TOOL, "read") is None
        # 回退主干
        r = reg.execute("read", path="x")
        assert r.is_ok and r.data == {"via": "main"}

