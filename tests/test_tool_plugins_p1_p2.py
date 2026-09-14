"""P1/P2: 工具插件扩展回归测试。

覆盖：
- P1: file_ops 插件（write/edit/create/insert/delete_lines/undo 委托 FileEditTool）
- P2: plugin_runner 子进程执行（warm 级骨架：插件跑子进程、JSON-RPC 返回）
"""

from __future__ import annotations

import os

import pytest

from lingclaude.core.plugin_loader import PluginLoader
from lingclaude.core.seam import SeamRegistry, SeamType
from lingclaude.engine.plugin_runner import run_plugin_subprocess

TOOLS_DIR = "lingclaude/plugins/tools"


@pytest.fixture(autouse=True)
def _clean_seam_registry():
    for name in list(SeamRegistry.list_names(SeamType.TOOL)):
        SeamRegistry.unregister(SeamType.TOOL, name)
    yield
    for name in list(SeamRegistry.list_names(SeamType.TOOL)):
        SeamRegistry.unregister(SeamType.TOOL, name)


class TestFileOpsPlugin:
    """P1: file_ops 工具组插件。"""

    @pytest.fixture()
    def plugin(self):
        loader = PluginLoader()
        results = loader.load_plugins_from_dir(TOOLS_DIR)
        assert "file_ops_plugin" in results
        return results["file_ops_plugin"].instance

    def test_loads_in_dir(self):
        """file_ops_plugin 随目录批量加载。"""
        results = PluginLoader().load_plugins_from_dir(TOOLS_DIR)
        assert "file_ops_plugin" in results
        assert results["file_ops_plugin"].is_ok

    def test_create_insert_read_roundtrip(self, plugin, tmp_path, monkeypatch):
        """create → insert → 文件内容正确（用 tmp_path 内相对路径）。"""
        monkeypatch.chdir(tmp_path)
        res = plugin.execute(name="file_create", path="a.txt", content="hello\n")
        assert res.is_ok
        res2 = plugin.execute(name="file_insert", path="a.txt", line=1, text="line1")
        assert res2.is_ok
        assert (tmp_path / "a.txt").read_text(encoding="utf-8").strip() == "hello\nline1"

    def test_undo_restores(self, plugin, tmp_path, monkeypatch):
        """undo 回滚到 .bak（file_edit 的 rollback 机制）。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "b.txt").write_text("original", encoding="utf-8")
        plugin.execute(name="edit", path="b.txt", old_text="original", new_text="changed")
        assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "changed"
        plugin.execute(name="file_undo", path="b.txt")
        assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "original"

    def test_delete_lines(self, plugin, tmp_path, monkeypatch):
        """delete_lines 删除指定行。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "c.txt").write_text("l1\nl2\nl3\n", encoding="utf-8")
        res = plugin.execute(name="file_delete_lines", path="c.txt", start_line=1, end_line=2)
        assert res.is_ok
        assert (tmp_path / "c.txt").read_text(encoding="utf-8") == "l1\nl3\n"


class TestPluginRunnerSubprocess:
    """P2: 子进程插件运行时（warm 级骨架）。"""

    def test_run_plugin_subprocess_read(self):
        """read_plugin 在子进程执行成功，结果 JSON 序列化。"""
        r = run_plugin_subprocess(
            f"{TOOLS_DIR}/read/manifest.plugin.json",
            "execute",
            {"path": "lingclaude/plugins/tools/read/plugin.py", "offset": 1, "limit": 2},
        )
        assert r["ok"] is True, r.get("error")
        inner = r["data"]
        assert inner["ok"] is True
        assert "lines" in inner["data"]

    def test_run_plugin_subprocess_missing_manifest(self):
        """manifest 不存在 → 返回错误（不炸）。"""
        r = run_plugin_subprocess("no/such/manifest.plugin.json", "execute", {})
        assert r["ok"] is False
        assert "manifest" in r["error"]

    def test_run_plugin_subprocess_bad_method(self):
        """调用不存在的方法 → 子进程内异常转 JSON 错误。"""
        r = run_plugin_subprocess(
            f"{TOOLS_DIR}/read/manifest.plugin.json", "no_such_method", {}
        )
        assert r["ok"] is False
        assert "AttributeError" in r["error"]

    def test_run_plugin_subprocess_timeout(self):
        """超时 → 返回超时错误（子进程被终止，不挂死主进程）。"""
        r = run_plugin_subprocess(
            f"{TOOLS_DIR}/bash/manifest.plugin.json",
            "execute",
            {"command": "sleep 5"},
            timeout=0.5,
        )
        assert r["ok"] is False
        assert "超时" in r["error"]

    def test_run_plugin_subprocess_mcp_shell_normalized(self):
        """MCP 外壳归一：{\"name\", \"arguments\"} → 只透传 arguments。

        2026-09-14 (warm 试点验证缺口补): plugin_runner 支持与 ToolRegistry
        MCP 调用流对齐的外壳格式（{\"name\": \"read\", \"arguments\": {...}}），
        子进程插件即可无缝接 mcp_proxy stdio transport。此改动此前无测试覆盖，
        本测试锁定语义防回归。
        """
        r = run_plugin_subprocess(
            f"{TOOLS_DIR}/read/manifest.plugin.json",
            "execute",
            {
                "name": "read",
                "arguments": {
                    "path": "lingclaude/plugins/tools/read/plugin.py",
                    "offset": 1,
                    "limit": 2,
                },
            },
        )
        assert r["ok"] is True, r.get("error")
        inner = r["data"]
        assert inner["ok"] is True
        assert "lines" in inner["data"]
