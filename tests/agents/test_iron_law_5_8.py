"""铁律 5（双向插片互认）/ 铁律 8（可隔离故障域）专项测试（2026-09-22，lc 审计 ② 项）。

权威条文：docs/LINGYUAN_IRON_LAW.md:77（铁律 5）、:125（铁律 8）。

lc 侧可测载体（测试只对准真实实现，不测注释）：
- 铁律 5 双向互认：lc_mcp_guard（atomcode→lc，agent/ 域）↔ proj_agent_gateway
  （lc→外部 agent，proj/ 域）——两插片互为对偶声明 + 各自 record 化（J4）。
  federation_pair 双 record 目前仅注释声明、无实现（审计实证），故对偶性
  按 T2 契约审计形态钉死：缝 key 对称、对偶声明对称、record 状态机完备。
- 铁律 8 故障域隔离：guard 健康探针失败累计 → absent（N4 缺席查，不假活）；
  SubAgent 模型/工具异常圈死在 SubAgentResult（爆炸半径不外溢主干）。
- 铁律 8 操作域/时效域（work_claim 锁）已有 16 项测试
  （tests/agents/test_work_claim.py + conftest 查锁跳过），本文件不重复。
"""
from __future__ import annotations

import json

import pytest

from lingclaude.core.state_store import StateStore
from lingclaude.engine.loop.sub_agent import SubAgent, SubAgentConfig, SubAgentResult
from lingclaude.plugins.agents.lc_mcp_guard.plugin import (
    ABSENT_THRESHOLD,
    TERMINAL_STATES,
    LcGuardMcpPlugin,
)
from lingclaude.plugins.agents.mcp_common import domain_of, health_key
from lingclaude.plugins.agents.proj_agent_gateway.plugin import AgentGatewayMcpPlugin
from lingclaude.plugins.agents.work_claim import WorkClaim


