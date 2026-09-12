"""lsp_registry 测试 — /lsp add 命令后端。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from lingclaude.engine.lsp_registry import (
    register, list_servers, remove, get_server, detect_lang,
    _CONFIG_PATH, _DEFAULT_SERVERS,
)


@pytest.fixture
def fresh_home(tmp_path, monkeypatch):
    """隔离 HOME 目录，避免污染用户配置。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


class TestLspRegistryDefaults:
    """默认内置映射。"""

    def test_default_servers(self, fresh_home):
        """默认内置 4 个：python/rust/typescript/go。"""
        servers = list_servers()
        assert len(servers) == 4
        langs = {s["lang"] for s in servers}
        assert langs == {"python", "rust", "typescript", "go"}
        for s in servers:
            assert s["default"] is True

    def test_get_server_default(self, fresh_home):
        """get_server 返回默认配置。"""
        py = get_server("python")
        assert py["command"] == "pylsp"
        assert py["default"] is True


class TestLspRegistryCustom:
    """自定义 LSP 配置。"""

    def test_register_custom(self, fresh_home):
        """注册自定义语言。"""
        reg = register("nix", "nil", ["--stdio"])
        assert reg["lang"] == "nix"
        assert reg["command"] == "nil"
        assert reg["args"] == ["--stdio"]
        assert reg["default"] is False

    def test_register_overrides_default(self, fresh_home):
        """自定义覆盖默认（保留但不再默认）。"""
        register("python", "pylsp-custom", ["--verbose"])
        py = get_server("python")
        assert py["command"] == "pylsp-custom"
        assert py["default"] is False

    def test_register_empty_lang_raises(self, fresh_home):
        """空语言名抛错。"""
        with pytest.raises(ValueError):
            register("", "cmd", [])

    def test_register_empty_command_raises(self, fresh_home):
        """空命令抛错。"""
        with pytest.raises(ValueError):
            register("lang", "", [])


class TestLspRegistryRemove:
    """删除 LSP 配置。"""

    def test_remove_custom(self, fresh_home):
        """删除自定义配置。"""
        register("test", "cmd", [])
        assert remove("test") is True
        assert get_server("test") is None

    def test_remove_default_blocked(self, fresh_home):
        """内置默认不可删。"""
        assert remove("python") is False
        # python 仍然存在（内置默认）
        assert get_server("python") is not None

    def test_remove_nonexistent(self, fresh_home):
        """不存在的语言返回 False。"""
        assert remove("nonexistent") is False


class TestLspRegistryPersistence:
    """持久化到 ~/.lingclaude/lsp_servers.json。"""

    def test_persistence(self, fresh_home):
        """注册后写入文件，重新加载仍存在。"""
        register("custom_lang", "custom_cmd", ["--arg1"])
        config_path = fresh_home / ".lingclaude" / "lsp_servers.json"
        assert config_path.exists()

        # 模拟新进程 — 重新读取
        servers = list_servers()
        custom = [s for s in servers if s["lang"] == "custom_lang"]
        assert len(custom) == 1
        assert custom[0]["command"] == "custom_cmd"
        assert custom[0]["args"] == ["--arg1"]

    def test_persistence_remove(self, fresh_home):
        """删除后文件更新。"""
        register("a", "cmd-a", [])
        register("b", "cmd-b", [])
        remove("a")

        import json
        config_path = fresh_home / ".lingclaude" / "lsp_servers.json"
        data = json.loads(config_path.read_text())
        assert "a" not in data
        assert "b" in data


class TestDetectLang:
    """语言检测（按文件扩展名）。"""

    @pytest.mark.parametrize("path,expected", [
        ("test.py", "python"),
        ("test.pyi", "python"),
        ("main.rs", "rust"),
        ("app.ts", "typescript"),
        ("app.tsx", "typescript"),
        ("main.go", "go"),
        ("unknown.txt", None),
        ("no_extension", None),
    ])
    def test_detect_lang(self, fresh_home, path, expected):
        """按扩展名映射语言。"""
        assert detect_lang(path) == expected


# ---------- P0-D: LSP check_server 握手验证 ----------

class TestCheckServer:
    """cc P0: /lsp 只注册不验证 → check_server 最小握手。"""

    def test_unregistered_lang_graceful(self, fresh_home):
        """无 server 配置时 graceful 降级。"""
        from lingclaude.engine.lsp_registry import check_server
        r = check_server("nonexistent_lang_xyz")
        assert r["ok"] is False
        assert "未注册" in r["error"]

    def test_command_not_installed(self, fresh_home, tmp_path):
        """server 命令不存在 → 明确报未安装（不卡死）。"""
        from lingclaude.engine.lsp_registry import check_server, register
        # 注册一个不存在的命令
        register("phantom", "/nonexistent/definitely-not-a-real-binary-xyz")
        r = check_server("phantom", workspace_root=str(tmp_path))
        assert r["ok"] is False
        assert "未安装" in r["error"]

    def test_fake_server_handshake_fails_cleanly(self, fresh_home, tmp_path):
        """假 server（启动即退出）→ 握手失败但 graceful 返回错误。"""
        import sys
        from lingclaude.engine.lsp_registry import check_server, register
        # 用 python 当"假 server"：读一行就退出（不响应 LSP 协议）
        register("fake", sys.executable, ["-c", "import sys; sys.stdin.readline()"])
        r = check_server("fake", workspace_root=str(tmp_path))
        assert r["ok"] is False
        assert r["error"]  # 有错误信息（超时/EOF）
