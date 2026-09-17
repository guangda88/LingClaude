"""cap/infer 插片测试（Phase A 封灵元推理栈，复用 SeamType.PROVIDER）。

覆盖（16 测试，对齐 agent_lingxi 范式）：
1. manifest 双声明（trust_level=T2 + plug_level=L1，N2 守卫消费）
2. 缝 key 域前缀（cap/infer，N3 守卫消费）
3. plugins.yaml 配置驱动（读 enabled llama-server 本机端口）
4. on_demand 语义（未拉起=absent 不是 dead，不记 debt）
5. list_models 实时探活（healthy/absent/probe_failures 字段齐全）
6. create() 缺席语义（URLError → state=absent + on_demand=True，N4 缺席查）
7. create() 成功语义（record 化 infer_run，J4）
8. create() 失败语义（非网络异常 → state=failed，含 error 留痕）
9. abort() record 化（不杀引擎进程，_health_check 不杀进程原则）
10. status() 域级汇总（endpoints_total/healthy/absent/on_demand 字段齐全）
11. 隔离故障域（单端口 absent 不扩散到其他端口，铁律 8）
12. register() 走 SeamType.PROVIDER（L1 替换，unregister 后主干照跑）
13. plugins.yaml 不可用 → 全端口 absent（不假活）
14. 远端端点（ai01/10.0.0.12）不进 lc 主干（隔离故障域）
15. 禁用端点（enabled=false）不收编
16. 缺省配置（config=None）create 兜底

hermetic：注入 tmp_path 的 StateStore + monkeypatch urllib，不依赖真实 8106 端口。
"""
from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest
import yaml

from lingclaude.core.seam import SeamRegistry, SeamType
from lingclaude.core.state_store import StateStore


# ── fixtures ─────────────────────────────────────────────────────────────
@pytest.fixture()
def plugins_yaml(tmp_path: Path) -> str:
    """构造最小 plugins.yaml（3 本机 + 1 远端 + 1 禁用，覆盖 on_demand 语义）。"""
    p = tmp_path / "plugins.yaml"
    doc = {"plugins": [
        {"name": "qwen2.5-7b@8106", "engine": "llama-server",
         "url": "http://127.0.0.1:8106/v1", "model": "qwen2.5-7b", "enabled": True, "thinking": False},
        {"name": "qwen2.5-2b@8130", "engine": "llama-server",
         "url": "http://127.0.0.1:8130/v1", "model": "qwen2.5-2b", "enabled": True, "thinking": False},
        {"name": "holo-3.1-35b@8110", "engine": "llama-server",
         "url": "http://127.0.0.1:8110/v1", "model": "holo-3.1-35b", "enabled": True, "thinking": True},
        {"name": "ai01-qwen@8211", "engine": "llama-server",
         "url": "http://10.0.0.12:8211/v1", "model": "qwen", "enabled": True},
        {"name": "disabled@8104", "engine": "llama-server",
         "url": "http://127.0.0.1:8104/v1", "model": "x", "enabled": False},
    ]}
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(p)


@pytest.fixture()
def plugin(plugins_yaml: str, tmp_path: Path) -> "LingyuanInferPlugin":
    from lingclaude.plugins.agents.cap_infer.plugin import LingyuanInferPlugin
    store = StateStore(backend="json", root=tmp_path / "state")
    plg = LingyuanInferPlugin(store=store, plugins_yaml=plugins_yaml)
    plg._manifest["transport"]["config_source"] = plugins_yaml  # 覆盖默认路径
    plg._load_models()
    return plg


@pytest.fixture()
def real_plugin(plugins_yaml: str, tmp_path: Path) -> "LingyuanInferPlugin":
    """直接用 manifest 默认路径的插件（list_models 走真实 plugins.yaml 探活）。"""
    from lingclaude.plugins.agents.cap_infer.plugin import LingyuanInferPlugin
    store = StateStore(backend="json", root=tmp_path / "state2")
    plg = LingyuanInferPlugin(store=store, plugins_yaml=plugins_yaml)
    return plg


# ── 1. manifest 双声明（N2 守卫消费）──────────────────────────────────────
def test_manifest_trust_plug_declared():
    m = json.loads((Path(__file__).parents[2] / "lingclaude" / "plugins" / "agents"
                    / "cap_infer" / "manifest.agent.json").read_text(encoding="utf-8"))
    assert m["trust_level"] == "T2"
    assert m["plug_level"] == "L1"
    assert m["caller"] == "lingclaude"


