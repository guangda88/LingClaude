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


class TestPluginStdioWarm:
    """P2 (warm 试点): MCP stdio 服务模式（serve_plugin_stdio）。

    2026-09-14 补: 上轮 plugin_runner 新增 _PLUGIN_STDIO_ENTRY（行式 JSON-RPC 2.0
    stdio server），但存在 2 个真实缺陷（实测发现）：
      1) _main() 无参调用 vs 定义需要 manifest_path → NameError
      2) tools/call 把 name 也透传给 execute → FileReadTool.read() got
         unexpected keyword 'name'
    本测试锁定 stdio 模式端到端语义，防回归。
    """

    def _spawn(self, manifest_rel: str):
        import json
        import subprocess
        import sys

        cmd = [
            sys.executable, "-c",
            "from lingclaude.engine.plugin_runner import serve_plugin_stdio; "
            f"serve_plugin_stdio('{manifest_rel}')",
        ]
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )

        def send(req):
            proc.stdin.write(json.dumps(req) + "\n")
            proc.stdin.flush()
            return json.loads(proc.stdout.readline())

        return proc, send

    def test_initialize_and_list(self):
        """initialize → serverInfo.name；tools/list → 提供的能力名。"""
        proc, send = self._spawn(f"{TOOLS_DIR}/read/manifest.plugin.json")
        r1 = send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2024-11-05"}})
        assert r1["result"]["serverInfo"]["name"] == "read_plugin"
        r2 = send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = [t["name"] for t in r2["result"]["tools"]]
        assert "read" in names
        proc.terminate()

    def test_tools_call_only_passes_arguments(self):
        """tools/call 只透传 arguments（name 是路由信息，不进 execute 参数）。"""
        proc, send = self._spawn(f"{TOOLS_DIR}/read/manifest.plugin.json")
        r = send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "read",
            "arguments": {"path": "lingclaude/plugins/tools/read/plugin.py",
                          "offset": 1, "limit": 2},
        }})
        content = r["result"]["content"][0]
        assert content["type"] == "text"
        assert "lines" in content["text"]  # read 结果已 JSON 序列化
        assert "isError" not in r["result"]
        proc.terminate()

    def test_unknown_method_returns_error(self):
        """未知方法 → JSON-RPC 错误（-32601），不挂死。"""
        proc, send = self._spawn(f"{TOOLS_DIR}/read/manifest.plugin.json")
        r = send({"jsonrpc": "2.0", "id": 4, "method": "tools/unknown", "params": {}})
        assert r["error"]["code"] == -32601
        proc.terminate()

    def test_git_plugin_stdio_tools_list(self):
        """git 插件走 stdio：tools/list 暴露 6 个 git 能力。"""
        proc, send = self._spawn(f"{TOOLS_DIR}/git/manifest.plugin.json")
        r = send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = [t["name"] for t in r["result"]["tools"]]
        assert set(names) >= {"git_status", "git_diff", "git_push"}
        proc.terminate()
