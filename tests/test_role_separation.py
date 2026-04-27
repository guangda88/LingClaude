from __future__ import annotations

import pytest
from lingclaude.core.role_separation import (
    AgentRoles,
    Role,
    RoleConflictChecker,
    RoleType,
    create_lingclaude_role_separation,
    ROLE_DEFINITIONS,
    save_role_config,
    load_role_config,
)
from pathlib import Path
import tempfile


class TestRoleType:
    def test_role_type_values(self) -> None:
        assert RoleType.RULE_MAKER.value == "rule_maker"
        assert RoleType.REFEREE.value == "referee"
        assert RoleType.SCORE_KEEPER.value == "score_keeper"
        assert RoleType.PARTICIPANT.value == "participant"


class TestAgentRoles:
    def test_has_role(self) -> None:
        agent = AgentRoles(
            agent_id="test_agent",
            roles=[
                ROLE_DEFINITIONS[RoleType.RULE_MAKER],
                ROLE_DEFINITIONS[RoleType.PARTICIPANT],
            ],
        )
        assert agent.has_role(RoleType.RULE_MAKER)
        assert agent.has_role(RoleType.PARTICIPANT)
        assert not agent.has_role(RoleType.REFEREE)

    def test_has_conflict(self) -> None:
        agent = AgentRoles(
            agent_id="test_agent",
            roles=[
                ROLE_DEFINITIONS[RoleType.RULE_MAKER],
                ROLE_DEFINITIONS[RoleType.PARTICIPANT],
            ],
        )
        # 规则制定者和参赛者有冲突
        assert agent.has_conflict(RoleType.REFEREE)
        assert agent.has_conflict(RoleType.PARTICIPANT)

    def test_can_perform_action(self) -> None:
        agent = AgentRoles(
            agent_id="test_agent",
            roles=[
                ROLE_DEFINITIONS[RoleType.RULE_MAKER],
            ],
        )
        assert agent.can_perform_action("propose_rule")
        assert agent.can_perform_action("amend_rule")
        assert not agent.can_perform_action("evaluate_member")


class TestRoleConflictChecker:
    def test_empty_checker_has_no_conflicts(self) -> None:
        checker = RoleConflictChecker(agent_roles=[])
        assert checker.check_conflicts() == []

    def test_detects_referee_participant_conflict(self) -> None:
        agent = AgentRoles(
            agent_id="test_agent",
            roles=[
                ROLE_DEFINITIONS[RoleType.REFEREE],
                ROLE_DEFINITIONS[RoleType.PARTICIPANT],
            ],
        )
        checker = RoleConflictChecker(agent_roles=[agent])
        conflicts = checker.check_conflicts()
        assert len(conflicts) == 1
        assert "同时担任裁判和参赛者" in conflicts[0]

    def test_detects_rule_maker_participant_conflict(self) -> None:
        agent = AgentRoles(
            agent_id="test_agent",
            roles=[
                ROLE_DEFINITIONS[RoleType.RULE_MAKER],
                ROLE_DEFINITIONS[RoleType.PARTICIPANT],
            ],
        )
        checker = RoleConflictChecker(agent_roles=[agent])
        conflicts = checker.check_conflicts()
        assert len(conflicts) == 1
        assert "同时担任规则制定者和参赛者" in conflicts[0]

    def test_detects_lingclaude_four_role_conflict(self) -> None:
        agent = AgentRoles(
            agent_id="lingclaude",
            roles=[
                ROLE_DEFINITIONS[RoleType.RULE_MAKER],
                ROLE_DEFINITIONS[RoleType.REFEREE],
                ROLE_DEFINITIONS[RoleType.SCORE_KEEPER],
                ROLE_DEFINITIONS[RoleType.PARTICIPANT],
            ],
        )
        checker = RoleConflictChecker(agent_roles=[agent])
        conflicts = checker.check_conflicts()
        # 灵克同时担任四个角色，应该检测到多个冲突
        assert len(conflicts) >= 1
        # 检查是否有专门针对四个角色的冲突
        has_four_role_conflict = any(
            "同时担任四个核心角色" in conflict
            for conflict in conflicts
        )
        assert has_four_role_conflict

    def test_ignores_disabled_agents(self) -> None:
        agent = AgentRoles(
            agent_id="test_agent",
            roles=[
                ROLE_DEFINITIONS[RoleType.REFEREE],
                ROLE_DEFINITIONS[RoleType.PARTICIPANT],
            ],
            enabled=False,  # 禁用该智能体
        )
        checker = RoleConflictChecker(agent_roles=[agent])
        conflicts = checker.check_conflicts()
        assert len(conflicts) == 0

    def test_validate_action_with_permission(self) -> None:
        agent = AgentRoles(
            agent_id="test_agent",
            roles=[
                ROLE_DEFINITIONS[RoleType.RULE_MAKER],
            ],
        )
        checker = RoleConflictChecker(agent_roles=[agent])
        result = checker.validate_action("test_agent", "propose_rule")
        assert result["allowed"] is True

    def test_validate_action_without_permission(self) -> None:
        agent = AgentRoles(
            agent_id="test_agent",
            roles=[
                ROLE_DEFINITIONS[RoleType.RULE_MAKER],
            ],
        )
        checker = RoleConflictChecker(agent_roles=[agent])
        result = checker.validate_action("test_agent", "evaluate_member")
        assert result["allowed"] is False
        assert "not allowed to perform action" in result["reason"]

    def test_validate_action_with_conflict(self) -> None:
        agent = AgentRoles(
            agent_id="test_agent",
            roles=[
                ROLE_DEFINITIONS[RoleType.REFEREE],
                ROLE_DEFINITIONS[RoleType.PARTICIPANT],
            ],
        )
        checker = RoleConflictChecker(agent_roles=[agent])
        result = checker.validate_action("test_agent", "evaluate_member")
        assert result["allowed"] is False
        assert "role conflicts" in result["reason"]