# ── 2. 缝 key 域前缀（N3 守卫消费）────────────────────────────────────────
def test_seam_key_domain_prefix():
    from lingclaude.plugins.agents.cap_infer.plugin import LingyuanInferPlugin
    assert LingyuanInferPlugin.name == "cap/infer"
    assert LingyuanInferPlugin.name.split("/", 1)[0] == "cap"


# ── 3. plugins.yaml 配置驱动 ─────────────────────────────────────────────
def test_load_models_from_yaml(plugin):
    assert "qwen2.5-7b@8106" in plugin._endpoints
    assert "qwen2.5-2b@8130" in plugin._endpoints
    assert "holo-3.1-35b@8110" in plugin._endpoints


# ── 4. on_demand 语义（未拉起=absent 不是 dead）─────────────────────────
def test_on_demand_flag_set(plugin):
    for key, ep in plugin._endpoints.items():
        assert ep.get("on_demand") is True, f"{key} 未标 on_demand"


# ── 5. list_models 字段齐全 ─────────────────────────────────────────────
def test_list_models_fields(plugin, monkeypatch):
    # monkeypatch 探活全 False（hermetic，不依赖真实端口）
    monkeypatch.setattr(plugin, "_probe", lambda url: False)
    lines = plugin.list_models()
    assert len(lines) == 3  # 远端 ai01 + 禁用 8104 被排除
    for l in lines:
        for f in ("name", "url", "model", "healthy", "absent", "on_demand", "probe_failures"):
            assert f in l, f"list_models 缺字段 {f}"
        assert l["on_demand"] is True
        assert l["healthy"] is False
        # 首次探活：probe_failures=1（还没到 ABSENT_THRESHOLD=2）
        assert l["absent"] is False


# ── 6. create() 缺席语义（URLError → absent + on_demand）───────────────
def test_create_absent_on_urlerror(plugin, monkeypatch):
    def fake_call_openai(config, ep):
        raise urllib.error.URLError("Connection refused")
    monkeypatch.setattr(plugin, "_call_openai", fake_call_openai)
    monkeypatch.setattr(plugin, "_probe", lambda url: False)
    res = plugin.create({"name": "qwen2.5-7b@8106", "prompt": "hi"})
    assert res["state"] == "absent"
    assert res["on_demand"] is True
    assert "Connection refused" in res["error"]
    # N4 缺席查：故障累计
    assert plugin._probe_failures.get("qwen2.5-7b@8106") == 1
    # record 化入账（J4）
    rec = plugin._store.load("infer_run", res["run_id"])
    assert rec["state"] == "absent"
    assert rec["on_demand"] is True


# ── 7. create() 成功语义（record 化）────────────────────────────────────
def test_create_success_records(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "_call_openai", lambda c, ep: {"choices": [{"message": {"content": "ok"}}]})
    monkeypatch.setattr(plugin, "_probe", lambda url: True)
    res = plugin.create({"name": "qwen2.5-7b@8106", "prompt": "hi"})
    assert res["state"] == "succeeded"
    assert res["result"]["choices"][0]["message"]["content"] == "ok"
    rec = plugin._store.load("infer_run", res["run_id"])
    assert rec["state"] == "succeeded"
    assert "latency_s" in rec


# ── 8. create() 失败语义（非网络异常 → failed，含 error 留痕）─────────
def test_create_failed_records(plugin, monkeypatch):
    def boom(c, ep):
        raise ValueError("bad config")
    monkeypatch.setattr(plugin, "_call_openai", boom)
    monkeypatch.setattr(plugin, "_probe", lambda url: True)
    res = plugin.create({"name": "qwen2.5-7b@8106", "prompt": "hi"})
    assert res["state"] == "failed"
    assert "bad config" in res["error"]
    rec = plugin._store.load("infer_run", res["run_id"])
    assert rec["state"] == "failed"
    assert rec["error"] == "bad config"


# ── 9. abort() record 化（不杀引擎进程）────────────────────────────────
def test_abort_records(plugin, monkeypatch):
    # 用 monkeypatch 让 create 抛非网络异常 → state=failed（非终态，可 aborted）
    monkeypatch.setattr(plugin, "_call_openai", lambda c, ep: (_ for _ in ()).throw(ValueError("boom")))
    monkeypatch.setattr(plugin, "_probe", lambda url: True)
    res_create = plugin.create({"name": "qwen2.5-7b@8106", "prompt": "hi"})
    assert res_create["state"] == "failed"
    # 非终态 → aborted 可 record
    res = plugin.abort(res_create["run_id"])
    assert res is True
    rec = plugin._store.load("infer_run", res_create["run_id"])
    assert rec["state"] == "aborted"
    # 已终态（aborted）→ 不可再 aborted
    res2 = plugin.abort(res_create["run_id"])
    assert res2 is False


