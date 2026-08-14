"""governance_router.py — 灵元V1.0 治理路由薄主干

出入+流转统一：
  出入: proposal 从提案→投票→决议→归档
  流转: ProposalStatus统一 transition

外观模式：统一governance_v2 + proposal_lifecycle 两模块接口。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("lingclaude.governance.governance_router")


class UnifiedProposalStatus:
    """统一提案状态 — 兼容governance_v2和proposal_lifecycle"""
    PROPOSED = "proposed"
    DISCUSSING = "discussing"
    VOTING = "voting"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"

    # 灵元V1.0 合法流转
    TRANSITIONS = {
        "proposed": ["discussing", "withdrawn"],
        "discussing": ["voting", "withdrawn"],
        "voting": ["resolved", "expired"],
        "resolved": [],
        "expired": [],
        "withdrawn": [],
    }

    @classmethod
    def can_transition(cls, from_state: str, to_state: str) -> bool:
        return to_state in cls.TRANSITIONS.get(from_state, [])


class GovernanceRouter:
    """灵元V1.0 治理路由薄主干

    外观模式：统一governance_v2.GovernanceEngine + proposal_lifecycle.ProposalLifecycle
    """

    def __init__(
        self,
        engine: Optional[Any] = None,
        lifecycle_mgr: Optional[Any] = None,
    ):
        self._engine = engine
        self._lifecycle_mgr = lifecycle_mgr

    def propose(
        self,
        title: str,
        proposer: str,
        body: str = "",
        quorum: int = 3,
        deadline_hours: int = 48,
    ) -> Dict[str, Any]:
        """统一提案入口 — 路由到engine或lifecycle"""
        if self._engine:
            proposal_id = self._engine.propose(
                title=title, proposer=proposer, body=body, quorum=quorum,
                deadline_hours=deadline_hours,
            )
            return {"proposal_id": proposal_id, "source": "engine", "status": UnifiedProposalStatus.PROPOSED}
        raise RuntimeError("No governance backend available")

    def vote(
        self,
        proposal_id: str,
        voter: str,
        decision: str,
        reason: str = "",
    ) -> bool:
        """统一投票入口"""
        if self._engine:
            self._engine.vote(
                proposal_id=proposal_id, voter=voter, vote=decision, reason=reason,
            )
            return True
        return False

    def resolve(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        """统一决议入口 — 自动检查quorum+deadline"""
        if self._engine:
            return self._engine.resolve(proposal_id)
        return None

    def get_status(self, proposal_id: str) -> Optional[str]:
        """统一状态查询"""
        if self._engine:
            p = self._engine.get_proposal(proposal_id)
            if p:
                return p.status
        return None

    def list_active(self) -> List[Dict[str, Any]]:
        """列出所有活跃提案"""
        if self._engine:
            return [p.to_dict() for p in self._engine.list_proposals(status_filter="active")]
        return []

    def dashboard(self) -> Dict[str, Any]:
        """统一治理看板"""
        result: Dict[str, Any] = {"active": 0, "resolved": 0, "expired": 0}
        if self._engine:
            active = self._engine.list_proposals(status_filter="active")
            result["active"] = len(active)
        return result
