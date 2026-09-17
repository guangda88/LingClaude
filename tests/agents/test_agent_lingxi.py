"""灵犀插片测试（Phase 1 试验田）——manifest schema + 协议 + record 化。

守卫锚点：
- N2（铁律 6）：trust_level/plug_level 必带，缺字段拒收；
- N3（铁律 7）：缝 key 必带域前缀；
- J4（铁律 3）：run 失败也必须入账（agent_run record）；
- 候选铁律 8：连续探针失败 → absent。
全部离线（不依赖真实 lingxi 进程），符合 CI hermetic 要求。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingclaude.plugins.agents.agent_lingxi.plugin import (
    ABSENT_THRESHOLD,
    LingxiAgentPlugin,
)

MANIFEST = json.loads(
    (Path(__file__).parents[2] / "lingclaude/plugins/agents/agent_lingxi/manifest.agent.json")
    .read_text(encoding="utf-8")
)


# ── N2 守卫：信任等级/拔插等级双声明 ────────────────────────────────────
def test_n2_trust_and_plug_level_declared():
    assert MANIFEST["trust_level"] in ("T1", "T2", "T3")
    assert MANIFEST["plug_level"] in ("L1", "L2", "L3")
    # 试验田按最严组合声明（用户裁定 lingxi=T1/L1）
    assert MANIFEST["trust_level"] == "T1"
    assert MANIFEST["plug_level"] == "L1"


# ── N3 守卫：域前缀缝 key ──────────────────────────────────────────────
def test_n3_namespaced_seam_key():
    assert MANIFEST["name"].startswith("agent/")
    assert LingxiAgentPlugin.name == MANIFEST["name"]


# ── 铁律 2 细则 5：stop_layer 三要素 ───────────────────────────────────
def test_stop_layer_three_elements():
    sl = MANIFEST["stop_layer"]
    assert sl["kernel"] and sl["seams"] and isinstance(sl["implementations"], int)


# ── J4：run 失败也必须入账（agent_run record 状态机）──────────────────
def test_run_failure_recorded(tmp_path):
    plugin = LingxiAgentPlugin.__new__(LingxiAgentPlugin)  # 跳过 __init__ 的真实路径
    plugin._manifest = MANIFEST
    from lingclaude.core.state_store import StateStore
    plugin._store = StateStore(backend="json", root=tmp_path)
    plugin._proc = None
    plugin._probe_failures = 0

    # 破坏传输命令 → _call_mcp 必然抛错 → run 必须记 failed（不吞账）
    plugin._manifest = dict(MANIFEST)
    plugin._manifest["transport"] = dict(MANIFEST["transport"], command=["false"])

    result = plugin.run("probe-failure-path")
    assert result["state"] == "failed"
    rec = plugin._store.load("agent_run", result["run_id"])
    assert rec is not None, "失败运行必须留 record（J4）"
    assert rec["state"] == "failed"


# ── 候选铁律 8：连续探针失败 → absent ─────────────────────────────────
def test_absent_after_consecutive_failures(tmp_path):
    plugin = LingxiAgentPlugin.__new__(LingxiAgentPlugin)
    plugin._manifest = dict(MANIFEST)
    from lingclaude.core.state_store import StateStore
    plugin._store = StateStore(backend="json", root=tmp_path)
    plugin._proc = None
    plugin._probe_failures = 0
    plugin._manifest = dict(MANIFEST, transport=dict(MANIFEST["transport"],
                                                     command=["false"]))

    for _ in range(ABSENT_THRESHOLD):
        st = plugin.status()
    assert st["absent"] is True and st["probe_failures"] >= ABSENT_THRESHOLD
    # 探针恢复 → absent 复位（可感知缺席，也可感知回归）
    plugin._manifest = MANIFEST  # 还原真实命令（探针仅 subprocess.run，失败返回 False 也不炸）
    st = plugin.status()
    assert st["probe_failures"] in (0, ABSENT_THRESHOLD + 1)  # 视本机 lingxi 是否可达


# ── 候选铁律 8 降级语义：absent 不假活 ──────────────────────────────────
def test_absent_plugin_reports_absent_not_healthy(tmp_path):
    plugin = LingxiAgentPlugin.__new__(LingxiAgentPlugin)
    plugin._manifest = dict(MANIFEST, transport=dict(MANIFEST["transport"],
                                                     command=["false"]))
    from lingclaude.core.state_store import StateStore
    plugin._store = StateStore(backend="json", root=tmp_path)
    plugin._proc = None
    plugin._probe_failures = ABSENT_THRESHOLD  # 预置：已连续失败

    st = plugin.status()
    assert st["absent"] is True


# ══ 探针债务清偿防回归（debt agent-lingxi-mcp-probe-timeout，20260917-21）══
def test_probe_payload_has_client_info_version():
    """根因 3 防回归：载荷带 clientInfo.version → 假 server 回 result → 判活。

    （cat 回显请求自身无 result 字段，不符合强化后的探针语义，故用条件应答件）
    """
    probe_server = [
        "import sys, json",
        "obj = json.loads(sys.stdin.readline())",
        "info = (obj.get('params') or {}).get('clientInfo') or {}",
        "if info.get('version'):",
        "    print(json.dumps({'jsonrpc': '2.0', 'id': obj.get('id'), 'result': {}}), flush=True)",
        "else:",
        "    print(json.dumps({'jsonrpc': '2.0', 'id': obj.get('id'), "
        "'error': {'code': -32603, 'message': 'missing clientInfo.version'}}), flush=True)",
    ]
    plugin = LingxiAgentPlugin.__new__(LingxiAgentPlugin)
    plugin._probe_failures = 0
    plugin._manifest = dict(MANIFEST, transport=dict(
        MANIFEST["transport"], command=["python3", "-c", "\n".join(probe_server)]))
    assert plugin._probe() is True  # 插片载荷带 version → 判活


def test_probe_rejects_error_response():
    """探针判定锚定 id=0 的 result 字段：error 响应（如缺 version 的 -32603）不得判活。"""
    err_resp = json.dumps({"jsonrpc": "2.0", "id": 0, "error": {"code": -32603}})
    script = f"printf '%s\\n' '{err_resp}'"
    plugin = LingxiAgentPlugin.__new__(LingxiAgentPlugin)
    plugin._manifest = dict(MANIFEST, transport=dict(MANIFEST["transport"],
                                                     command=["bash", "-c", script]))
    plugin._probe_failures = 0
    assert plugin._probe() is False


def test_extract_response_anchors_by_id_not_last_line():
    """J5 行为级：结果判定按请求 id 锚定，不被 initialize 回包（首行/其他行）冒充。"""
    out = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 0, "result": {"init": True}}),
        "非 JSON 噪声行",
        json.dumps({"jsonrpc": "2.0", "id": 1,
                    "result": {"content": [{"type": "text", "text": "ok"}]}}),
    ])
    assert LingxiAgentPlugin._extract_response(out, 1)["result"]["content"][0]["text"] == "ok"
    assert LingxiAgentPlugin._extract_response(out, 9) is None


def test_call_mcp_does_handshake_before_tools_call(tmp_path):
    """协议修正防回归：_call_mcp 必须先 initialize+initialized 再 tools/call。"""
    def fake_server(tmp_path):
        # 假 MCP server：按请求 id 回对应响应（initialize → {}，tools/call → done）
        lines = [
            "import sys, json",
            "for line in sys.stdin:",
            "    line = line.strip()",
            "    if not line:",
            "        continue",
            "    obj = json.loads(line)",
            "    if obj.get('id') == 0:",
            "        print(json.dumps({'jsonrpc': '2.0', 'id': 0, 'result': {}}), flush=True)",
            "    elif obj.get('id') == 1:",
            "        print(json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': "
            "{'content': [{'type': 'text', 'text': 'done'}]}}), flush=True)",
            "        break",
        ]
        p = tmp_path / "fake_server.py"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return ["python3", str(p)]

    # seen 通过文件带出（子进程内存不可见）
    plugin = LingxiAgentPlugin.__new__(LingxiAgentPlugin)
    plugin._manifest = dict(MANIFEST, transport=dict(MANIFEST["transport"],
                                                     command=fake_server(tmp_path)))
    from lingclaude.core.state_store import StateStore
    plugin._store = StateStore(backend="json", root=tmp_path)
    plugin._proc = None
    plugin._probe_failures = 0

    result = plugin.run("echo handshake-check")
    assert result["state"] == "succeeded"
    assert "done" in str(result["result"])


def test_run_state_transitions_are_j4_compliant(tmp_path):
    """铁律 3/J4：run 的 record 终态必须属于状态机合法集合（不发明新终态）。"""
    from lingclaude.plugins.agents.agent_lingxi.plugin import TERMINAL_STATES
    plugin = LingxiAgentPlugin.__new__(LingxiAgentPlugin)
    plugin._manifest = dict(MANIFEST, transport=dict(MANIFEST["transport"],
                                                     command=["false"]))
    from lingclaude.core.state_store import StateStore
    plugin._store = StateStore(backend="json", root=tmp_path)
    plugin._proc = None
    plugin._probe_failures = 0

    result = plugin.run("state-machine-check")
    assert result["state"] in TERMINAL_STATES
    rec = plugin._store.load("agent_run", result["run_id"])
    assert rec["state"] in TERMINAL_STATES


def test_manifest_command_matches_server_cmd():
    """manifest 固化命令与 _server_cmd() 一致（双源漂移防回归）。"""
    plugin = LingxiAgentPlugin.__new__(LingxiAgentPlugin)
    plugin._manifest = MANIFEST
    assert plugin._server_cmd() == MANIFEST["transport"]["command"]
    assert "--use-openssl-ca" in plugin._server_cmd()  # 根因 1 修复在位
    assert "npx" not in plugin._server_cmd()          # 根因 2（npx OOM）不回归



# ══ N4 缺席查·域级圈死（铁律 8 隔离故障域，2026-09-18 守卫升格）════════════
def _mk(tmp_path, command):
    """离线构造：跳过 __init__，注入 tmp 存储与指定传输命令（CI hermetic）。"""
    from lingclaude.core.state_store import StateStore
    p = LingxiAgentPlugin.__new__(LingxiAgentPlugin)
    p._manifest = dict(MANIFEST, transport=dict(MANIFEST["transport"], command=command))
    p._store = StateStore(backend="json", root=tmp_path)
    p._proc = None
    p._probe_failures = 0
    return p


def test_domain_health_quarantine_circles_failure(tmp_path):
    """域级圈死：域内任一 absent → 整域 quarantined；域外不受影响（不假活、不扩散）。"""
    from lingclaude.plugins.agents.agent_lingxi.plugin import (
        _domain_of, _health_key, query_domain_health)
    assert _domain_of("agent/lingxi") == "agent"  # 域=缝 key 域前缀（与铁律 7 同源）

    bad = _mk(tmp_path, ["false"])  # 探针必败
    for _ in range(ABSENT_THRESHOLD):
        st = bad.status()
    assert st["absent"] is True

    # 同域另一插片的健康 record（数据面按 schema 直写，模拟域内成员）
    store = bad._store
    store.save("health_state", _health_key("agent/other"), {
        "name": "agent/other", "domain": "agent",
        "healthy": True, "absent": False, "probe_failures": 0,
        "updated_at": 0.0})

    dom = query_domain_health(store, "agent", root=tmp_path)
    assert dom["absent"] == ["agent/lingxi"]
    assert "agent/other" in dom["healthy"]
    assert dom["quarantined"] is True  # 圈死：域内缺席即整域隔离

    out = query_domain_health(store, "tools", root=tmp_path)  # 域外不受影响
    assert out["quarantined"] is False and out["records"] == 0


def test_absent_recovery_rewrites_record_healthy(tmp_path):
    """可感知缺席亦可感知回归：探针恢复 → health record 翻写 healthy，域级圈死解除。"""
    from lingclaude.plugins.agents.agent_lingxi.plugin import query_domain_health
    probe_server = [
        "import sys, json",
        "obj = json.loads(sys.stdin.readline())",
        "print(json.dumps({'jsonrpc': '2.0', 'id': obj.get('id'), 'result': {}}), flush=True)",
    ]
    p = _mk(tmp_path, ["false"])
    for _ in range(ABSENT_THRESHOLD):
        p.status()
    # 探针恢复（假 server：恒回 result）
    p._manifest = dict(MANIFEST, transport=dict(
        MANIFEST["transport"], command=["python3", "-c", "\n".join(probe_server)]))
    st = p.status()
    assert st["healthy"] is True and st["absent"] is False and st["probe_failures"] == 0
    rec = p._store.load("health_state", "agent__lingxi")
    assert rec["healthy"] is True and rec["absent"] is False
    dom = query_domain_health(p._store, "agent", root=tmp_path)
    assert dom["quarantined"] is False and dom["healthy"] == ["agent/lingxi"]


def test_domain_health_no_records_is_unknown_not_healthy(tmp_path):
    """空域不臆断健康：无 record → records=0、healthy 为空、不圈死但不给健康结论。"""
    from lingclaude.core.state_store import StateStore
    from lingclaude.plugins.agents.agent_lingxi.plugin import query_domain_health
    store = StateStore(backend="json", root=tmp_path)
    dom = query_domain_health(store, "agent", root=tmp_path)
    assert dom["records"] == 0 and dom["healthy"] == [] and dom["absent"] == []
    assert dom["quarantined"] is False