# ── 10. status() 域级汇总 ───────────────────────────────────────────────
def test_status_domain_summary(plugin):
    monkeypatch_probe(plugin, lambda url: False)
    st = plugin.status()
    assert st["name"] == "cap/infer"
    assert st["on_demand"] is True
    assert "endpoints_total" in st
    assert "healthy" in st
    assert "absent" in st
    # 全探活失败且未累计到阈值：healthy=[]，absent=[]（首次失败 < ABSENT_THRESHOLD）
    assert st["healthy"] == []


# ── 11. 隔离故障域（单端口 absent 不扩散）──────────────────────────────
def test_fault_isolation(plugin, monkeypatch):
    # 模拟：8106 探活失败累计到阈值 → 该端口 absent；8130 探活成功 → 不 absent
    monkeypatch.setattr(plugin, "_probe", lambda url: "8130" in url)  # 8130 通，8106 不通
    plugin._probe_failures["qwen2.5-7b@8106"] = 2  # 8106 累计到阈值
    lines = plugin.list_models()
    by = {l["name"]: l for l in lines}
    assert by["qwen2.5-7b@8106"]["absent"] is True
    assert by["qwen2.5-7b@8106"]["healthy"] is False
    # 故障域隔离：8130 探活成功，不受 8106 故障影响
    assert by["qwen2.5-2b@8130"]["absent"] is False
    assert by["qwen2.5-2b@8130"]["healthy"] is True
    assert by["qwen2.5-2b@8130"]["probe_failures"] == 0


# ── 12. register() 走 SeamType.PROVIDER（L1 替换）─────────────────────
def test_register_unregister_mainloop():
    """M5 截肢：unregister 后主干照跑（SeamRegistry 不感知 cap/infer）。"""
    import lingclaude.plugins.agents.cap_infer.plugin as cap_mod
    SeamRegistry.reset()
    cap_mod.register(SeamRegistry)
    try:
        got = SeamRegistry.get(SeamType.PROVIDER, "cap/infer")
        assert got.name == "cap/infer"
        # 主干不感知：unregister 后 get_optional 返 None，不崩
        SeamRegistry.unregister(SeamType.PROVIDER, "cap/infer")
        assert SeamRegistry.get_optional(SeamType.PROVIDER, "cap/infer") is None
        # 主干其他插片照跑
        SeamRegistry.register(SeamType.TOOL, "bash", object())
        assert SeamRegistry.has(SeamType.TOOL, "bash")
    finally:
        SeamRegistry.reset()


# ── 13. plugins.yaml 不可用 → 全端口 absent（不假活）─────────────────
def test_plugins_yaml_missing(plugin, tmp_path):
    plugin._plugins_yaml = str(tmp_path / "nope.yaml")
    plugin._load_models()
    assert plugin._endpoints == {}
    assert plugin._plugins_yaml_error is not None
    # status() 不崩，endpoints_total=0
    st = plugin.status()
    assert st["endpoints_total"] == 0


# ── 14. 远端端点不进 lc 主干（隔离故障域）──────────────────────────────
def test_remote_endpoint_excluded(plugin):
    # ai01 10.0.0.12 应被排除
    assert "ai01-qwen@8211" not in plugin._endpoints


# ── 15. 禁用端点不收编 ─────────────────────────────────────────────────
def test_disabled_endpoint_excluded(plugin):
    assert "disabled@8104" not in plugin._endpoints


# ── 16. 缺省配置 create 兜底 ────────────────────────────────────────────
def test_create_default_config(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "_call_openai", lambda c, ep: {"choices": []})
    monkeypatch.setattr(plugin, "_probe", lambda url: True)
    res = plugin.create(None)  # 缺省：取首个端点
    assert res["state"] == "succeeded"
    assert res["run_id"].startswith("infer:")


# ── 辅助 ───────────────────────────────────────────────────────────────
def monkeypatch_probe(plugin, fn):
    """统一 monkeypatch _probe 的便捷封装。"""
    import pytest as _pt
    _pt.MonkeyPatch().setattr(plugin, "_probe", fn)
