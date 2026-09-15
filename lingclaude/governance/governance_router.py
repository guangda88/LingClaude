"""governance_router.py — 灵元V1.0 治理路由薄主干

出入+流转统一：
  出入: proposal 从提案→投票→决议→归档
  流转: ProposalStatus统一 transition

外观模式：统一governance_v2 + proposal_lifecycle 两模块接口。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from lingclaude.governance.governance_v2 import GovernanceEngine, ProposalStatus

logger = logging.getLogger("lingclaude.governance.governance_router")


class GovernanceRouter:
    """灵元V1.0 治理路由薄主干

    外观模式：统一 governance_v2.GovernanceEngine + proposal_lifecycle。

    E4（灵元1.0 P1, 2026-09-09）清偿定案：
    - 原实现是"幻想门面"：propose/vote/resolve/get_proposal/list_proposals 均转发到
      GovernanceEngine 上不存在的方法，一旦注入必然 AttributeError——有缝无机件。
    - 现在 propose() 接真引擎（create_proposal 签名匹配，uuid 补 proposal_id）；
      get_status/list_active/dashboard 按引擎真实字段（proosals/status 枚举）诚实接线；
      vote/resolve 无真实对应物，改显式 NotImplementedError（H1：诚实接口优于静默谎言）。

    E11（灵元1.0 再照, 2026-09-15）：删除死状态机 UnifiedProposalStatus——
    全仓零消费（类定义外无任何引用），且值域（proposed/discussing/voting/resolved/expired/withdrawn）
    与真引擎 ProposalStatus（analysis/open/objection_raised/passed/failed/withdrawn）不一致，
    是"幻觉状态机"。状态一律以 governance_v2.ProposalStatus 为单源。
    """

    def __init__(
        self,
        engine: Optional[Any] = None,
        lifecycle_mgr: Optional[Any] = None,
    ):
        self._engine = engine if engine is not None else self._create_default_engine()
        self._lifecycle_mgr = lifecycle_mgr  # P2 接线 proposal_lifecycle

    @staticmethod
    def _create_default_engine() -> Any:
        """E4: 默认真实引擎，终结"有注入设计但从未被注入"。"""
        return GovernanceEngine()

    def propose(
        self,
        title: str,
        proposer: str,
        body: str = "",
        quorum: int = 3,
        deadline_hours: int = 48,
        notify: bool = False,
    ) -> Dict[str, Any]:
        """统一提案入口 — 路由到真实引擎。

        notify=False（默认）：机器路由不开议会线——GovernanceEngine.bus 属性会
            惰性自动连接灵信总线，True 会导致每次 webui 工具审批都向族议会广播。
            需要议会审议时由调用方显式传 True（governance_v2.create_proposal 原生开关）。
        quorum：保留兼容参数（引擎异议制下暂不使用，见 governance_v2 异议语义）。
        """
        if self._engine is None:
            raise RuntimeError("No governance backend available")

        proposal_id = f"rt-{uuid.uuid4().hex[:12]}"
        created = self._engine.create_proposal(
            proposal_id=proposal_id,
            proposer=proposer,
            title=title,
            body=body,
            deadline_hours=float(deadline_hours),
            notify=notify,
        )
        return {
            "proposal_id": created.proposal_id,
            "source": "engine",
            "status": created.status.value
            if hasattr(created.status, "value")
            else str(created.status),
        }

    def vote(
        self,
        proposal_id: str,
        voter: str,
        decision: str,
        reason: str = "",
    ) -> bool:
        """E4: 幻想接口显式化——引擎无 vote 语义（异议制），原转发必 AttributeError。"""
        raise NotImplementedError(
            "GovernanceRouter.vote: 引擎为异议制(governance_v2.raise_objection)，"
            "无独立投票语义；P2 统一流转设计后再议"
        )

    def resolve(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        """E4: 幻想接口显式化（引擎无 resolve，有 lifecycle.finalize 但语义不同）。"""
        raise NotImplementedError(
            "GovernanceRouter.resolve: 请用 GovernanceEngine.lifecycle.finalize；P2 统一后回收"
        )

    def get_status(self, proposal_id: str) -> Optional[str]:
        """统一状态查询 — 按引擎真实字段接线（E4）。"""
        if self._engine:
            p = getattr(self._engine, "proposals", {}).get(proposal_id)
            if p is not None:
                status = getattr(p, "status", None)
                return status.value if hasattr(status, "value") else str(status)
        return None

    def list_active(self) -> List[Dict[str, Any]]:
        """列出所有活跃提案 — 按引擎真实字段接线（E4）。"""
        if self._engine:
            return [
                p.to_dict()
                for p in self._engine.proposals.values()
                if getattr(p, "status", None) == ProposalStatus.OPEN
            ]
        return []

    def dashboard(self) -> Dict[str, Any]:
        """统一治理看板 — 按引擎真实字段接线（E4）。"""
        result: Dict[str, Any] = {"active": 0, "resolved": 0, "expired": 0}
        if self._engine:
            result["active"] = sum(
                1
                for p in self._engine.proposals.values()
                if getattr(p, "status", None) == ProposalStatus.OPEN
            )
        return result
