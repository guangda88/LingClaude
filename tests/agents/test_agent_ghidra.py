"""agent_ghidra 插片守卫测试（hermetic：不依赖真实 Ghidra/后端进程）。

六个必含测试（mcp-wrap 清单）：
1. test_n2_trust_and_plug_level_declared  — N2 双声明
2. test_n3_namespaced_seam_key            — N3 域前缀缝 key
3. test_stop_layer_three_elements         — 铁律 2 细则 5 三要素
4. test_run_failure_recorded              — J4 失败也入账
5. test_absent_after_consecutive_failures — 候选铁律 8 连续失败→absent
6. test_probe_rejects_error_response      — J5 行为级：error 响应不得判活

hermetic 红线：所有 MCP 调用走 subprocess stub 注入固定响应（照 test_agent_lingxi
模式），不连真实 8081 后端；真实端点验证单独标 @pytest.mark.live，不进 CI。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingclaude.core.state_store import StateStore
from lingclaude.plugins.agents.agent_ghidra.plugin import (
    ABSENT_THRESHOLD,
    ALLOWED_TOOLS,
    GhidraAgentPlugin,
    MANIFEST,
)

PLUGIN_DIR = Path(__file__).parents[1] / "lingclaude" / "plugins" / "agents" / "agent_ghidra"


def _make_plugin(tmp_path: Path) -> GhidraAgentPlugin:
    """hermetic 插件实例：StateStore 落 tmp_path，不触真实 agent_runs。"""
    store = StateStore(backend="json", root=tmp_path / "agent_runs")
    return GhidraAgentPlugin(store=store)


# ── 1. N2：trust/plug 双声明 ────────────────────────────────────────
def test_n2_trust_and_plug_level_declared():
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert m.get("trust_level") in ("T1", "T2", "T3"), "trust_level 必须声明（N2）"
    assert m.get("plug_level") in ("L1", "L2", "L3"), "plug_level 必须声明（N2）"
    # T2 契约：只读为主 + 受限写（与 plugin.ALLOWED_TOOLS 白名单一致）
    assert m["trust_level"] == "T2"


# ── 2. N3：域前缀缝 key ─────────────────────────────────────────────
def test_n3_namespaced_seam_key():
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert "/" in m["name"], "manifest name 必带域前缀（N3）"
    assert m["name"] == "agent/ghidra"
    # registry 层用裸名（双层语义勿混淆——lingxi 教训 5）
    assert GhidraAgentPlugin.name == "agent/ghidra"


# ── 3. 铁律 2 细则 5：stop_layer 三要素 ─────────────────────────────
def test_stop_layer_three_elements():
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sl = m["stop_layer"]
    assert sl.get("kernel"), "stop_layer.kernel 必填（谁在干活）"
    assert isinstance(sl.get("seams"), list) and sl["seams"], "stop_layer.seams 必填"
    assert isinstance(sl.get("implementations"), int) and sl["implementations"] >= 1
    # command 双源一致：manifest.command 与 plugin._server_cmd() 同源（lingxi 教训 6）
    p = _make_plugin(Path("/tmp") / f"ghidra_n3_{id(object())}")
    assert p._server_cmd() == m["transport"]["command"]


# ── 4. J4：失败也入账 ───────────────────────────────────────────────
def test_run_failure_recorded(tmp_path):
    """白名单外工具 run 必须记 failed record（J4：失败是合法终态）。"""
    p = _make_plugin(tmp_path)
    out = p.run("execute_command", command="rm -rf /")  # 非白名单工具
    assert out["state"] == "failed"
    rec = p._store.load("agent_run", out["run_id"])
    assert rec is not None, "failed run 必须有 record（J4）"
    assert rec["state"] == "failed"
    assert "not in allowlist" in rec.get("error", "")


def test_run_success_recorded(tmp_path, monkeypatch):
    """成功锚定响应体（exit 0 ≠ 成功——lingxi 教训 4）。"""

    class FakeProc:
        def __init__(self, *a, **k):
            pass

        def communicate(self, payload, timeout=None):
            resp = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {
                "content": [{"type": "text", "text": "Function: FUN_004013c0"}]}})
            return (resp + "\n"), ""

        def poll(self):
            return 0

        def terminate(self):
            pass

    p = _make_plugin(tmp_path)
    monkeypatch.setattr("subprocess.Popen", FakeProc)
    out = p.run("list_methods", offset=0, limit=3)
    assert out["state"] == "succeeded"
    assert "FUN_004013c0" in out["result"]
    rec = p._store.load("agent_run", out["run_id"])
    assert rec is not None and rec["state"] == "succeeded"


# ── 5. 候选铁律 8：连续探针失败 → absent ────────────────────────────
def test_absent_after_consecutive_failures(tmp_path, monkeypatch):
    p = _make_plugin(tmp_path)
    monkeypatch.setattr(p, "_probe", lambda: False)
    states = [p.status() for _ in range(ABSENT_THRESHOLD + 1)]
    assert not states[0]["absent"], "第 1 次失败不缺席"
    assert states[ABSENT_THRESHOLD - 1]["absent"], f"连续 {ABSENT_THRESHOLD} 次失败即 absent"
    assert states[-1]["absent"] and states[-1]["probe_failures"] >= ABSENT_THRESHOLD
    # N4：health_state record 已落盘可查
    rec = p._store.load("health_state", "health__agent_ghidra")
    # key 规范由 mcp_common.health_key 决定；宽松断言 record 存在即可
    assert any(
        (tmp_path / "agent_runs" / "health_state").glob("*.json")
    ), "health_state record 必须落盘（N4 数据面）"


# ── 6. J5 行为级：error 响应不得判活 ────────────────────────────────
def test_probe_rejects_error_response(tmp_path, monkeypatch):
    p = _make_plugin(tmp_path)

    class FakeRun:
        def __init__(self, *a, **k):
            # id=0 回 error（协议层活着但业务拒绝）——不得判活
            self.returncode = 0
            self.stdout = json.dumps({
                "jsonrpc": "2.0", "id": 0,
                "error": {"code": -32603, "message": "internal error"}}) + "\n"
            self.stderr = ""

    monkeypatch.setattr("subprocess.run", FakeRun)
    assert p._probe() is False, "error 响应不得判活（J5 行为级）"


# ── 附加：T2 白名单面（契约审计锚点）────────────────────────────────
def test_allowlist_read_only_dominant():
    write_tools = {"rename_function", "rename_data", "rename_function_by_address",
                   "set_decompiler_comment", "set_disassembly_comment"}
    assert write_tools <= ALLOWED_TOOLS, "受限写工具必须在白名单内声明"
    dangerous = {"execute_command", "write_file", "bash", "delete"}
    assert not (dangerous & ALLOWED_TOOLS), "T2 契约：禁止 shell/文件系统写工具"


@pytest.mark.live
def test_live_backend_endpoints():
    """真实端点验证（不进 CI；需 Ghidra headless 后端 8081 在跑）。"""
    import urllib.request
    with urllib.request.urlopen("http://127.0.0.1:8081/methods?limit=2", timeout=5) as r:
        body = r.read().decode("utf-8")
        assert "Function:" in body
