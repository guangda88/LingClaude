"""L7/L10 工程化 v0.2 新方法测试

覆盖 validate_operation + check_role_boundary（灵安 R1 强制前置：越权/凭证/身份 3 类）
"""
from __future__ import annotations

from lingclaude.core.role_separation import (
    AgentRoles,
    RoleConflictChecker,
    RoleType,
    GateType,
    EvidenceVerdict,
    FailClosedAction,
    ROLE_DEFINITIONS,
)


def _make_checker() -> RoleConflictChecker:
    return RoleConflictChecker(
        agent_roles=[
            AgentRoles(
                agent_id="lingclaude",
                roles=[ROLE_DEFINITIONS[RoleType.PARTICIPANT]],
                enabled=True,
            ),
            AgentRoles(
                agent_id="lingan",
                roles=[ROLE_DEFINITIONS[RoleType.REFEREE]],
                enabled=True,
            ),
        ]
    )


class TestValidateOperation:
    def test_valid_evidence_pass(self) -> None:
        c = _make_checker()
        result = c.validate_operation(
            "edit",
            {
                "gate_id": "a" * 32,
                "gate_type": GateType.ROLE.value,
                "evidence_payload": {"commit": "abc123"},
                "verdict": EvidenceVerdict.PASS.value,
            },
        )
        assert result["allowed"] is True
        assert result["missing_fields"] == []

    def test_missing_gate_id(self) -> None:
        c = _make_checker()
        result = c.validate_operation(
            "edit",
            {
                "gate_type": GateType.ROLE.value,
                "evidence_payload": {},
                "verdict": EvidenceVerdict.PASS.value,
            },
        )
        assert result["allowed"] is False
        assert "gate_id" in result["missing_fields"]
        assert result["fail_closed_action"] == FailClosedAction.BLOCK.value

    def test_invalid_gate_type(self) -> None:
        c = _make_checker()
        result = c.validate_operation(
            "edit",
            {
                "gate_id": "a" * 32,
                "gate_type": "bogus",
                "evidence_payload": {},
                "verdict": EvidenceVerdict.PASS.value,
            },
        )
        assert result["allowed"] is False
        assert "invalid gate_type" in result["reason"]

    def test_fail_verdict_blocks(self) -> None:
        c = _make_checker()
        result = c.validate_operation(
            "edit",
            {
                "gate_id": "a" * 32,
                "gate_type": GateType.IDENTITY.value,
                "evidence_payload": {"reason": "X-Agent-Id mismatch"},
                "verdict": EvidenceVerdict.FAIL.value,
                "fail_closed_action": FailClosedAction.DOUBLE_SIGN.value,
            },
        )
        assert result["allowed"] is False
        assert result["fail_closed_action"] == FailClosedAction.DOUBLE_SIGN.value

    def test_strict_mode_requires_owner_sign(self) -> None:
        c = _make_checker()
        result = c.validate_operation(
            "deploy",
            {
                "gate_id": "a" * 32,
                "gate_type": GateType.AUTHORIZATION.value,
                "evidence_payload": {},
                "verdict": EvidenceVerdict.PASS.value,
            },
            strict=True,
        )
        assert result["allowed"] is False
        assert "owner_sign" in result["missing_fields"]
        assert "commit_hash" in result["missing_fields"]


class TestCheckRoleBoundary:
    def test_valid_context(self) -> None:
        c = _make_checker()
        result = c.check_role_boundary(
            {"agent_id": "lingclaude", "action": "vote"}
        )
        assert result["allowed"] is True
        assert result["boundary_violation"] is None

    def test_agenda_role_overlap(self) -> None:
        c = _make_checker()
        result = c.check_role_boundary(
            {
                "agent_id": "lingclaude",
                "agenda_owner": "lingclaude",
                "convener": "lingclaude",
                "host": "lingan",
            }
        )
        assert result["allowed"] is False
        assert result["boundary_violation"] == "agenda_role_overlap"

    def test_self_vote_blocked(self) -> None:
        c = RoleConflictChecker(
            agent_roles=[
                AgentRoles(
                    agent_id="lingyan",
                    roles=[ROLE_DEFINITIONS[RoleType.RULE_MAKER]],
                    enabled=True,
                ),
            ]
        )
        result = c.check_role_boundary(
            {
                "agent_id": "lingyan",
                "action": "vote_rule_change",
                "proposal_owner": "lingyan",
            }
        )
        assert result["allowed"] is False
        assert result["boundary_violation"] == "self_vote"

    def test_action_not_allowed(self) -> None:
        c = _make_checker()
        result = c.check_role_boundary(
            {"agent_id": "lingclaude", "action": "evaluate_member"}
        )
        assert result["allowed"] is False
        assert result["boundary_violation"] == "action_not_allowed"

    def test_disabled_agent(self) -> None:
        c = RoleConflictChecker(
            agent_roles=[
                AgentRoles(
                    agent_id="lingclaude",
                    roles=[ROLE_DEFINITIONS[RoleType.PARTICIPANT]],
                    enabled=False,
                ),
            ]
        )
        result = c.check_role_boundary({"agent_id": "lingclaude"})
        assert result["allowed"] is False
        assert result["boundary_violation"] == "disabled_agent"


class TestIdentityCredentialAuthorization:
    """灵安 R1 强制前置：越权/凭证/身份 3 类"""

    def test_identity_fail_closed(self) -> None:
        c = _make_checker()
        result = c.validate_operation(
            "write",
            {
                "gate_id": "a" * 32,
                "gate_type": GateType.IDENTITY.value,
                "evidence_payload": {"X-Agent-Id": "unknown"},
                "verdict": EvidenceVerdict.FAIL.value,
                "fail_closed_action": FailClosedAction.BLOCK.value,
            },
        )
        assert result["allowed"] is False
        assert result["fail_closed_action"] == FailClosedAction.BLOCK.value

    def test_credential_double_sign(self) -> None:
        c = _make_checker()
        result = c.validate_operation(
            "deploy",
            {
                "gate_id": "a" * 32,
                "gate_type": GateType.CREDENTIAL.value,
                "evidence_payload": {"token": "expired"},
                "verdict": EvidenceVerdict.FAIL.value,
                "fail_closed_action": FailClosedAction.DOUBLE_SIGN.value,
            },
        )
        assert result["allowed"] is False
        assert result["fail_closed_action"] == FailClosedAction.DOUBLE_SIGN.value

    def test_authorization_alert(self) -> None:
        c = _make_checker()
        result = c.validate_operation(
            "vote",
            {
                "gate_id": "a" * 32,
                "gate_type": GateType.AUTHORIZATION.value,
                "evidence_payload": {"scope": "out_of_scope"},
                "verdict": EvidenceVerdict.FAIL.value,
                "fail_closed_action": FailClosedAction.ALERT.value,
            },
        )
        assert result["allowed"] is False
        assert result["fail_closed_action"] == FailClosedAction.ALERT.value


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])