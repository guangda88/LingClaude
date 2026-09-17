"""os/resource 四探针插片测试（Phase C，SeamType.RESOURCE，M5 截肢）。

覆盖（14 测试，对齐 agent_lingxi/cap_infer 范式）：
1. manifest 双声明（trust_level=T3 + plug_level=L3，N2 守卫消费）
2. 缝 key 域前缀（os/resource，N3 守卫消费）
3. list_probes 字段齐全（healthy/absent/data_preview）
4. probe() 单探针模式（指定 gpu/cpu/mem/disk 其一）
5. probe() 全四探针模式（dict 含 gpu/cpu/mem/disk 四 key）
6. probe_one 隔离故障域（单探针异常返 None，不影响其他探针）
7. cpu 探针（/proc/loadavg 实测，load1/load5/load15/cpus 字段）
8. mem 探针（/proc/meminfo 实测，mem_total_kb/mem_available_kb/mem_used_pct）
9. disk 探针（os.statvfs 实测，total_bytes/free_bytes/used_pct）
10. gpu 探针（nvidia-smi 可用 → gpus 列表；不可用 → None，T3 只观测）
11. status() 域级汇总（probes_total/healthy/absent，单探针 absent 不扩散）
12. record 化（resource_probe type，J4，含 probe/absent 字段）
13. register() 走 SeamType.RESOURCE（M5 截肢主干照跑，L3 缺席裸奔）
14. 未知探针名 probe_one → None（圈死，不崩）

hermetic：注入 tmp_path StateStore + monkeypatch 探针函数，不依赖真实硬件。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from lingclaude.core.seam import SeamRegistry, SeamType
from lingclaude.core.state_store import StateStore


@pytest.fixture()
def plugin(tmp_path: Path) -> "ResourceProbePlugin":
    from lingclaude.plugins.agents.os_resource.plugin import ResourceProbePlugin
    store = StateStore(backend="json", root=tmp_path / "state")
    return ResourceProbePlugin(store=store)  # probe=None → 全四探针


@pytest.fixture()
def plugin_gpu(tmp_path: Path) -> "ResourceProbePlugin":
    from lingclaude.plugins.agents.os_resource.plugin import ResourceProbePlugin
    store = StateStore(backend="json", root=tmp_path / "state_gpu")
    return ResourceProbePlugin(store=store, probe="gpu")


# ── 1. manifest 双声明（N2 守卫消费）────────────────────────────────────
def test_manifest_trust_plug_declared():
    m = json.loads((Path(__file__).parents[2] / "lingclaude" / "plugins" / "agents"
                    / "os_resource" / "manifest.agent.json").read_text(encoding="utf-8"))
    assert m["trust_level"] == "T3"
    assert m["plug_level"] == "L3"
    assert m["caller"] == "lingclaude"


# ── 2. 缝 key 域前缀（N3 守卫消费）──────────────────────────────────────
def test_seam_key_domain_prefix():
    from lingclaude.plugins.agents.os_resource.plugin import ResourceProbePlugin
    assert ResourceProbePlugin.name == "os/resource"
    assert ResourceProbePlugin.name.split("/", 1)[0] == "os"


# ── 3. list_probes 字段齐全 ────────────────────────────────────────────
def test_list_probes_fields(plugin):
    lines = plugin.list_probes()
    assert len(lines) == 4
    names = [l["name"] for l in lines]
    assert names == ["gpu", "cpu", "mem", "disk"]
    for l in lines:
        for f in ("name", "healthy", "absent", "data_preview"):
            assert f in l, f"list_probes 缺字段 {f}"
        # 互斥：healthy 与 absent 不会同真
        assert not (l["healthy"] and l["absent"])


# ── 4. probe() 单探针模式 ─────────────────────────────────────────────
def test_probe_single_mode(plugin, monkeypatch):
    """指定单探针（mem）→ 返 mem 数据 dict，不含其他探针。"""
    p = _make_single(plugin._store, "mem")
    data = p.probe()
    # 实测 /proc/meminfo 可用（Linux）
    assert data is not None
    assert "mem_total_kb" in data
    assert "mem_available_kb" in data
    # 单探针模式只采自己，不含 gpu/cpu/disk
    assert "gpus" not in data
    assert "load1" not in data


def _make_single(store, probe):
    from lingclaude.plugins.agents.os_resource.plugin import ResourceProbePlugin
    return ResourceProbePlugin(store=store, probe=probe)


# ── 5. probe() 全四探针模式 ───────────────────────────────────────────
def test_probe_all(plugin, monkeypatch):
    data = plugin.probe()
    assert data is not None
    for k in ("gpu", "cpu", "mem", "disk"):
        assert k in data


# ── 6. probe_one 隔离故障域（单探针异常返 None，不影响其他）─────────
def test_probe_one_isolation(plugin, monkeypatch):
    # 模拟 gpu 探针抛异常 → 返 None（圈死），其他探针正常
    def boom():
        raise OSError("gpu probe boom")
    monkeypatch.setattr(plugin, "_probe_gpu", boom)
    assert plugin.probe_one("gpu") is None
    # cpu 探针不受 gpu 故障影响
    cpu = plugin.probe_one("cpu")
    assert cpu is not None
    assert "load1" in cpu


# ── 7. cpu 探针实测 ───────────────────────────────────────────────────
def test_probe_cpu(plugin):
    data = plugin.probe_one("cpu")
    assert data is not None
    assert data["cpus"] >= 1
    for k in ("load1", "load5", "load15"):
        assert k in data
        assert isinstance(data[k], float)


# ── 8. mem 探针实测 ───────────────────────────────────────────────────
def test_probe_mem(plugin):
    data = plugin.probe_one("mem")
    assert data is not None
    assert data["mem_total_kb"] > 0
    assert data["mem_available_kb"] >= 0
    assert 0 <= data["mem_used_pct"] <= 100


# ── 9. disk 探针实测 ──────────────────────────────────────────────────
def test_probe_disk(plugin):
    data = plugin.probe_one("disk")
    assert data is not None
    assert data["total_bytes"] > 0
    assert data["free_bytes"] >= 0
    assert 0 <= data["used_pct"] <= 100


# ── 10. gpu 探针（nvidia-smi 可用/不可用 → T3 只观测）───────────────
def test_probe_gpu_available(plugin, monkeypatch):
    # 模拟 nvidia-smi 返回正常 CSV
    fake_stdout = ("0, NVIDIA GeForce GTX 1660 Ti, 3357, 6144, 0\n")
    with patch("lingclaude.plugins.agents.os_resource.plugin.shutil.which",
               return_value="/usr/bin/nvidia-smi"), \
         patch("lingclaude.plugins.agents.os_resource.plugin.subprocess.run") as m:
        m.return_value = type("R", (), {"returncode": 0, "stdout": fake_stdout, "stderr": ""})()
        data = plugin.probe_one("gpu")
        assert data is not None
        assert data["count"] == 1
        assert data["gpus"][0]["name"] == "NVIDIA GeForce GTX 1660 Ti"
        assert data["gpus"][0]["util_pct"] == 0


def test_probe_gpu_absent_when_no_nvidia_smi(plugin, monkeypatch):
    # nvidia-smi 不存在 → None（absent，T3 只观测，不崩）
    monkeypatch.setattr("lingclaude.plugins.agents.os_resource.plugin.shutil.which",
                       lambda x: None)
    data = plugin.probe_one("gpu")
    assert data is None


# ── 11. status() 域级汇总（单探针 absent 不扩散）────────────────────
def test_status_domain_summary(plugin, monkeypatch):
    # 模拟：cpu/mem/disk 正常，gpu absent（nvidia-smi 无）
    monkeypatch.setattr(plugin, "_probe_gpu", lambda: None)
    st = plugin.status()
    assert st["name"] == "os/resource"
    assert st["probe"] == "all"
    assert st["probes_total"] == 4
    assert "gpu" in st["absent"]
    # 其他探针不受 gpu absent 影响（隔离故障域）
    assert "cpu" not in st["absent"]
    assert "mem" not in st["absent"]
    assert "disk" not in st["absent"]


# ── 12. record 化（J4，resource_probe type）────────────────────────
def test_record_written(plugin, tmp_path):
    plugin.probe()
    keys = plugin._store.list_keys("resource_probe")
    assert len(keys) >= 1
    rec = plugin._store.load("resource_probe", keys[0])
    assert rec["plugin"] == "os/resource"
    assert rec["state"] in ("ok", "absent")
    assert "transited_at" in rec


# ── 13. register() 走 SeamType.RESOURCE（M5 截肢主干照跑）─────────
def test_register_unregister_mainloop():
    """M5 截肢：unregister 后主干照跑（L3 缺席裸奔，主干零依赖资源探针功能）。"""
    import lingclaude.plugins.agents.os_resource.plugin as os_res_mod
    SeamRegistry.reset()
    os_res_mod.register(SeamRegistry)
    try:
        got = SeamRegistry.get(SeamType.RESOURCE, "os/resource")
        assert got.name == "os/resource"
        # 协议检查通过
        missing = SeamRegistry.check_protocol(SeamType.RESOURCE, got)
        assert missing == []
        # 主干不感知：unregister 后 get_optional 返 None，不崩
        SeamRegistry.unregister(SeamType.RESOURCE, "os/resource")
        assert SeamRegistry.get_optional(SeamType.RESOURCE, "os/resource") is None
        # 主干其他插片照跑
        SeamRegistry.register(SeamType.TOOL, "bash", object())
        assert SeamRegistry.has(SeamType.TOOL, "bash")
    finally:
        SeamRegistry.reset()


# ── 14. 未知探针名 → None（圈死，不崩）──────────────────────────────
def test_unknown_probe_returns_none(plugin):
    data = plugin.probe_one("nonexistent_probe")
    assert data is None


# ── 辅助 fixture ─────────────────────────────────────────────────────
@pytest.fixture()
def plugin_cpu(tmp_path: Path) -> "ResourceProbePlugin":
    from lingclaude.plugins.agents.os_resource.plugin import ResourceProbePlugin
    store = StateStore(backend="json", root=tmp_path / "state_cpu")
    return ResourceProbePlugin(store=store, probe="cpu")
