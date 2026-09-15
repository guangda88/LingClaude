"""Phase 2 验收：Agent 插片协议（AGENT/MULTIMODAL/ORCHESTRATOR）。

对标 Ekko Studio 多 Agent 挂载调度的架构开关：
  - SeamType 扩展 3 枚举（AGENT/MULTIMODAL/ORCHESTRATOR）
  - 协议结构性检查（check_protocol）
  - 与现有 SubagentManager 的互操作映射
"""
from __future__ import annotations

from lingclaude.core.seam import AgentCapability, AgentSeam, SeamRegistry, SeamType


class FakeAgent:
    """实现 AgentSeam 协议的测试 Agent（鸭子类型，不继承）。"""

    name = "fake_research"

    def run(self, *args, **kwargs):
        return {"output": "done"}

    def abort(self, agent_id: str) -> bool:
        return True

    def status(self, agent_id: str) -> str:
        return "completed"


class BadAgent:
    """缺 status() 的坏 Agent（应被协议检查发现）。"""

    name = "bad_agent"

    def run(self, *args, **kwargs):
        return {}


class FakeMultimodal:
    name = "lingtong_emotion"

    def task_type(self) -> str:
        return "emotion"

    def input_schema(self) -> dict:
        return {"type": "object", "properties": {"text": {"type": "string"}}}

    def execute(self, **kwargs):
        return {"emotion": "positive", "score": 0.9}


class FakeCrew:
    name = "research_crew"

    def create_crew(self, members: list[str], **kwargs):
        return "crew-1"

    def dispatch(self, crew_id: str, task: str, mode: str = "sequential"):
        return {"status": "done", "members": {}}

    def status(self, crew_id: str) -> dict:
        return {"crew_id": crew_id, "members": {}}


def _reset():
    SeamRegistry.reset()


# ---------------------------------------------------------------------------
# 1. SeamType 扩展
# ---------------------------------------------------------------------------


def test_seamtype_has_three_new_enum_members():
    assert SeamType.AGENT.value == "agent"
    assert SeamType.MULTIMODAL.value == "multimodal"
    assert SeamType.ORCHESTRATOR.value == "orchestrator"
    # 原有枚举不受影响
    assert SeamType.TOOL.value == "tool"
    assert SeamType.PROVIDER.value == "provider"


# ---------------------------------------------------------------------------
# 2. AGENT 插片注册/查询/注销（热拔插）
# ---------------------------------------------------------------------------


def test_agent_seam_register_get_unregister():
    _reset()
    agent = FakeAgent()
    SeamRegistry.register(SeamType.AGENT, "fake_research", agent)
    assert SeamRegistry.has(SeamType.AGENT, "fake_research")
    assert SeamRegistry.get(SeamType.AGENT, "fake_research") is agent
    assert "fake_research" in SeamRegistry.list_names(SeamType.AGENT)
    assert SeamRegistry.unregister(SeamType.AGENT, "fake_research") is True
    assert SeamRegistry.has(SeamType.AGENT, "fake_research") is False


def test_agent_seam_snapshot_shows_agent_type():
    _reset()
    SeamRegistry.register(SeamType.AGENT, "lingresearch", FakeAgent())
    snap = SeamRegistry.snapshot()
    assert "agent" in snap
    assert "lingresearch" in snap["agent"]


def test_agent_seam_get_optional_missing():
    _reset()
    assert SeamRegistry.get_optional(SeamType.AGENT, "nope") is None


# ---------------------------------------------------------------------------
# 3. 协议结构性检查（AGENT/MULTIMODAL/ORCHESTRATOR）
# ---------------------------------------------------------------------------


def test_check_protocol_agent_good_and_bad():
    _reset()
    assert SeamRegistry.check_protocol(SeamType.AGENT, FakeAgent()) == []
    missing = set(SeamRegistry.check_protocol(SeamType.AGENT, BadAgent()))
    assert missing == {"abort", "status"}  # 缺 abort() 和 status()


def test_check_protocol_multimodal():
    _reset()
    assert SeamRegistry.check_protocol(SeamType.MULTIMODAL, FakeMultimodal()) == []
    # 普通对象缺 3 个成员
    missing = set(SeamRegistry.check_protocol(SeamType.MULTIMODAL, object()))
    assert {"task_type", "input_schema", "execute"} <= missing


def test_check_protocol_orchestrator():
    _reset()
    assert SeamRegistry.check_protocol(SeamType.ORCHESTRATOR, FakeCrew()) == []
    missing = set(SeamRegistry.check_protocol(SeamType.ORCHESTRATOR, object()))
    assert {"create_crew", "dispatch", "status"} <= missing


# ---------------------------------------------------------------------------
# 4. 多模态插片注册/调用
# ---------------------------------------------------------------------------


def test_multimodal_register_and_execute():
    _reset()
    mm = FakeMultimodal()
    SeamRegistry.register(SeamType.MULTIMODAL, "lingtong_emotion", mm)
    got = SeamRegistry.get(SeamType.MULTIMODAL, "lingtong_emotion")
    assert got.task_type() == "emotion"
    assert got.execute(text="好棒")["emotion"] == "positive"
    assert SeamRegistry.check_protocol(SeamType.MULTIMODAL, got) == []


# ---------------------------------------------------------------------------
# 5. 编排插片注册/调用（对标 Multi-Agent Crews）
# ---------------------------------------------------------------------------


def test_orchestrator_register_and_dispatch():
    _reset()
    crew = FakeCrew()
    SeamRegistry.register(SeamType.ORCHESTRATOR, "research_crew", crew)
    got = SeamRegistry.get(SeamType.ORCHESTRATOR, "research_crew")
    crew_id = got.create_crew(["lingresearch", "lingzhi"])
    assert crew_id == "crew-1"
    assert got.dispatch(crew_id, "分析代码")["status"] == "done"
    assert got.status(crew_id)["crew_id"] == "crew-1"


# ---------------------------------------------------------------------------
# 6. AgentCapability 元数据 + 与 SubagentManager 互操作映射
# ---------------------------------------------------------------------------


def test_agent_capability_dataclass():
    cap = AgentCapability(
        name="lingresearch",
        description="研究员",
        kind="research",
        methods=("propose_experiment", "evaluate_result"),
        transport="stdio",
    )
    assert cap.name == "lingresearch"
    assert cap.kind == "research"
    assert cap.methods == ("propose_experiment", "evaluate_result")
    assert cap.transport == "stdio"
    # 默认值
    default = AgentCapability(name="x")
    assert default.version == "0.1.0"
    assert default.transport == "in-process"


def test_agent_seam_maps_to_subagent_backend_contract():
    """AgentSeam 协议属性与 SubagentBackend 对齐（互操作层）。"""
    from lingclaude.engine.subagent.base import SubagentBackend

    # AgentSeam 要求 name/run/abort/status
    agent_attrs = set(AgentSeam.__protocol_attrs__)
    assert {"name", "run", "abort", "status"} <= agent_attrs
    # SubagentBackend 也有这些（abort/status 有默认实现）
    backend_attrs = {a for a in dir(SubagentBackend) if not a.startswith("_")}
    assert {"name", "run", "abort", "status"} <= backend_attrs
