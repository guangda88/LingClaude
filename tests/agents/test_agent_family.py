"""灵字辈家族批量插片化契约测试（批1：MCP stdio 型 5 成员）。

守卫锚点（全离线 hermetic，不依赖真实成员进程）：
- N1 对账精神：manifest trust/plug 声明 == org_member 登记账本（组织事实源）；
- N2（铁律 6）：trust_level/plug_level 必带且值合法；
- N3（铁律 7）：缝 key 必带域前缀 agent/，plugin.name 与 manifest 同源；
- 铁律 2 细则 5：stop_layer 三要素；
- J4（铁律 3）：run 失败也必须入账（agent_run record）；
- 铁律 8：连续探针失败 → absent，健康落 health_state（domain=agent）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingclaude.core.state_store import StateStore
from lingclaude.plugins.agents.agent_family import (
    McpAgentPluginBase,
    load_org_member,
)
from lingclaude.plugins.agents.agent_lingan.plugin import LingAnAgentPlugin
from lingclaude.plugins.agents.agent_lingcreate.plugin import LingCreateAgentPlugin
from lingclaude.plugins.agents.agent_lingflow.plugin import LingFlowAgentPlugin
from lingclaude.plugins.agents.agent_lingminopt.plugin import LingMinoptAgentPlugin
from lingclaude.plugins.agents.agent_lingweb.plugin import LingWebAgentPlugin
from lingclaude.plugins.agents.agent_lingyang.plugin import LingYangAgentPlugin
from lingclaude.plugins.agents.agent_lingzhi.plugin import LingZhiAgentPlugin
from lingclaude.plugins.agents.proj_lingchu.plugin import ProjLingchuPlugin
from lingclaude.plugins.agents.proj_lingdai.plugin import ProjLingdaiPlugin
from lingclaude.plugins.agents.proj_lingkang.plugin import ProjLingkangPlugin
from lingclaude.plugins.agents.proj_linglv.plugin import ProjLinglvPlugin
from lingclaude.plugins.agents.proj_lingshang.plugin import ProjLingshangPlugin
from lingclaude.plugins.agents.proj_lingsheng.plugin import ProjLingshengPlugin
from lingclaude.plugins.agents.proj_lingshi.plugin import ProjLingshiPlugin
from lingclaude.plugins.agents.proj_lingyi.plugin import ProjLingyiPlugin
from lingclaude.plugins.agents.agent_lingmessage.plugin import LingMessageAgentPlugin
from lingclaude.plugins.agents.agent_lingresearch.plugin import LingResearchAgentPlugin
from lingclaude.plugins.agents.agent_lingtongask.plugin import LingTongaskAgentPlugin
from lingclaude.plugins.agents.agent_zhibridge.plugin import ZhibridgeAgentPlugin

REPO = Path(__file__).parents[2]
AG = REPO / "lingclaude/plugins/agents"

MCP_MEMBERS = {  # 批1：MCP stdio 型（真实传输路径）
    "lingmessage": LingMessageAgentPlugin,
    "lingresearch": LingResearchAgentPlugin,
    "lingtongask": LingTongaskAgentPlugin,
    "lingcreate": LingCreateAgentPlugin,
    "zhibridge": ZhibridgeAgentPlugin,
}

REGISTRY_MEMBERS = {  # 批2-4：登记型（载体在/未装/缺席，非 MCP 传输）
    "agent/lingan": LingAnAgentPlugin,
    "agent/lingflow": LingFlowAgentPlugin,
    "agent/lingminopt": LingMinoptAgentPlugin,
    "agent/lingweb": LingWebAgentPlugin,
    "agent/lingyang": LingYangAgentPlugin,
    "agent/lingzhi": LingZhiAgentPlugin,
    "proj/lingyi": ProjLingyiPlugin,
    "proj/lingkang": ProjLingkangPlugin,
    "proj/linglü": ProjLinglvPlugin,      # 缝 key=账本原样 proj/linglü；目录 ASCII 名 proj_linglv（ü→v）
    "proj/lingchu": ProjLingchuPlugin,
    "proj/lingdai": ProjLingdaiPlugin,
    "proj/lingshang": ProjLingshangPlugin,
    "proj/lingsheng": ProjLingshengPlugin,
    "proj/lingshi": ProjLingshiPlugin,
}
MEMBERS = {**MCP_MEMBERS, **REGISTRY_MEMBERS}



def _mid_dir(seam_or_mid: str) -> tuple[str, str]:
    """缝 key（agent/xxx 或 proj/xxx）或裸 mid → (mid, 插片目录名)。

    proj/linglü 的目录是 ASCII 名 proj_linglv（ü→v），manifest 缝 key 保持账本原样。
    """
    if "/" in seam_or_mid:
        dom, mid = seam_or_mid.split("/", 1)
    else:
        dom, mid = "agent", seam_or_mid
    # 目录名统一 <域>_<mid>：agent_ 与 proj_ 前缀都在（账本 12 agent/ + 8 proj/ 全量插片化）；
    # proj/linglü 例外（ü→v → proj_linglv），缝 key 保持账本原样 proj/linglü。
    return mid, f"{dom}_{mid.replace(chr(252), 'v')}"

def _manifest(seam_or_mid: str) -> dict:
    _mid, dirname = _mid_dir(seam_or_mid)
    return json.loads((AG / dirname / "manifest.agent.json").read_text(encoding="utf-8"))


def _make(mid: str, tmp_path: Path, *, cmd: list[str] | None = None):
    """构造插片实例（离线：可注入假命令），StateStore 指向 tmp。"""
    manifest = dict(_manifest(mid))
    if cmd is not None:
        manifest["transport"] = dict(manifest["transport"], command=cmd)
    (tmp_path / "m.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return MEMBERS[mid](tmp_path / "m.json",
                        store=StateStore(backend="json", root=tmp_path / "store"))


# ── N1 对账：manifest 声明 == org_member 登记账本 ──────────────────────
@pytest.mark.parametrize("seam_key", sorted(MEMBERS))
def test_manifest_matches_org_member_ledger(seam_key):
    # MEMBERS 键为缝 key 或裸 mid；账本文件名是裸 mid（lingan.json）——先转换
    mid, _dirname = _mid_dir(seam_key)
    org = load_org_member(mid)
    assert org, f"org_member/{mid}.json 必须存在（组织事实源）"
    mf = _manifest(seam_key)  # 缝 key 原样解析目录（proj 域成员不能退化为 agent_ 前缀）
    assert mf["name"] == org["plug_seam_key"], "缝 key 必须与账本一致"
    assert mf["trust_level"] == org["trust_level"], "trust 必须与账本一致"
    assert mf["plug_level"] == org["plug_level"], "plug 必须与账本一致"
    assert mf.get("org_member") == mid


# ── N2/N3 + stop_layer 三要素 ──────────────────────────────────────────
@pytest.mark.parametrize("mid", sorted(MEMBERS))
def test_manifest_schema_contract(mid):
    mf = _manifest(mid)
    assert mf["trust_level"] in ("T1", "T2", "T3")
    assert mf["plug_level"] in ("L1", "L2", "L3")
    assert mf["name"].split("/", 1)[0] in ("agent", "proj")  # 铁律 7：域前缀两域皆合法
    sl = mf["stop_layer"]
    assert sl["kernel"] and sl["seams"] and isinstance(sl["implementations"], int)
    if mf["transport"]["kind"] == "absent":
        # 缺席载体：capabilities 如实为空（铁律 8 第 1 条，与 absent_carriers 用例同口径）
        assert mf["capabilities"] == []
    else:
        assert mf["capabilities"], "capabilities 必须非空（实测载体工具清单）"
    assert mf["health_probe"]["absent_after"] >= 1


@pytest.mark.parametrize("mid", sorted(MEMBERS))
def test_plugin_name_from_manifest(mid, tmp_path):
    plugin = _make(mid, tmp_path)
    assert plugin.name == _manifest(mid)["name"]


# ── J4：run 失败也必须入账 ─────────────────────────────────────────────
@pytest.mark.parametrize("mid", sorted(MEMBERS))
def test_run_failure_recorded(mid, tmp_path):
    plugin = _make(mid, tmp_path, cmd=["false"])  # 破坏传输 → 必然失败
    result = plugin.run("ping", {})
    assert result["state"] == "failed"
    rec = plugin._store.load("agent_run", result["run_id"])
    assert rec is not None, "失败运行必须留 record（J4）"
    assert rec["state"] == "failed"
    assert rec["plugin"] == _manifest(mid)["name"]


# ── 铁律 8：连续探针失败 → absent + health_state 落账 ──────────────────
@pytest.mark.parametrize("mid", sorted(MEMBERS))
def test_absent_after_consecutive_failures(mid, tmp_path):
    plugin = _make(mid, tmp_path, cmd=["false"])
    threshold = _manifest(mid)["health_probe"]["absent_after"]
    for _ in range(threshold):
        st = plugin.status()
    assert st["absent"] is True and st["healthy"] is False
    rec = plugin._store.load("health_state", plugin.name.replace("/", "__"))
    assert rec is not None and rec["domain"] == plugin.name.split("/", 1)[0]  # agent/ 或 proj/
    assert rec["absent"] is True


def test_healthy_probe_flips_absent_off(tmp_path):
    """探针恢复 → 失败计数清零（解除圈死的数据面行为，批1 MCP 型锚定）。"""
    plugin = _make("lingmessage", tmp_path, cmd=["false"])
    for _ in range(2):
        plugin.status()
    # 换成必成功的假 server：回一行合法 initialize result
    fake = tmp_path / "fake_ok.py"
    fake.write_text(
        "import sys, json\n"
        "for line in sys.stdin:\n"
        "    obj = json.loads(line)\n"
        "    if obj.get('id') == 0:\n"
        "        print(json.dumps({'jsonrpc': '2.0', 'id': 0, 'result': {}}), flush=True)\n",
        encoding="utf-8")
    plugin._manifest = dict(plugin._manifest,
                            transport=dict(plugin._manifest["transport"],
                                           command=["python3", str(fake)]))
    st = plugin.status()
    assert st["healthy"] is True and st["absent"] is False
    assert st["probe_failures"] == 0


def test_abort_records_terminal_state(tmp_path):
    plugin = _make("lingmessage", tmp_path)
    run = plugin.run("x", {})  # 已失败终止
    assert plugin.abort(run["run_id"]) is False  # 终态不可再 abort


def test_family_base_is_shared_implementation():
    """单实现锚定：5 个成员类全部继承同一基类（禁复制范式）。"""
    for cls in MEMBERS.values():
        assert issubclass(cls, McpAgentPluginBase)


# ══ 批2-4：登记型插片（载体在/未装/缺席）════════════════════════════════

@pytest.mark.parametrize("seam_key", sorted(REGISTRY_MEMBERS))
def test_registry_member_transport_kind(seam_key, tmp_path):
    """登记型 transport.kind 必须如实声明（mcp 之外的 kind：调用必 failed 入账）。"""
    mf = _manifest(seam_key)  # 传缝 key（含域），_mid_dir 内部解析目录
    assert mf["transport"]["kind"] in ("library", "cli", "service", "platform", "absent")
    assert mf["name"] == seam_key


@pytest.mark.parametrize("seam_key", sorted(REGISTRY_MEMBERS))
def test_registry_member_run_fails_honest(seam_key, tmp_path):
    """非 MCP 型 run：不假活——必须 failed 且入账（J4），缺席载体同样留账。"""
    _mid, dirname = _mid_dir(seam_key)
    manifest_path = AG / dirname / "manifest.agent.json"
    mf = json.loads(manifest_path.read_text(encoding="utf-8"))
    plugin = REGISTRY_MEMBERS[seam_key](manifest_path,
                                        store=StateStore(backend="json", root=tmp_path / "s"))
    result = plugin.run("ping", {})
    assert result["state"] in ("failed", "timeout")  # 不允许 succeeded 假活
    rec = plugin._store.load("agent_run", result["run_id"])
    assert rec is not None and rec["state"] == result["state"]


def test_absent_carriers_are_honest():
    """载体缺席 5 成员（全在 proj/ 域）：capabilities 为空 + notes 声明缺席（铁律 8 第 1 条）。"""
    for seam_key in ("proj/lingchu", "proj/lingdai", "proj/lingshang",
                     "proj/lingsheng", "proj/lingshi"):
        mf = _manifest(seam_key)
        assert mf["capabilities"] == []
        assert "缺席" in mf["notes"]


def test_all_20_seam_keys_match_ledger():
    """全集对账：20 插片缝 key == org_member 账本 plug_seam_key（含既有批 lingxi）。"""
    org_keys = set()
    for jf in (REPO / "data/ling_org/org_member").glob("*.json"):
        org = json.loads(jf.read_text(encoding="utf-8"))
        if org.get("plug_seam_key"):
            org_keys.add(org["plug_seam_key"])
    assert len(org_keys) == 22  # 12 agent/ + 8 proj/ + 2（family-meeting/agent-gateway 对账补登记 2026-09-21）
    mine = {json.loads((AG / d / "manifest.agent.json").read_text(encoding="utf-8"))["name"]
            for d in AG.iterdir() if d.is_dir() and (AG / d / "manifest.agent.json").is_file()
            and (d.name.startswith("agent_") or d.name.startswith("proj_"))}
    # agent_lingxi 目录也在（既有批）→ 目录扫描覆盖全集，与账本一一对应
    assert org_keys == mine
    assert len(mine) == 22