class TestCreateLingclaudeRoleSeparation:
    def test_creates_checker_with_lingclaude(self) -> None:
        checker = create_lingclaude_role_separation()
        lingclaude = next((a for a in checker.agent_roles if a.agent_id == "lingclaude"), None)
        assert lingclaude is not None
        assert lingclaude.enabled

    def test_no_conflicts_after_separation(self) -> None:
        checker = create_lingclaude_role_separation()
        conflicts = checker.check_conflicts()
        # 角色分离后不应有冲突
        assert len(conflicts) == 0

    def test_all_agents_enabled(self) -> None:
        checker = create_lingclaude_role_separation()
        assert all(a.enabled for a in checker.agent_roles)

    def test_lingclaude_is_participant_only(self) -> None:
        checker = create_lingclaude_role_separation()
        lingclaude = next((a for a in checker.agent_roles if a.agent_id == "lingclaude"), None)
        assert lingclaude is not None
        assert lingclaude.has_role(RoleType.PARTICIPANT)
        assert not lingclaude.has_role(RoleType.RULE_MAKER)
        assert not lingclaude.has_role(RoleType.REFEREE)
        assert not lingclaude.has_role(RoleType.SCORE_KEEPER)


class TestRoleConfigPersistence:
    def test_save_and_load_role_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "role_config.json"

            # 创建并保存配置
            checker = RoleConflictChecker(
                agent_roles=[
                    AgentRoles(
                        agent_id="test_agent",
                        roles=[
                            ROLE_DEFINITIONS[RoleType.RULE_MAKER],
                        ],
                    ),
                ],
            )
            save_role_config(config_path, checker)

            # 加载配置
            loaded_checker = load_role_config(config_path)

            # 验证加载的配置
            assert len(loaded_checker.agent_roles) >= 1
            test_agent = next((a for a in loaded_checker.agent_roles if a.agent_id == "test_agent"), None)
            assert test_agent is not None
            assert test_agent.has_role(RoleType.RULE_MAKER)

    def test_saved_config_has_no_conflicts_after_separation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "role_config.json"

            checker = create_lingclaude_role_separation()
            save_role_config(config_path)

            import json
            data = json.loads(config_path.read_text(encoding="utf-8"))

            assert "conflicts" in data
            assert isinstance(data["conflicts"], list)
            # 角色分离后没有冲突
            assert len(data["conflicts"]) == 0
            # 4个 agent 都注册了
            assert len(data["agents"]) == 4


class TestRoleDefinitions:
    def test_all_roles_defined(self) -> None:
        assert RoleType.RULE_MAKER in ROLE_DEFINITIONS
        assert RoleType.REFEREE in ROLE_DEFINITIONS
        assert RoleType.SCORE_KEEPER in ROLE_DEFINITIONS
        assert RoleType.PARTICIPANT in ROLE_DEFINITIONS

    def test_rule_maker_conflicts(self) -> None:
        role = ROLE_DEFINITIONS[RoleType.RULE_MAKER]
        assert RoleType.REFEREE in role.conflicts_with
        assert RoleType.PARTICIPANT in role.conflicts_with

    def test_referee_conflicts(self) -> None:
        role = ROLE_DEFINITIONS[RoleType.REFEREE]
        assert RoleType.RULE_MAKER in role.conflicts_with
        assert RoleType.PARTICIPANT in role.conflicts_with
        assert RoleType.SCORE_KEEPER in role.conflicts_with

    def test_all_roles_have_description(self) -> None:
        for role_type, role in ROLE_DEFINITIONS.items():
            assert role.name
            assert role.description
            assert len(role.responsibilities) > 0
