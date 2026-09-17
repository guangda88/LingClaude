"""lc_mcp_guard 薄壳测试（agent/lc-guard，铁律 5 双向互认：ac 调 lc 守卫面）。

覆盖（10 测试，对齐 mcp-wrap skill 四件套测试骨架 + 薄壳纪律）：
1. manifest N2 双声明（trust_level=T1 + plug_level=L1）
2. 缝 key 域前缀（agent/lc-guard，N3 守卫消费）
3. stop_layer 三要素齐（kernel/seams/implementations，铁律 2 细则 5）
4. MCP initialize 握手（id=0 回 result + serverInfo，error 响应不判活）
5. tools/list 全 6 工具（lc_audit_trigger/lc_audit_tasks/lc_ledger_query/
   lc_org_query/lc_law_read/lc_workclaim_query）
6. lc_ledger_query 查台账（列 key + 取单条 + 不存在报错结构）
7. lc_org_query 查灵族（列成员 + 取单条 + 模糊匹配拼音全称）
8. lc_law_read 读铁律（全文前 4000 + 锚点命中/未命中结构）
9. lc_audit_trigger 转发脚本（exit 结构化，薄壳不判业务）
10. 薄壳纪律（J1：server.py 无业务判断——grep 断言不含 if 状态==xx 则 yy）

hermetic：subprocess 注入固定载荷跑真实薄壳（薄壳自身是 lc 仓内代码，
可直跑，不依赖外部 atomcode 进程）；台账/灵族组织查真实文件（只读）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[2]
SERVER = REPO / "lingclaude" / "plugins" / "agents" / "lc_mcp_guard" / "server.py"
MANIFEST = REPO / "lingclaude" / "plugins" / "agents" / "lc_mcp_guard" / "manifest.agent.json"


def _mcp_call(payloads: list[dict], timeout: int = 30) -> list[dict]:
    """MCP stdio 多轮调用：发一串 JSON-RPC，回读全部响应行。

    注意（FastMCP stdio 单通道串行）：同一次子进程内连续多个 tools/call 只有
    首个响应（id=N）可靠；后续 id 无响应。故多工具调用场景用 _mcp_call_single
    每次单独拉起子进程。本函数只用于"单 tools/call + 多 list"的组合。
    """
    text = "\n".join(json.dumps(p) for p in payloads) + "\n"
    r = subprocess.run([sys.executable, str(SERVER)], input=text,
                       capture_output=True, text=True, timeout=timeout, cwd=str(REPO))
    out = []
    for line in r.stdout.strip().splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _mcp_call_single(tool_name: str, arguments: dict, timeout: int = 30) -> dict:
    """单次 tools/call：initialize → initialized → 单 call，返 call 响应。

    薄壳是子进程（MCP 单会话），多工具调用场景逐个拉起，避开单通道时序问题。
    返回 {tool_name, result_text(parsed), raw}。
    """
    payloads = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "ac-test", "version": "1.0.0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": tool_name, "arguments": arguments}},
    ]
    out = _mcp_call(payloads, timeout=timeout)
    resp = next((o for o in out if o.get("id") == 1), None)
    if resp is None:
        return {"tool_name": tool_name, "error": "no response for tools/call"}
    if "error" in resp:
        return {"tool_name": tool_name, "error": resp["error"]}
    text = resp["result"]["content"][0]["text"]
    try:
        return {"tool_name": tool_name, "data": json.loads(text), "raw_text": text}
    except json.JSONDecodeError:
        return {"tool_name": tool_name, "data": None, "raw_text": text}


# ── 1. manifest N2 双声明 ─────────────────────────────────────────────
def test_n2_trust_plug_declared():
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert m["trust_level"] in ("T1", "T2", "T3")
    assert m["plug_level"] in ("L1", "L2", "L3")
    assert m["trust_level"] == "T1"  # lc 仓代码在手，全审计
    assert m["plug_level"] == "L1"   # 薄壳可换其他实现，接口一致不崩


# ── 2. 缝 key 域前缀（N3）────────────────────────────────────────────
def test_n3_namespaced_seam_key():
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert "/" in m["name"], f"缝 key 必带域前缀（N3）: {m['name']}"
    assert m["name"].split("/", 1)[0] in ("core", "agent", "cap", "os", "hw")
    assert m["name"] == "agent/lc-guard"


# ── 3. stop_layer 三要素（铁律 2 细则 5）─────────────────────────────
def test_stop_layer_three_elements():
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sl = m.get("stop_layer", {})
    for f in ("kernel", "seams", "implementations"):
        assert f in sl, f"stop_layer 缺 {f}（铁律 2 细则 5 三要素）"
    assert sl["implementations"] >= 1


# ── 4. MCP initialize 握手（id=0 回 result，error 不判活）──────────
def test_mcp_initialize_handshake():
    payloads = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "ac-test", "version": "1.0.0"}}},
    ]
    out = _mcp_call(payloads)
    assert len(out) >= 1, "MCP initialize 无响应（薄壳未拉起）"
    resp = out[0]
    assert resp.get("id") == 0
    assert "result" in resp, f"initialize 返 error 不得判活: {resp}"
    assert "error" not in resp, f"initialize error 响应: {resp.get('error')}"
    si = resp["result"].get("serverInfo", {})
    assert si.get("name") == "lc-guard"


# ── 5. tools/list 全 6 工具 ─────────────────────────────────────────
def test_tools_list_six():
    payloads = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "ac-test", "version": "1.0.0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    ]
    out = _mcp_call(payloads)
    resp = next(o for o in out if o.get("id") == 1)
    tools = {t["name"] for t in resp["result"]["tools"]}
    expected = {"lc_audit_trigger", "lc_audit_tasks", "lc_ledger_query",
                "lc_org_query", "lc_law_read", "lc_workclaim_query"}
    assert tools == expected, f"tools 面不齐: {tools ^ expected}"


# ── 6. lc_ledger_query 查台账（单调用逐个拉起，FastMCP 单通道串行）────
def test_lc_ledger_query():
    # 列 key（arch_debt 存在且非空）
    r2 = _mcp_call_single("lc_ledger_query", {"ledger_type": "arch_debt"})
    assert "error" not in r2, r2
    d2 = r2["data"]
    assert d2["type"] == "arch_debt"
    assert d2["count"] > 0
    # 取单条（5 笔裁决债之一，已 resolved）
    r3 = _mcp_call_single("lc_ledger_query", {
        "ledger_type": "arch_debt",
        "key": "candzone-ironlaw8-faultdomain-arbitration.json"})
    d3 = r3["data"]
    assert d3["record"]["kind"] == "candzone_arbitration"
    assert d3["record"]["state"] == "resolved"
    # 不存在 → error 结构（薄壳结构化报错，不让 ac 猜）
    r4 = _mcp_call_single("lc_ledger_query", {"ledger_type": "nope"})
    d4 = r4["data"]
    assert "error" in d4
    assert "available" in d4


# ── 7. lc_org_query 查灵族（拼音全称模糊匹配）──────────────────────
def test_lc_org_query():
    r2 = _mcp_call_single("lc_org_query", {"member": "lingxi"})
    d2 = r2["data"]
    assert d2["record"]["name"] == "lingxi"
    assert d2["record"]["trust_level"] == "T1"
    r3 = _mcp_call_single("lc_org_query", {})
    d3 = r3["data"]
    assert d3["count"] > 0
    assert "lingxi" in d3["members"]


# ── 8. lc_law_read 读铁律 ───────────────────────────────────────────
def test_lc_law_read():
    r2 = _mcp_call_single("lc_law_read", {})
    d2 = r2["data"]
    assert d2["length"] > 1000
    assert "铁律" in d2["text"]
    # 锚点命中（文档含 "铁律 8：可隔离故障域"，带空格匹配）
    r3 = _mcp_call_single("lc_law_read", {"section": "铁律 8"})
    d3 = r3["data"]
    assert "section" in d3 and "text" in d3
    assert "可隔离故障域" in d3["text"] or "铁律 8" in d3["text"]
    # 锚点未命中 → error 结构（薄壳诚实报错 + hint）
    r4 = _mcp_call_single("lc_law_read", {"section": "不存在的锚点xyz"})
    d4 = r4["data"]
    assert "error" in d4 and "hint" in d4


# ── 9. lc_audit_trigger 转发脚本（exit 结构化，薄壳不判业务）──────
def test_lc_audit_trigger_forwards():
    r2 = _mcp_call_single("lc_audit_trigger", {}, timeout=60)
    assert "error" not in r2, r2
    d2 = r2["data"]
    assert "exit" in d2, f"lc_audit_trigger 未结构化返回 exit: {d2.keys()}"
    assert "stdout" in d2


# ── 10. 薄壳纪律（J1：无业务判断）──────────────────────────────────
def test_thin_shell_no_business_logic():
    """J1 薄壳纪律：server.py 不抄守卫 kernel，只转发。

    断言：无 'if .*state ==' / 'if .*absent ==' 这类业务判断
    （薄壳只转发 lc 对应实体，状态判断在 lc 守卫脚本里）。
    """
    src = SERVER.read_text(encoding="utf-8")
    # 薄壳允许的错误结构判断（"if not p.exists()" 等文件存在性检查是协议翻译）
    # 但禁止业务状态判断（如 if record['state'] == 'absent' 则 ...）
    import re
    forbidden = re.findall(r"if\s+\w+\[?'?state'?\]\s*==", src)
    assert not forbidden, f"薄壳抄了业务判断（J1 违例）: {forbidden}"
    # 薄壳必须有协议翻译（if p.exists() 等文件面判断合法）
    assert "if not p.exists()" in src or "if p.exists()" in src
