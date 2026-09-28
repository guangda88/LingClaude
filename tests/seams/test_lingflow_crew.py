"""cap/lingflow-crew（CrewOrchestrator 首插片）守卫测试。

锚点：
- 协议完整性：isinstance(crew, CrewOrchestrator)（runtime_checkable）；
- N3：缝 key 域前缀 cap/；
- 端到端：真实 workflow YAML（AGENT 真缝 + 控制流原语 + 通知）dispatch 全绿；
- hermetic：不依赖 lingflow 真实载体（platform 载体债 family-carriers-batch2-4
  与本债交叉引用，测试用 agent/ghidra 真缝 + 内置原语验证全链路）；
- J4：crew_run record 落盘；J5：失败显式（坏 workflow/未知 skill 均 failed）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lingclaude.core.seam import CrewOrchestrator, SeamRegistry, SeamType
from lingclaude.plugins.agents.registry_loader import load_all
from lingclaude.seams.lingflow_crew import (
    LingflowCrewOrchestrator,
    _resolve_template,
)

TEST_WF_DIR = Path(__file__).parent / "fixtures" / "lingflow_workflows"


@pytest.fixture(scope="module", autouse=True)
def _family_loaded():
    """AGENT 缝就位（模拟 main() 启动装配；ghidra 真缝用于端到端）。"""
    load_all()


@pytest.fixture()
def crew(tmp_path):
    """hermetic orchestrator：workflow 目录用测试 fixture。"""
    c = LingflowCrewOrchestrator(workflow_dir=TEST_WF_DIR)
    yield c
    # 清理注册表（fixture 级注册不外泄）
    try:
        SeamRegistry.unregister(SeamType.ORCHESTRATOR, c.name)
    except Exception:  # noqa: BLE001
        pass


# ── 协议完整性 ──────────────────────────────────────────────────────
def test_protocol_isinstance(crew):
    assert isinstance(crew, CrewOrchestrator), "CrewOrchestrator 协议实现不完整"


def test_n3_namespaced_key(crew):
    assert crew.name == "cap/lingflow-crew"
    assert "/" in crew.name  # N3 域前缀


def test_registry_roundtrip(crew):
    SeamRegistry.register(SeamType.ORCHESTRATOR, crew.name, crew)
    assert crew.name in SeamRegistry.snapshot().get(SeamType.ORCHESTRATOR.value, [])
    got = SeamRegistry.get(SeamType.ORCHESTRATOR, crew.name)
    assert got is crew


# ── create_crew 校验 ────────────────────────────────────────────────
def test_create_crew_rejects_empty(crew):
    with pytest.raises(ValueError):
        crew.create_crew([])


def test_create_crew_unknown_workflow(crew):
    with pytest.raises(FileNotFoundError):
        crew.create_crew(["zzz-no-such-workflow"])


def test_create_crew_ok(crew):
    cid = crew.create_crew(["crew-e2e-test"], params={"k": "v"})
    assert cid.startswith("crew-")
    st = crew.status(cid)
    assert st["state"] == "created"
    assert st["workflows"] == ["crew-e2e-test"]


def test_status_unknown_crew(crew):
    st = crew.status("crew-nonexistent")
    assert st["state"] == "unknown"


# ── 端到端（真实 AGENT 缝 + 控制流原语）────────────────────────────
@pytest.mark.live  # 步骤1 真缝派单需 Ghidra headless 8081 在跑（2026-09-28 审计补标）
def test_dispatch_e2e(crew):
    cid = crew.create_crew(["crew-e2e-test"])
    res = crew.dispatch(cid, task="", mode="sequential")
    assert res["state"] == "succeeded", res.get("error")
    steps = res["steps"]
    assert [s["state"] for s in steps] == ["succeeded"] * 3
    # 步骤 1：真缝派单返回真实 Ghidra 数据
    assert "FUN_" in str(steps[0]["output"])
    # 步骤 2/3：控制流原语
    assert steps[1]["output"].get("taken") == "true"
    assert steps[2]["output"].get("notified") is True
    # status 追踪一致
    assert crew.status(cid)["state"] == "succeeded"


def test_dispatch_unknown_skill_fails_explicitly(crew):
    """J5：未知 skill 显式 failed，不静默跳过。

    注：未知 skill 落到 lingflow 委派层（层 4）后由其载体债返回 failed
    （family-carriers-batch2-4：platform 载体 run 一律 failed 不假活）——
    crew 引擎如实透传该失败为 crew failed（J5 显式语义满足），错误文本
    来自委派层而非"未知 skill"字面。
    """
    cid = crew.create_crew(["crew-bad-skill"])
    res = crew.dispatch(cid, task="")
    assert res["state"] == "failed"
    assert res.get("error"), "失败必须带错误说明（不静默）"
    assert crew.status(cid)["state"] == "failed"


def test_dispatch_unknown_crew_raises(crew):
    with pytest.raises(KeyError):
        crew.dispatch("crew-nonexistent", task="")


# ── 模板插值 ────────────────────────────────────────────────────────
def test_template_params_default():
    out = _resolve_template("{{params.x | default('fallback')}}", {}, {})
    assert out == "fallback"


def test_template_params_value():
    out = _resolve_template("{{params.q}}", {"q": "hello"}, {})
    assert out == "hello"


def test_template_task_output():
    out = _resolve_template(
        "{{tasks.t1.output.total}}", {}, {"t1": {"total": 7}})
    assert out == "7"


# ── J4 审计 ─────────────────────────────────────────────────────────
def test_j4_records(tmp_path):
    from lingclaude.core.state_store import StateStore
    store = StateStore(backend="json", root=tmp_path / "crew_runs")
    c = LingflowCrewOrchestrator(workflow_dir=TEST_WF_DIR, store=store)
    cid = c.create_crew(["crew-e2e-test"])
    c.dispatch(cid, task="")
    # create 与 run 两个 record 均落盘
    assert store.load("crew_run", f"create:{cid}") is not None
    rec = store.load("crew_run", f"run:{cid}")
    assert rec is not None and rec["state"] == "succeeded"
