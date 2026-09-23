"""ResearchCrew — 跨 Agent 科研编排插片（CrewOrchestrator Protocol 实现）。

Phase 3 (2026-09-15): 对标 Ekko Studio 的 Multi-Agent Crews，编排灵族多 Agent
协同完成科研工程流。首个实例：

    灵研.propose_experiment → 灵知.knowledge_query → 灵极优.run_optimization → 灵研.evaluate

设计纪律：
- 治理真引擎：提案跟踪用 GovernanceEngine（create_proposal / raise_objection /
  check_deadlines），不碰 GovernanceRouter 的 vote/resolve 诚实桩（E4：无真实语义）。
- fail-closed：成员后端未注册 → 该步骤返回 error 且整体失败，不静默跳过。
- 串行编排（sequential）：按成员顺序逐个 dispatch，前一步成功才进下一步。
- 提案状态映射：CREW 生命周期 → GovernanceEngine 提案（OPEN→PASSED/FAILED）。
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from lingclaude.core.seam import CrewOrchestrator, SeamRegistry, SeamType
from lingclaude.engine.subagent.base import (
    SubagentContext,
    SubagentRequest,
)
from lingclaude.engine.subagent.manager import SubagentManager
from lingclaude.governance.governance_v2 import GovernanceEngine

logger = logging.getLogger("lingclaude.seams.research_crew")


@dataclass
class CrewMemberStep:
    """编排中的单步执行记录。"""

    provider: str
    task: str
    status: str = "pending"  # pending / running / success / failed / aborted
    output: str = ""
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    duration_ms: int = 0


@dataclass
class ResearchCrewInstance:
    """一个 crew 的运行实例。"""

    crew_id: str
    members: list[str]
    task: str
    mode: str = "sequential"
    status: str = "created"  # created / running / succeeded / failed / aborted
    proposal_id: str = ""
    steps: list[CrewMemberStep] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class ResearchCrew:
    """科研编排 crew（CrewOrchestrator Protocol 实现）。

    成员 provider 名（SubagentManager 后端）：
    - lingresearch: 灵研（科研方法）
    - lingzhi:      灵知（知识库）
    - lingminopt:   灵极优（优化）
    未注册的 provider 在 dispatch 时 fail-closed 报错。
    """

    name = "research_crew"

    def __init__(
        self,
        manager: SubagentManager | None = None,
        governance: Any = None,
    ) -> None:
        self._manager = manager or SubagentManager()
        if governance is None:
            governance = GovernanceEngine()
        self._governance = governance
        self._crews: dict[str, ResearchCrewInstance] = {}
        self._lock = None

    # ── CrewOrchestrator Protocol ──
    def create_crew(self, members: list[str], **kwargs: Any) -> str:
        """创建 crew，返回 crew_id。members = SubagentManager 已注册后端 provider 名。"""
        if not members:
            raise ValueError("crew members 不能为空")
        # 校验成员已注册（fail-closed）— 精确命中，不走 get_backend 默认回退
        registered = set(self._manager._backends) | set(self._manager._aliases)
        missing = [m for m in members if m not in registered]
        if missing:
            raise ValueError(
                f"成员后端未注册: {missing}（可用: {self._manager.list_backends()}）"
            )

        crew_id = f"crew-{uuid.uuid4().hex[:10]}"
        crew = ResearchCrewInstance(
            crew_id=crew_id,
            members=list(members),
            task=kwargs.get("task", ""),
            mode=kwargs.get("mode", "sequential"),
        )

        # 建治理提案跟踪（notify=False：机器编排不开议会线）
        try:
            proposal_id = f"rc-{uuid.uuid4().hex[:12]}"
            created = self._governance.create_proposal(
                proposal_id=proposal_id,
                proposer="research_crew",
                title=f"科研编排: {crew.task or crew_id}",
                body=(
                    f"Crew {crew_id} 成员: {', '.join(members)}; "
                    f"任务: {crew.task or '(未命名)'}"
                ),
                deadline_hours=float(kwargs.get("deadline_hours", 48.0)),
                notify=False,
            )
            crew.proposal_id = created.proposal_id
            logger.info("Crew %s 提案已建: %s", crew_id, proposal_id)
        except Exception as e:  # noqa: BLE001 — 治理不可用不阻断编排
            logger.warning("Crew %s 提案创建失败（治理跟踪降级）: %s", crew_id, e)
            crew.proposal_id = ""

        self._crews[crew_id] = crew
        return crew_id

    def dispatch(
        self,
        crew_id: str,
        task: str,
        mode: str = "sequential",
    ) -> dict[str, Any]:
        """串行 dispatch：按成员顺序逐个执行，前一步成功才进下一步。"""
        crew = self._crews.get(crew_id)
        if crew is None:
            return {"success": False, "error": f"crew 不存在: {crew_id}"}

        if crew.status == "running":
            return {"success": False, "error": f"crew 正在运行: {crew_id}"}

        crew.task = task or crew.task
        crew.mode = mode
        crew.status = "running"
        crew.updated_at = time.time()

        results: dict[str, Any] = {"steps": [], "succeeded": [], "failed": []}
        ctx = SubagentContext()

        for provider in crew.members:
            step = CrewMemberStep(provider=provider, task=crew.task)
            step.status = "running"
            step.started_at = time.time()
            crew.steps.append(step)

            try:
                # 精确命中校验（不走 get_backend 默认回退）
                registered = set(self._manager._backends) | set(self._manager._aliases)
                if provider not in registered:
                    raise RuntimeError(f"成员后端未注册: {provider}")

                req = SubagentRequest(
                    task=crew.task,
                    context=f"Crew {crew_id} 串行编排第 {len(crew.steps)} 步，成员 {provider}",
                    max_rounds=5,
                    provider=provider,
                )
                result = self._manager.run(req, ctx)
                step.finished_at = time.time()
                step.duration_ms = int((step.finished_at - step.started_at) * 1000)

                if result.success:
                    step.status = "success"
                    step.output = result.output
                    results["succeeded"].append(provider)
                    logger.info(
                        "Crew %s step %s 成功 (%.0fms)",
                        crew_id, provider, step.duration_ms,
                    )
                else:
                    step.status = "failed"
                    step.error = result.error or "unknown error"
                    results["failed"].append(provider)
                    logger.warning(
                        "Crew %s step %s 失败: %s",
                        crew_id, provider, step.error,
                    )
                    break  # 串行：失败即停
            except Exception as e:  # noqa: BLE001 — 单步异常记录后停止
                step.status = "failed"
                step.error = str(e)
                step.finished_at = time.time()
                results["failed"].append(provider)
                logger.exception("Crew %s step %s 异常: %s", crew_id, provider, e)
                break

        # 汇总
        crew.updated_at = time.time()
        crew.result = results
        if results["failed"]:
            crew.status = "failed"
        else:
            crew.status = "succeeded"

        # 治理：提案决议（真实语义）
        self._finalize_governance(crew)

        return {
            "crew_id": crew_id,
            "status": crew.status,
            "proposal_id": crew.proposal_id,
            "steps": [
                {
                    "provider": s.provider,
                    "status": s.status,
                    "duration_ms": s.duration_ms,
                    "output_preview": (s.output or "")[:200],
                    "error": s.error,
                }
                for s in crew.steps
            ],
            "succeeded": results["succeeded"],
            "failed": results["failed"],
        }

    def _finalize_governance(self, crew: ResearchCrewInstance) -> None:
        """治理决议：crew 结束后按结果标记提案（真引擎生命周期）。"""
        if not crew.proposal_id:
            return
        try:
            lc = self._governance.lifecycle
            if lc:
                outcome = "passed" if crew.status == "succeeded" else "failed"
                note = (
                    f"Crew {crew.crew_id} {crew.status}; "
                    f"成功: {', '.join(s.provider for s in crew.steps if s.status=='success')}"
                )
                lc.finalize(crew.proposal_id, outcome, note)
                logger.info("Crew %s 提案 %s 决议: %s", crew.crew_id, crew.proposal_id, outcome)
        except Exception as e:  # noqa: BLE001 — 治理决议失败不阻断返回
            logger.warning("Crew %s 治理决议失败: %s", crew.crew_id, e)

    def status(self, crew_id: str) -> dict[str, Any]:
        """查询 crew 状态。"""
        crew = self._crews.get(crew_id)
        if crew is None:
            return {"error": f"crew 不存在: {crew_id}"}
        return {
            "crew_id": crew.crew_id,
            "status": crew.status,
            "mode": crew.mode,
            "proposal_id": crew.proposal_id,
            "members": crew.members,
            "task": crew.task,
            "steps": [
                {
                    "provider": s.provider,
                    "status": s.status,
                    "duration_ms": s.duration_ms,
                    "error": s.error,
                }
                for s in crew.steps
            ],
            "updated_at": crew.updated_at,
        }

    # ── 辅助 ──
    def list_crews(self) -> list[dict[str, Any]]:
        return [
            {
                "crew_id": c.crew_id,
                "status": c.status,
                "task": c.task,
                "members": c.members,
                "proposal_id": c.proposal_id,
            }
            for c in self._crews.values()
        ]


def register_research_crew(name: str = "cap/research_crew") -> bool:
    """把 ResearchCrew 注册进 SeamRegistry（SeamType.ORCHESTRATOR）。

    注册前做 CrewOrchestrator 协议结构校验（fail-closed）：
    缺成员即拒绝注册，杜绝残缺实例进入注册表。
    name 默认带域前缀（铁律 7 / N3：跨物理层缝 key 必为 {ns}/{seam}，
    2026-09-23 守卫上岗后由裸 key 'research_crew' 迁移）。
    """
    try:
        crew = ResearchCrew()
        missing = SeamRegistry.check_protocol(SeamType.ORCHESTRATOR, crew)
        if missing:
            logger.error(
                "ResearchCrew 缺少 CrewOrchestrator 协议成员 %s，拒绝注册", missing
            )
            return False
        SeamRegistry.register(SeamType.ORCHESTRATOR, name, crew)
        logger.info("ResearchCrew 已注册: %s (orchestrator)", name)
        return True
    except Exception as e:  # noqa: BLE001 — 注册失败不崩溃
        logger.error("ResearchCrew 注册失败: %s", e)
        return False
