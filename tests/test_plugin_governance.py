"""plugin_governance 单测（M3 blocklist + install scope，2026-10-02）。

分两层：
- 核心函数层：monkeypatch policy_loader.get 直接喂策略 dict（不碰真 yaml）
- 接线层：三侧 loader 在治理拒绝时跳过装载（用最小 fake 探针）

hermetic：无真文件依赖（核心层），接线层 tmp_path。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lingclaude.core import plugin_governance as pg


def _feed_policy(monkeypatch, raw: dict) -> None:
    """把策略 dict 直接喂给核心层（绕开 policy_loader 文件读取）。"""
    monkeypatch.setattr(
        pg.policy_loader, "get", lambda _name: raw, raising=False
    )


# ── 缺省零分叉 ───────────────────────────────────────────────────

def test_policy_absent_allows_everything(monkeypatch):
    _feed_policy(monkeypatch, {})
    assert pg.check_file_allowed(Path("/x/anything.py")).allowed
    assert pg.check_manifest_entry("any", "core/plugins/a.py:C").allowed


# ── blocklist：文件 stem / 插件名，精确 + 通配 ────────────────────

def test_blocklist_exact_and_glob(monkeypatch):
    _feed_policy(monkeypatch, {
        "enabled": True,
        "blocklist": {"plugin_names": ["evil_hook", "*cred*"]},
    })
    v = pg.check_file_allowed(Path("/home/u/.lingclaude/hooks/evil_hook.py"))
    assert not v.allowed and "blocklist" in v.reason
    assert not pg.check_file_allowed(Path("/x/my_credentials_stealer.py")).allowed
    assert pg.check_file_allowed(Path("/x/benign.py")).allowed


def test_blocklist_on_manifest_name(monkeypatch):
    _feed_policy(monkeypatch, {
        "enabled": True,
        "blocklist": {"plugin_names": ["bad_plugin"]},
    })
    v = pg.check_manifest_entry("bad_plugin", "core/plugins/x.py:C")
    assert not v.allowed and "blocklist" in v.reason


# ── install scope：专属规则 / default deny / 坏 pattern 收紧 ──────

def test_scope_rule_match(monkeypatch):
    _feed_policy(monkeypatch, {
        "enabled": True,
        "install_scopes": {"default": "deny"},
        "scope_rules": [
            {"plugin": "good", "entry_patterns": ["core/plugins/*", "vendor/approved/*"]},
        ],
    })
    assert pg.check_manifest_entry("good", "core/plugins/reader.py:Reader").allowed
    assert pg.check_manifest_entry("good", "vendor/approved/x.py:X").allowed
    assert not pg.check_manifest_entry("good", "/tmp/evil.py:Evil").allowed


def test_scope_default_deny_without_rule(monkeypatch):
    _feed_policy(monkeypatch, {
        "enabled": True,
        "install_scopes": {"default": "deny"},
    })
    v = pg.check_manifest_entry("unlisted", "core/plugins/a.py:C")
    assert not v.allowed and "default=deny" in v.reason


def test_scope_default_allow_unlisted(monkeypatch):
    _feed_policy(monkeypatch, {
        "enabled": True,
        "install_scopes": {"default": "allow"},
        "scope_rules": [{"plugin": "pinned", "entry_patterns": ["pinned/*"]}],
    })
    assert pg.check_manifest_entry("other", "anywhere/x.py:X").allowed
    assert not pg.check_manifest_entry("pinned", "elsewhere/x.py:X").allowed


def test_bad_default_value_tightens(monkeypatch):
    _feed_policy(monkeypatch, {
        "enabled": True,
        "install_scopes": {"default": "yes-please"},
    })
    assert not pg.check_manifest_entry("x", "core/a.py:C").allowed


# ── fail-safe：检查异常 → deny ───────────────────────────────────

def test_deny_on_error(monkeypatch):
    def boom(_p):
        raise RuntimeError("治理炸了")

    v = pg.deny_on_error(boom, Path("/x/a.py"))
    assert not v.allowed and "异常" in v.reason


def test_load_policy_raises_upward(monkeypatch):
    """policy_loader 基础设施故障向上抛（消费方 fail-safe deny），不吞。"""
    def broken(_name):
        raise RuntimeError("policy infra down")

    monkeypatch.setattr(pg.policy_loader, "get", broken, raising=False)
    with pytest.raises(RuntimeError):
        pg.load_policy()


# ── 接线层：hook_registry / slash loader / core loader ───────────

def test_hook_registry_blocks_listed_file(monkeypatch, tmp_path):
    from lingclaude.core import hook_registry

    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "evil_hook.py").write_text("def register(api):\n    pass\n", encoding="utf-8")
    _feed_policy(monkeypatch, {
        "enabled": True,
        "blocklist": {"plugin_names": ["evil_hook"]},
    })
    monkeypatch.setattr(hook_registry, "_LOAD_DONE", False)
    status = hook_registry.load_user_hooks(hooks_dir=hooks, force=True)
    assert "blocked" in status["evil_hook.py"]


def test_slash_loader_blocks_listed_file(monkeypatch, tmp_path):
    import lingclaude.cli.slash_plugin_loader as spl

    fake = tmp_path / "evilplug.py"
    fake.write_text("def register(add):\n    pass\n", encoding="utf-8")
    _feed_policy(monkeypatch, {
        "enabled": True,
        "blocklist": {"plugin_names": ["evilplug"]},
    })

    class FakeRegistry(dict):
        pass

    reg = FakeRegistry()
    monkeypatch.setattr(spl, "PLUGINS_DIR", tmp_path)
    import lingclaude.cli.commands as _cmds
    monkeypatch.setattr(_cmds, "SLASH_REGISTRY", reg)
    loaded = spl.load_slash_plugins()
    assert all("evilplug" not in c for c in loaded)  # 未注册即通过
    assert "/evilplug" not in reg


def test_core_loader_scope_deny(monkeypatch, tmp_path):
    from lingclaude.core.plugin_loader import PluginLoader
    from lingclaude.core.plugin_manifest import PluginManifest
    from lingclaude.core.seam import SeamRegistry

    _feed_policy(monkeypatch, {
        "enabled": True,
        "install_scopes": {"default": "deny"},
    })
    # entry 指向不存在文件——治理拒绝应先于文件存在性检查（接线在 validate 之后）
    from lingclaude.core.seam import SeamType

    m = PluginManifest(name="scope_test_plug", version="1.0.0",
                       type=SeamType.TOOL, entry="/nonexistent/path/x.py:X")
    loader = PluginLoader(registry=SeamRegistry)
    result = loader.load_plugin(m)
    assert not result.ok
    assert "插件治理拒绝" in result.error
