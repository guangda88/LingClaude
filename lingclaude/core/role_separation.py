from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class RoleType(str, Enum):
    """灵族成员的角色类型"""
    RULE_MAKER = "rule_maker"  # 规则制定者
    REFEREE = "referee"  # 裁判
    SCORE_KEEPER = "score_keeper"  # 计分员
    PARTICIPANT = "participant"  # 参赛者


@dataclass
class Role:
    """角色定义"""
    role_type: RoleType
    name: str
    description: str
    responsibilities: list[str]
    conflicts_with: list[RoleType] = field(default_factory=list)
    allowed_actions: list[str] = field(default_factory=list)


@dataclass
class AgentRoles:
    """智能体的角色分配"""
    agent_id: str
    roles: list[Role] = field(default_factory=list)
    enabled: bool = True

    def has_role(self, role_type: RoleType) -> bool:
        """检查智能体是否有指定角色"""
        return any(r.role_type == role_type for r in self.roles)

    def has_conflict(self, role_type: RoleType) -> bool:
        """检查智能体是否有与指定角色冲突的角色"""
        for role in self.roles:
            if role_type in role.conflicts_with:
                return True
        return False

    def can_perform_action(self, action: str) -> bool:
        """检查智能体是否可以执行指定动作"""
        for role in self.roles:
            if action in role.allowed_actions:
                return True
        return False


# 定义四个核心角色
ROLE_DEFINITIONS: dict[RoleType, Role] = {
    RoleType.RULE_MAKER: Role(
        role_type=RoleType.RULE_MAKER,
        name="规则制定者",
        description="负责制定和修改灵族规则、标准、宪法",
        responsibilities=[
            "起草族规和治理标准",
            "参与宪法修改投票",
            "定义tier分类标准",
            "制定行为评估指标",
        ],
        conflicts_with=[RoleType.REFEREE, RoleType.PARTICIPANT],
        allowed_actions=["propose_rule", "amend_rule", "vote_rule_change"],
    ),
    RoleType.REFEREE: Role(
        role_type=RoleType.REFEREE,
        name="裁判",
        description="负责评估成员表现、分配tier、执行规则",
        responsibilities=[
            "评估成员表现",
            "分配tier等级",
            "执行治理检查",
            "处理违规行为",
        ],
        conflicts_with=[RoleType.RULE_MAKER, RoleType.PARTICIPANT, RoleType.SCORE_KEEPER],
        allowed_actions=["evaluate_member", "assign_tier", "check_governance", "penalize_member"],
    ),
    RoleType.SCORE_KEEPER: Role(
        role_type=RoleType.SCORE_KEEPER,
        name="计分员",
        description="负责统计数据、计算分数、生成报告",
        responsibilities=[
            "统计代码提交数",
            "统计测试数量",
            "计算贡献分数",
            "生成治理报告",
        ],
        conflicts_with=[RoleType.REFEREE, RoleType.PARTICIPANT],
        allowed_actions=["collect_stats", "calculate_score", "generate_report"],
    ),
    RoleType.PARTICIPANT: Role(
        role_type=RoleType.PARTICIPANT,
        name="参赛者",
        description="作为普通成员参与自治，被评估、被管理",
        responsibilities=[
            "遵守族规",
            "参与投票",
            "接受评估",
            "贡献代码",
        ],
        conflicts_with=[RoleType.RULE_MAKER, RoleType.REFEREE, RoleType.SCORE_KEEPER],
        allowed_actions=["vote", "propose", "contribute_code", "self_improve"],
    ),
}


class RoleConflictError(Exception):
    """角色冲突异常"""
    def __init__(self, agent_id: str, role_types: list[RoleType]) -> None:
        self.agent_id = agent_id
        self.role_types = role_types
        super().__init__(
            f"Agent '{agent_id}' has conflicting roles: "
            f"{', '.join(rt.value for rt in role_types)}"
        )


