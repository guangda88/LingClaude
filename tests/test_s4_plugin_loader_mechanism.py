"""S4 契约测试：PluginLoader 从「库」变「机制」（灵元：新增插件 = 加 manifest 文件）。

- load_plugins_from_dir：扫描 *.plugin.json 批量加载，失败隔离，目录缺失 fail-soft
- wiring.assemble 接入：引擎装配必经路径触发批量加载（生产调用点）
- 机制端到端：加 manifest → SeamRegistry 可查 → 热拔插生效
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lingclaude.core.seam import SeamRegistry, SeamType

PLUGIN_BODY = '''class EchoTool:
    name = "echo"
    def execute(self, text: str = "") -> str:
        return f"[plugin-echo:{text}]"
'''


@pytest.fixture(autouse=True)
def _clean_registry():
    SeamRegistry.reset()
    yield
    SeamRegistry.reset()


def _write_manifest(dir_path: Path, name: str, entry: str) -> Path:
    mf = dir_path / f"{name}.plugin.json"
    mf.write_text(
        '{"name": "%s", "version": "1.0.0", "type": "tool", "entry": "%s", '
        '"description": "s4 test"}' % (name, entry),
        encoding="utf-8",
    )
    return mf


def test_load_plugins_from_dir_batch(tmp_path: Path):
    """目录扫描批量加载：每个 manifest 一个插件，注册进 SeamRegistry。"""
    plugin_file = tmp_path / "echo_tool.py"
    plugin_file.write_text(PLUGIN_BODY, encoding="utf-8")
    _write_manifest(tmp_path, "echo", f"{plugin_file}:EchoTool")

    from lingclaude.core.plugin_loader import PluginLoader

    loader = PluginLoader()
    results = loader.load_plugins_from_dir(tmp_path)
    assert "echo" in results
    assert results["echo"].is_ok
    # 注册进 SeamRegistry，消费面可查
    inst = SeamRegistry.get_optional(SeamType.TOOL, "echo")
    assert inst is not None
    assert inst.execute(text="hi") == "[plugin-echo:hi]"


def test_load_plugins_from_dir_missing_dir(tmp_path: Path):
    """目录不存在 → 返回 {}（fail-soft，引擎启动无插件目录不报错）。"""
    from lingclaude.core.plugin_loader import PluginLoader

    loader = PluginLoader()
    assert loader.load_plugins_from_dir(tmp_path / "nope") == {}


def test_load_plugins_from_dir_bad_manifest_isolated(tmp_path: Path):
    """坏 manifest 不影响其他插件（失败隔离）。"""
    plugin_file = tmp_path / "echo_tool.py"
    plugin_file.write_text(PLUGIN_BODY, encoding="utf-8")
    _write_manifest(tmp_path, "echo", f"{plugin_file}:EchoTool")
    # 坏 manifest
    (tmp_path / "bad.plugin.json").write_text("{not json", encoding="utf-8")

    from lingclaude.core.plugin_loader import PluginLoader

    loader = PluginLoader()
    results = loader.load_plugins_from_dir(tmp_path)
    assert results["echo"].is_ok
    # 坏 manifest 用 stem 作 key（bad.plugin.json → "bad.plugin"）且失败隔离
    assert not results["bad.plugin"].is_ok  # 隔离失败，不抛异常


def test_wiring_assemble_triggers_plugin_load(tmp_path: Path, monkeypatch):
    """wiring.assemble 接入：装配必经路径触发批量加载（生产调用点）。"""
    plugin_file = tmp_path / "echo_tool.py"
    plugin_file.write_text(PLUGIN_BODY, encoding="utf-8")
    _write_manifest(tmp_path, "echo", f"{plugin_file}:EchoTool")

    # monkeypatch wiring._load_plugins_if_present 的目录指向 tmp_path
    import lingclaude.core.wiring as wiring

    # 直接验证 wiring.assemble 会调用插件加载（monkeypatch 目录 + 记录）
    calls: list[str] = []
    orig = wiring._load_plugins_if_present

    def _spy():
        calls.append("called")
        # 用 tmp_path 的插件做真实加载，验证机制端到端
        from lingclaude.core.plugin_loader import PluginLoader

        PluginLoader().load_plugins_from_dir(tmp_path)

    monkeypatch.setattr(wiring, "_load_plugins_if_present", _spy)
    # 调用 assemble（空 manifest + 裸引擎）
    engine = type("E", (), {})()
    ctx = wiring.WiringContext(engine=engine, session_manager=None, provider=None)
    wired = wiring.assemble(ctx, manifest=())
    assert calls == ["called"]
    assert wired == []
    # 端到端：插件已注册，消费面可查
    assert SeamRegistry.get_optional(SeamType.TOOL, "echo") is not None


def test_j3_stop_layer_required_by_validation(tmp_path: Path):
    """J3（铁律 2 停层显式化）制度化：声明了就必须完整合法，缺失/非法拒绝入册。"""
    from lingclaude.core.plugin_loader import PluginLoader

    plugin_file = tmp_path / "echo_tool.py"
    plugin_file.write_text(PLUGIN_BODY, encoding="utf-8")
    loader = PluginLoader()

    # 1) 无 stop_layer → warning 不阻断（存量兼容），仍可加载
    mf = tmp_path / "echo_no_decl.plugin.json"
    mf.write_text(
        '{"name": "echo_no_decl", "version": "1.0.0", "type": "tool", '
        '"entry": "%s:EchoTool"}' % plugin_file,
        encoding="utf-8",
    )
    res = loader.load_plugins_from_dir(tmp_path)
    assert res["echo_no_decl"].is_ok  # 存量无声明不拒绝

    # 2) 声明了但格式非法（缺 kernel）→ 拒绝入册（fail fast）
    bad = tmp_path / "echo_bad.plugin.json"
    bad.write_text(
        '{"name": "echo_bad", "version": "1.0.0", "type": "tool", '
        '"entry": "%s:EchoTool", "stop_layer": {"seams": ["x"], "implementations": 1}}' % plugin_file,
        encoding="utf-8",
    )
    res = loader.load_plugins_from_dir(tmp_path)
    # 解析失败的 manifest 以 stem 作 key（bad.plugin.json → "echo_bad.plugin"）
    bad_key = next(k for k in res if "echo_bad" in k)
    assert not res[bad_key].is_ok
    assert "stop_layer" in res[bad_key].error

    # 3) 完整合法声明 → 加载成功
    good = tmp_path / "echo_good.plugin.json"
    good.write_text(
        '{"name": "echo_good", "version": "1.0.0", "type": "tool", '
        '"entry": "%s:EchoTool", "stop_layer": {"kernel": "execute 分派薄壳", '
        '"seams": ["engine/echo"], "implementations": 1}}' % plugin_file,
        encoding="utf-8",
    )
    res = loader.load_plugins_from_dir(tmp_path)
    assert res["echo_good"].is_ok


def test_j3_stop_layer_nested_form_accepted(tmp_path: Path):
    """复盘 2026-09-23：38c894e 将 6 份 manifest 迁为嵌套形态（stop_layer.sub_seams）
    但校验器只认扁平形态 → file_ops/ast 等插件整组加载失败（test_tool_plugins ERROR）。
    契约：扁平与嵌套两种停层形态均合法；盘上生产 manifest 必须全部通过校验。
    """
    import json as _json

    from lingclaude.core.plugin_loader import PluginLoader
    from lingclaude.core.plugin_manifest import validate_manifest_dict

    # 1) 嵌套形态（生产关单形态）合法且可加载
    plugin_file = tmp_path / "echo_plugin.py"
    plugin_file.write_text(PLUGIN_BODY, encoding="utf-8")
    nested = {
        "name": "echo_nested",
        "version": "1.0.0",
        "type": "tool",
        "entry": f"{plugin_file}:EchoTool",
        "stop_layer": {
            "kernel": "execute 分派薄壳",
            "sub_seams": {"seams": ["x.Seam"], "implementations": 1, "note": "单实现"},
        },
    }
    assert validate_manifest_dict(nested) == []

    mf_path = tmp_path / "echo_nested.plugin.json"
    mf_path.write_text(_json.dumps(nested), encoding="utf-8")

    res = PluginLoader().load_plugins_from_dir(tmp_path)
    assert res["echo_nested"].is_ok

    # 2) 扁平形态（5234c2a 原始约定）仍合法 —— 存量兼容
    flat = dict(nested, name="echo_flat", stop_layer={"kernel": "k", "seams": ["s"], "implementations": 1})
    assert validate_manifest_dict(flat) == []

    # 3) 盘上 6 份生产 manifest 全部通过校验（防数据/门禁再脱节）
    tools_dir = Path(__file__).resolve().parent.parent / "lingclaude" / "plugins" / "tools"
    manifests = sorted(tools_dir.glob("*/manifest.plugin.json"))
    assert len(manifests) == 6
    for mf in manifests:
        data = _json.loads(mf.read_text(encoding="utf-8"))
        errs = validate_manifest_dict(data)
        assert errs == [], f"{mf.name}: {errs}"