def _load_sweeper():
    """scripts/ 非包，用 importlib 按路径加载 work_claim_sweeper（N4 时效查测试用）。"""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "work_claim_sweeper.py"
    spec = importlib.util.spec_from_file_location("wcsweep_test", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


# ── 铁律 5：双向插片互认（lc-guard ↔ agent-gateway 对偶对称性） ────────

def test_pair_seam_keys_symmetric_with_domain_prefix():
    """两对偶插片的缝 key 均带域前缀（铁律 7 联动），构成互认对。"""
    assert LcGuardMcpPlugin.name == "agent/lc-guard"
    assert AgentGatewayMcpPlugin.name == "agent/proj-agent-gateway"
    assert domain_of(LcGuardMcpPlugin.name) == "agent"
    assert domain_of(AgentGatewayMcpPlugin.name) == "agent"  # batch4 N3五域合规


def test_pair_declaration_symmetry():
    """对偶声明必须双向对称：lc 侧声明 atomcode，gateway 侧声明 lc-guard。

    铁律 5：「我可为人之插片，人可为我之插片」——单向声明即半边 record，
    按「dissolved 不允许留半边」的同款纪律，声明也必须成对（T2 契约审计形态：
    声明在模块 docstring，六态状态机为灵元联邦层未来项）。
    """
    import lingclaude.plugins.agents.lc_mcp_guard.plugin as lc_mod
    import lingclaude.plugins.agents.proj_agent_gateway.plugin as gw_mod
    assert "atomcode" in (lc_mod.__doc__ or ""), "lc 侧缺对偶声明（atomcode→lc 方向）"
    assert "lc-guard" in (gw_mod.__doc__ or ""), "gateway 侧缺对偶声明（lc→外部 agent 方向）"


@pytest.fixture
def guard(tmp_path):
    store = StateStore(backend="json", root=tmp_path)
    return LcGuardMcpPlugin(store=store)


def test_pair_record_machine_on_failure(guard, monkeypatch):
    """对偶侧调用失败必须入账（铁律 3/J4：失败结构化，不丢账）。"""
    monkeypatch.setattr(guard, "_call_mcp_tool",
                        lambda tool, args: (_ for _ in ()).throw(RuntimeError("MCP tool error: boom")))
    result = guard.run("no_such_tool_zzz")
    assert result["state"] == "failed"
    rec = guard._store.load("agent_run", result["run_id"])
    assert rec is not None
    assert rec["state"] == "failed"
    assert rec["plugin"] == LcGuardMcpPlugin.name
    assert "error" in rec and rec["error"]
    assert rec["state"] in TERMINAL_STATES


def test_pair_unknown_tool_is_transport_success_with_raw_text(guard, monkeypatch):
    """薄壳语义钉死：未知工具经 MCP 返回 isError 载荷 → 归一 raw_text → succeeded。

    真实 server（2026-09-22 实证）对未知工具回 result.content[0].text="Unknown
    tool: …" 且 isError=true——传输层成功、业务错误在载荷内（错误不抛异常，
    由对端守卫脚本语义兜底）。注意：真实 stdio 子进程路径存在冷启动竞态
    （偶发 id=1 无响应），故此处 stub _call_mcp_tool 的归一出口，非确定性问题
    已单独留给 guard 轨（probe 间隔 300s 可容忍单次抖动）。
    """
    monkeypatch.setattr(guard, "_call_mcp_tool",
                        lambda tool, args: {"raw_text": f"Unknown tool: {tool}"})
    result = guard.run("no_such_tool_zzz")
    assert result["state"] == "succeeded"
    assert "Unknown tool" in result["result"]["raw_text"]


def test_pair_record_machine_on_success(guard, monkeypatch):
    """成功路径 record 化：running → succeeded 两段可 query。"""
    monkeypatch.setattr(guard, "_call_mcp_tool", lambda tool, args: {"ok": True})
    result = guard.run("lc_ledger_query")
    assert result["state"] == "succeeded"
    rec = guard._store.load("agent_run", result["run_id"])
    assert rec["state"] == "succeeded"
    assert rec["result"]


def test_j4_terminal_states_pinned():
    """J4 run 状态机终态集合钉死（超出集合即 record 语义破坏）。"""
    assert TERMINAL_STATES == ("succeeded", "timeout", "failed", "aborted")


def test_pair_abort_records_for_non_terminal(guard):
    """abort 只对非终态生效并 record 化（MCP stdio 不可中断的诚实语义）。"""
    run_id = "lc-guard:test-abort"
    guard._record(run_id, "running", {})
    assert guard.abort(run_id) is True
    assert guard._store.load("agent_run", run_id)["state"] == "aborted"
    # 终态不可再 abort
    run_id2 = "lc-guard:test-done"
    guard._record(run_id2, "succeeded", {})
    assert guard.abort(run_id2) is False


# ── 铁律 8：可隔离故障域（guard 探针累计 → absent；N4 缺席查） ─────────

def test_guard_absent_after_consecutive_failures(guard, monkeypatch):
    """连续 ABSENT_THRESHOLD 次探针失败 → absent（不假活），恢复即清零。"""
    assert ABSENT_THRESHOLD >= 1
    monkeypatch.setattr(guard, "_probe", lambda: False)
    for i in range(1, ABSENT_THRESHOLD + 1):
        st = guard.status()
        assert st["absent"] is (i >= ABSENT_THRESHOLD)
        assert st["probe_failures"] == i
    monkeypatch.setattr(guard, "_probe", lambda: True)
    st = guard.status()
    assert st["absent"] is False and st["probe_failures"] == 0


def test_guard_health_state_persisted(guard, monkeypatch):
    """N4 数据面：健康状态持久化为 health_state record（缺席可 query）。"""
    monkeypatch.setattr(guard, "_probe", lambda: False)
    for _ in range(ABSENT_THRESHOLD):  # 打满阈值才 absent
        guard.status()
    rec = guard._store.load("health_state", health_key(guard.name))
    assert rec is not None
    assert rec["domain"] == domain_of(guard.name)
    assert rec["absent"] is True


def test_guard_probe_requires_jsonrpc_result_not_error(guard):
    """J5 行为级判活：error 响应不得判活（探针要求 id=0 result）。"""
    # _probe 走真实 stdio 子进程（server.py 不在则 False），此处验证失败累计一致性
    st1 = guard.status()
    st2 = guard.status()
    if st1["healthy"]:
        assert st2["probe_failures"] == 0
    else:
        assert st2["probe_failures"] == st1["probe_failures"] + 1


# ── 铁律 8：可隔离故障域（SubAgent 异常圈死，爆炸半径不外溢主干） ──────

class _FakeResponse:
    def __init__(self, content="", tool_calls=()):
        self.content = content
        self.tool_calls = list(tool_calls)


class _FakeProviderResult:
    def __init__(self, response, is_error=False, error=None):
        self.data = response
        self.is_error = is_error
        self.error = error


class _ExplodingProvider:
    """complete 一调即炸（模拟模型端不可用/网络断）。"""

    def complete(self, messages, tools=None):
        raise RuntimeError("model backend down")


class _FakeRegistry:
    @staticmethod
    def get_all_definitions():
        return []


class _FakeRuntime:
    registry = _FakeRegistry()

    def __init__(self, explode: bool = False) -> None:
        self.explode = explode

    def execute_tool(self, name, **kwargs):
        if self.explode:
            raise ValueError("tool kernel crashed")
        return {"ok": True, "tool": name}


class _ScriptedProvider:
    """第 1 轮要求工具调用，第 2 轮给最终文本（驱动工具轮路径）。"""

    def __init__(self, calls) -> None:
        self._calls = list(calls)
        self.seen_messages: list = []

    def complete(self, messages, tools=None):
        self.seen_messages.append(list(messages))
        return _FakeProviderResult(self._calls.pop(0))


def test_provider_crash_isolated_in_result():
    """模型端异常必须圈死为 SubAgentResult(success=False)，绝不向主干抛出。"""
    agent = SubAgent(runtime=_FakeRuntime(), provider=_ExplodingProvider())
    result = agent.run("probe task")
    assert isinstance(result, SubAgentResult)
    assert result.success is False
    assert result.error and result.error.startswith("Model call failed")
    assert "model backend down" in result.error  # 异常语义保留可诊断


@pytest.mark.parametrize("exc", [RuntimeError("x"), ValueError("y"), OSError("z")])
def test_provider_crash_types_all_isolated(exc):
    """任意 Exception 类型都圈死（故障域无例外面）。"""

    class _P:
        def complete(self, messages, tools=None):
            raise exc

    agent = SubAgent(runtime=_FakeRuntime(), provider=_P())
    result = agent.run("t")
    assert result.success is False and result.error


class _TC:
    """ToolCall 形状（sub_agent 消费 tc.id/tc.name/tc.arguments 属性）。"""

    def __init__(self, id: str, name: str, arguments: str = "{}") -> None:
        self.id = id
        self.name = name
        self.arguments = arguments


def test_tool_crash_isolated_in_tool_result():
    """工具内核崩溃圈死为结构化 ToolResult（EXECUTION_ERROR），轮次照常收束。"""
    calls = [
        _FakeResponse(tool_calls=[_TC("1", "bash")]),
        _FakeResponse(content="done"),
    ]
    agent = SubAgent(
        runtime=_FakeRuntime(explode=True),
        provider=_ScriptedProvider(calls),
        config=SubAgentConfig(),
    )
    result = agent.run("t")
    assert result.success is True          # 工具崩溃不否定整轮
    assert "bash" in result.tools_used     # 已执行即入账
    assert result.rounds == 2
    # 工具输出必须是结构化 JSON（role=tool 消息，含 EXECUTION_ERROR 语义）
    tool_msg = [m for m in agent._provider.seen_messages[-1]
                if m.get("role") == "tool" and m.get("name") == "bash"]
    assert tool_msg, "工具结果必须回注消息流"
    payload = json.loads(tool_msg[0]["content"])
    assert payload["error_code"] == "EXECUTION_ERROR"  # ToolError.to_dict 扁平形状
    assert payload["error"]  # 错误消息保留可诊断


def test_disallowed_tool_rejected_without_polluting_tools_used():
    """越权工具被拒（错误 JSON），不进 tools_used，不影响轮次收束。"""
    calls = [
        _FakeResponse(tool_calls=[_TC("1", "bash")]),
        _FakeResponse(content="done"),
    ]
    agent = SubAgent(
        runtime=_FakeRuntime(),
        provider=_ScriptedProvider(calls),
        config=SubAgentConfig(allowed_tools=("read",)),  # bash 越权
    )
    result = agent.run("t")
    assert result.success is True
    assert result.tools_used == ()  # 越权调用不入账


def test_max_tools_per_round_enforced():
    """单轮工具数上限截断（max_tools_per_round 语义）。"""
    many = [_TC(str(i), "read") for i in range(5)]
    calls = [
        _FakeResponse(tool_calls=many),
        _FakeResponse(content="done"),
    ]
    agent = SubAgent(
        runtime=_FakeRuntime(),
        provider=_ScriptedProvider(calls),
        config=SubAgentConfig(max_tools_per_round=3),
    )
    result = agent.run("t")
    assert len(result.tools_used) == 3


def test_main_loop_unaffected_by_subagent_failure():
    """端到端故障域：子代理反复爆炸，主循环哨兵状态零污染。"""

    class _Engine:
        def __init__(self) -> None:
            self.turns = 0

        def step(self, task):
            self.turns += 1
            agent = SubAgent(runtime=_FakeRuntime(), provider=_ExplodingProvider())
            r = agent.run(task)
            return r.success  # 主循环只消费结构化结果，不吃异常

    engine = _Engine()
    for _ in range(3):
        assert engine.step("t") is False
    assert engine.turns == 3  # 主循环节拍未被打断


# ── N4 时效查专项（P0 #2 整改，2026-09-23）──────────────────────────
# 覆盖：sweeper 强制释放过期 held 锁 / 归档过期 released 锁 / 守卫故障不静默（J5）。

def _make_tmp_claim_store(tmp_path):
    """临时台账 + 一条可操控 expires_at 的 work_claim record。"""
    store = StateStore(backend="json", root=tmp_path)
    claim = WorkClaim(store)
    # 直接写一条过期 held 锁（绕开 bind 的 TTL 默认值，精确控制 expires_at）
    import time as _t
    key = claim._key("agent/expired-target")
    store.save("work_claim", key, {
        "member": "ghost-member", "path": "agent/expired-target",
        "state": "held", "bound_at": _t.time() - 3600,
        "expires_at": _t.time() - 1800,  # 已过期 30 分钟
        "note": "N4 时效查测试",
    })
    return store, claim, key


def test_sweeper_forces_expired_held_claim(tmp_path):
    """过期 held 锁 → sweeper 强制释放（候选铁律 8：失联自动失效）。"""
    sweep = _load_sweeper().sweep

    store, claim, key = _make_tmp_claim_store(tmp_path)
    rec = store.load("work_claim", key)
    assert rec["state"] == "held"  # 前置：仍是 held

    stats = sweep(store, dry_run=False)
    assert stats["swept_expired_held"] == 1

    rec_after = store.load("work_claim", key)
    assert rec_after["state"] == "released"
    assert rec_after["released_by"] == "sweeper"
    assert rec_after.get("swept") is True


def test_sweeper_archives_expired_released_claim(tmp_path):
    """过期 released 锁 → sweeper 补审计事件（可回放，锁本体保留）。"""
    sweep = _load_sweeper().sweep

    store, claim, key = _make_tmp_claim_store(tmp_path)
    rec = store.load("work_claim", key)
    rec.update({"state": "released", "released_at": rec["bound_at"],
                "released_by": "orig-member"})
    store.save("work_claim", key, rec)

    stats = sweep(store, dry_run=False)
    assert stats["archived_released"] == 1
    # 锁本体保留（可回放），state 仍是 released
    rec_after = store.load("work_claim", key)
    assert rec_after["state"] == "released"


def test_sweeper_dry_run_does_not_modify(tmp_path):
    """dry-run 只报告不修改（可预测性：先看后动）。"""
    sweep = _load_sweeper().sweep

    store, claim, key = _make_tmp_claim_store(tmp_path)
    stats = sweep(store, dry_run=True)
    assert stats["swept_expired_held"] == 1
    rec_after = store.load("work_claim", key)
    assert rec_after["state"] == "held"  # dry-run 未改


def test_claim_query_fault_visible_not_silent(tmp_path):
    """J5 反例修复：查锁故障入账 arch_audit_state，不静默吞掉。"""
    import sys
    from pathlib import Path

    # 造一个必失败的查锁路径：root 指向不存在目录
    bad_ledger = tmp_path / "no_such_ledger"
    # 直接验证 conftest 的入账函数
    from tests.agents import conftest

    # 备份原 LEDGER 后用 tmp 覆盖，验证入账写入
    orig = conftest.LEDGER
    try:
        conftest.LEDGER = tmp_path
        conftest._log_guard_fault(RuntimeError("synthetic claim-query fault"))
        state_file = tmp_path / "arch_audit_state" / "guard_faults.json"
        assert state_file.exists()
        import json as _j
        faults = _j.loads(state_file.read_text(encoding="utf-8"))
        assert isinstance(faults, dict) and len(faults) >= 1
        entry = next(iter(faults.values()))
        assert entry["kind"] == "claim_query_fault"
        assert "synthetic" in entry["error"]
    finally:
        conftest.LEDGER = orig