class RoleConflictChecker:
    """角色冲突检查器"""

    def __init__(self, agent_roles: list[AgentRoles]) -> None:
        self.agent_roles = agent_roles

    def check_conflicts(self) -> list[str]:
        """检查所有智能体的角色冲突，返回冲突列表"""
        conflicts = []

        for agent in self.agent_roles:
            if not agent.enabled:
                continue

            # 检查是否同时担任裁判和参赛者
            if agent.has_role(RoleType.REFEREE) and agent.has_role(RoleType.PARTICIPANT):
                conflicts.append(
                    f"Agent '{agent.agent_id}' 同时担任裁判和参赛者，存在利益冲突"
                )

            # 检查是否同时担任规则制定者和参赛者
            if agent.has_role(RoleType.RULE_MAKER) and agent.has_role(RoleType.PARTICIPANT):
                conflicts.append(
                    f"Agent '{agent.agent_id}' 同时担任规则制定者和参赛者，存在利益冲突"
                )

            # 检查是否同时担任裁判和计分员
            if agent.has_role(RoleType.REFEREE) and agent.has_role(RoleType.SCORE_KEEPER):
                conflicts.append(
                    f"Agent '{agent.agent_id}' 同时担任裁判和计分员，存在利益冲突"
                )

            # 检查是否同时担任规则制定者和裁判
            if agent.has_role(RoleType.RULE_MAKER) and agent.has_role(RoleType.REFEREE):
                conflicts.append(
                    f"Agent '{agent.agent_id}' 同时担任规则制定者和裁判，存在利益冲突"
                )

            # 灵克特殊检查：同时担任四个角色
            if agent.agent_id == "lingclaude":
                role_count = sum([
                    agent.has_role(RoleType.RULE_MAKER),
                    agent.has_role(RoleType.REFEREE),
                    agent.has_role(RoleType.SCORE_KEEPER),
                    agent.has_role(RoleType.PARTICIPANT),
                ])
                if role_count == 4:
                    conflicts.append(
                        f"Agent '{agent.agent_id}' 同时担任四个核心角色 "
                        f"（规则制定者、裁判、计分员、参赛者），"
                        f"存在严重的结构性利益冲突"
                    )

        return conflicts

    def validate_action(self, agent_id: str, action: str) -> dict[str, Any]:
        """验证智能体是否可以执行指定动作"""
        agent = next((a for a in self.agent_roles if a.agent_id == agent_id), None)

        if not agent:
            return {
                "allowed": False,
                "reason": f"Agent '{agent_id}' not found",
            }

        if not agent.enabled:
            return {
                "allowed": False,
                "reason": f"Agent '{agent_id}' is disabled",
            }

        # 检查是否允许执行该动作
        if not agent.can_perform_action(action):
            return {
                "allowed": False,
                "reason": f"Agent '{agent_id}' is not allowed to perform action '{action}'",
            }

        # 检查是否存在角色冲突
        conflicts = self.check_conflicts()
        if conflicts:
            # 找出与该智能体相关的冲突
            agent_conflicts = [c for c in conflicts if agent_id in c]
            if agent_conflicts:
                return {
                    "allowed": False,
                    "reason": f"Agent '{agent_id}' has role conflicts: {'; '.join(agent_conflicts)}",
                }

        return {
            "allowed": True,
        }


def create_lingclaude_role_separation() -> RoleConflictChecker:
    """创建灵克的角色分离配置

    目标：将灵克的四个角色分离到不同的智能体或组件
    """
    lingclaude_roles = AgentRoles(
        agent_id="lingclaude",
        roles=[
            ROLE_DEFINITIONS[RoleType.PARTICIPANT],
        ],
        enabled=True,
    )

    # 未来的理想配置（需要其他智能体配合）
    lingyan_roles = AgentRoles(
        agent_id="lingyan",
        roles=[
            ROLE_DEFINITIONS[RoleType.RULE_MAKER],
        ],
        enabled=True,
    )

    lingtong_roles = AgentRoles(
        agent_id="lingtong",
        roles=[
            ROLE_DEFINITIONS[RoleType.REFEREE],
        ],
        enabled=True,
    )

    lingzhi_roles = AgentRoles(
        agent_id="lingzhi",
        roles=[
            ROLE_DEFINITIONS[RoleType.SCORE_KEEPER],
        ],
        enabled=True,
    )

    lingclaude_roles = AgentRoles(
        agent_id="lingclaude",
        roles=[
            ROLE_DEFINITIONS[RoleType.PARTICIPANT],
        ],
        enabled=True,
    )

    return RoleConflictChecker(
        agent_roles=[
            lingclaude_roles,
            lingyan_roles,
            lingtong_roles,
            lingzhi_roles,
        ],
    )


def save_role_config(path: Path, checker: RoleConflictChecker | None = None) -> None:
    """保存角色配置到文件

    Args:
        path: 保存路径
        checker: 角色冲突检查器，如果为None则使用默认的灵克配置
    """
    import json

    if checker is None:
        checker = create_lingclaude_role_separation()

    config = {
        "version": "1.0.0",
        "created_at": "2026-04-21",
        "description": "灵族成员角色配置，用于角色分离和冲突检查",
        "agents": [],
        "conflicts": checker.check_conflicts(),
    }

    for agent in checker.agent_roles:
        agent_config = {
            "agent_id": agent.agent_id,
            "enabled": agent.enabled,
            "roles": [
                {
                    "role_type": role.role_type.value,
                    "name": role.name,
                    "description": role.description,
                    "responsibilities": role.responsibilities,
                }
                for role in agent.roles
            ],
        }
        config["agents"].append(agent_config)

    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def load_role_config(path: Path) -> RoleConflictChecker:
    """从文件加载角色配置"""
    import json

    data = json.loads(path.read_text(encoding="utf-8"))

    agent_roles = []
    for agent_config in data.get("agents", []):
        roles = []
        for role_config in agent_config.get("roles", []):
            role_type = RoleType(role_config["role_type"])
            roles.append(ROLE_DEFINITIONS[role_type])

        agent_roles.append(
            AgentRoles(
                agent_id=agent_config["agent_id"],
                roles=roles,
                enabled=agent_config["enabled"],
            )
        )

    return RoleConflictChecker(agent_roles=agent_roles)


# 类型注解导入
from typing import Any
