"""P3 SeamRegistry + PluginManifest + PluginLoader 测试。

验收口径（灵元 P3）：
- register/unregister 热拔插：注册即生效、注销后 get 抛错、幂等
- PluginManifest：schema 校验（合法/非法/from_dict/from_json）
- PluginLoader：importlib 动态加载真实插件文件 → SeamRegistry 注册
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingclaude.core.plugin_loader import PluginLoader
from lingclaude.core.plugin_manifest import PluginManifest, validate_manifest_dict
from lingclaude.core.seam import SandboxPlugin, SeamRegistry, SeamType


@pytest.fixture(autouse=True)
def _reset_registry():
    SeamRegistry.reset()
    yield
    SeamRegistry.reset()


# ---------------------------------------------------------------------------
# P3-1 SeamRegistry
# ---------------------------------------------------------------------------
class FakeTool:
    name = "fake_tool"

    def execute(self, *args, **kwargs):
        return "ok"


def test_register_get_unregister():
    tool = FakeTool()
    SeamRegistry.register(SeamType.TOOL, "fake_tool", tool)
    assert SeamRegistry.get(SeamType.TOOL, "fake_tool") is tool
    assert SeamRegistry.has(SeamType.TOOL, "fake_tool") is True
    assert "fake_tool" in SeamRegistry.list_names(SeamType.TOOL)

    assert SeamRegistry.unregister(SeamType.TOOL, "fake_tool") is True
    assert SeamRegistry.has(SeamType.TOOL, "fake_tool") is False
    with pytest.raises(KeyError):
        SeamRegistry.get(SeamType.TOOL, "fake_tool")


def test_register_overwrites_same_name():
    """同名注册 = 热更覆盖，已持引用不受影响。"""
    t1, t2 = FakeTool(), FakeTool()
    SeamRegistry.register(SeamType.TOOL, "t", t1)
    old_ref = SeamRegistry.get(SeamType.TOOL, "t")
    SeamRegistry.register(SeamType.TOOL, "t", t2)
    assert SeamRegistry.get(SeamType.TOOL, "t") is t2
    assert old_ref is t1  # 旧引用仍指向旧实例


def test_unregister_idempotent():
    assert SeamRegistry.unregister(SeamType.TOOL, "never_registered") is False
    SeamRegistry.register(SeamType.TOOL, "t", FakeTool())
    assert SeamRegistry.unregister(SeamType.TOOL, "t") is True
    assert SeamRegistry.unregister(SeamType.TOOL, "t") is False


def test_get_optional_returns_none():
    assert SeamRegistry.get_optional(SeamType.TOOL, "nope") is None


def test_empty_name_rejected():
    with pytest.raises(ValueError):
        SeamRegistry.register(SeamType.TOOL, "", FakeTool())


def test_protocol_check():
    """sandbox 插片检查 available/wrap 成员。"""
    class GoodSandbox:
        name = "good"
        def available(self): return True
        def wrap(self, command, **kw): return command

    class BadSandbox:
        name = "bad"

    assert SeamRegistry.check_protocol(SeamType.SANDBOX, GoodSandbox()) == []
    assert set(SeamRegistry.check_protocol(SeamType.SANDBOX, BadSandbox())) == {"available", "wrap"}
    # None 协议类型不检查
    assert SeamRegistry.check_protocol(SeamType.TRANSPORT, object()) == []


# ---------------------------------------------------------------------------
# P3-3 PluginManifest
# ---------------------------------------------------------------------------
def test_manifest_valid():
    m = PluginManifest(name="my-tool", version="1.0.0", type=SeamType.TOOL, entry="tools/my.py:MyTool")
    assert m.validate() == []
    d = m.to_dict()
    assert d["name"] == "my-tool"
    assert d["type"] == "tool"


def test_manifest_invalid_name_version():
    m = PluginManifest(name="Bad Name!", version="x", type=SeamType.TOOL, entry="a.py:A")
    errors = m.validate()
    assert len(errors) >= 2


def test_manifest_from_dict_and_json(tmp_path: Path):
    data = {
        "name": "my-plugin",
        "version": "2.3.4",
        "type": "sandbox",
        "entry": "plugins/sandbox.py:MySandbox",
        "description": "test",
        "requires": ["foo"],
        "enabled": True,
    }
    m = PluginManifest.from_dict(data)
    assert m.type is SeamType.SANDBOX
    assert m.requires == ("foo",)

    # JSON 往返
    m2 = PluginManifest.from_json(json.dumps(data))
    assert m2 == m

    # 文件
    p = tmp_path / "m.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    assert PluginManifest.from_file(p) == m


def test_manifest_schema_rejects_unknown_field():
    errors = validate_manifest_dict({"name": "a", "version": "1.0.0", "type": "tool",
                                     "entry": "a.py:A", "evil": 1})
    assert any("未知字段" in e for e in errors)


def test_manifest_schema_rejects_bad_type():
    errors = validate_manifest_dict({"name": "a", "version": "1.0.0", "type": "wat",
                                     "entry": "a.py:A"})
    assert any("type" in e for e in errors)


def test_manifest_missing_required():
    errors = validate_manifest_dict({"name": "a"})
    assert any("缺少必填字段" in e for e in errors)


# ---------------------------------------------------------------------------
# P3-2 PluginLoader（真实插件文件）
# ---------------------------------------------------------------------------
PLUGIN_SRC = '''\
"""测试插件：MyTool 是一个 tool 插片。"""
name = "my-tool"

class MyTool:
    name = "my-tool"
    def execute(self, *args, **kwargs):
        return "from-plugin:" + str(args)

create = MyTool
'''


def test_loader_loads_real_plugin_file(tmp_path: Path):
    plugin_file = tmp_path / "my_tool_plugin.py"
    plugin_file.write_text(PLUGIN_SRC, encoding="utf-8")

    m = PluginManifest(name="my-tool", version="1.0.0", type=SeamType.TOOL,
                       entry=f"{plugin_file}:MyTool")
    loader = PluginLoader()
    result = loader.load_plugin(m)

    assert result.is_ok
    assert result.instance.name == "my-tool"
    # 已注册进 SeamRegistry
    inst = SeamRegistry.get(SeamType.TOOL, "my-tool")
    assert inst is result.instance
    assert inst.execute("hello") == "from-plugin:('hello',)"


def test_loader_unload_hot_plug(tmp_path: Path):
    plugin_file = tmp_path / "p2.py"
    plugin_file.write_text(PLUGIN_SRC, encoding="utf-8")
    m = PluginManifest(name="my-tool", version="1.0.0", type=SeamType.TOOL,
                       entry=f"{plugin_file}:MyTool")
    loader = PluginLoader()
    assert loader.load_plugin(m).is_ok
    assert loader.unload_plugin(m) is True
    assert SeamRegistry.has(SeamType.TOOL, "my-tool") is False
    # 幂等
    assert loader.unload_plugin(m) is False


def test_loader_missing_file_returns_fail(tmp_path: Path):
    m = PluginManifest(name="ghost", version="1.0.0", type=SeamType.TOOL,
                       entry=f"{tmp_path}/nope.py:Ghost")
    loader = PluginLoader()
    result = loader.load_plugin(m)
    assert not result.is_ok
    assert "不存在" in result.error or "失败" in result.error


def test_loader_disabled_plugin_rejected(tmp_path: Path):
    m = PluginManifest(name="off", version="1.0.0", type=SeamType.TOOL,
                       entry="x.py:X", enabled=False)
    loader = PluginLoader()
    result = loader.load_plugin(m)
    assert not result.is_ok
    assert "禁用" in result.error


def test_loader_bad_manifest_rejected():
    m = PluginManifest(name="Bad!", version="x", type=SeamType.TOOL, entry="")
    loader = PluginLoader()
    result = loader.load_plugin(m)
    assert not result.is_ok
    assert "manifest 非法" in result.error


def test_loader_reload_same_name(tmp_path: Path):
    """同名重载 = 热更：新文件内容覆盖注册实例。"""
    f = tmp_path / "r.py"
    f.write_text("class T:\n    name='t'\n    def execute(self): return 'v1'\n", encoding="utf-8")
    m = PluginManifest(name="t", version="1.0.0", type=SeamType.TOOL, entry=f"{f}:T")
    loader = PluginLoader()
    r1 = loader.load_plugin(m)
    assert r1.is_ok and r1.instance.execute() == "v1"

    # 改文件 → 重载
    f.write_text("class T:\n    name='t'\n    def execute(self): return 'v2'\n", encoding="utf-8")
    r2 = loader.load_plugin(m)
    assert r2.is_ok and r2.instance.execute() == "v2"
    assert SeamRegistry.get(SeamType.TOOL, "t").execute() == "v2"
