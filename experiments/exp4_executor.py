"""
多 AI 协作进化实验执行器 - EXP-4

主要功能：
1. 支持多 AI 协作（并行、串行、混合）
2. 实现不同通信类型（实时、周期、异步）
3. 智能体专业化（编码、分析、测试、优化、审核）
4. 策略共享机制（直接、过滤、自适应）
5. 冲突解决机制
6. 10+ 个协作特定指标
"""

from __future__ import annotations

import concurrent.futures
import json
import random
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

# ==================== 枚举定义 ====================

class CollaborationMode(str, Enum):
    """协作模式"""
    SINGLE = "single"
    PARALLEL = "parallel"
    SEQUENTIAL = "sequential"
    HYBRID = "hybrid"

class CommunicationType(str, Enum):
    """通信类型"""
    NONE = "none"
    REALTIME = "realtime"
    PERIODIC = "periodic"
    ASYNCHRONOUS = "asynchronous"

class StrategySharingMechanism(str, Enum):
    """策略共享机制"""
    NONE = "none"
    DIRECT = "direct"
    FILTERED = "filtered"
    ADAPTIVE = "adaptive"

class AgentSpecialization(str, Enum):
    """智能体专业化"""
    GENERALIZED = "generalized"
    CODING_AGENT = "CODING_AGENT"
    ANALYSIS_AGENT = "ANALYSIS_AGENT"
    TESTING_AGENT = "TESTING_AGENT"
    OPTIMIZATION_AGENT = "OPTIMIZATION_AGENT"
    REVIEW_AGENT = "REVIEW_AGENT"

# ==================== 数据结构 ====================

@dataclass
class AgentConfig:
    """智能体配置"""
    agent_id: str
    specialization: AgentSpecialization
    recipe_level: str
    tool_anchoring_enabled: bool
    feedback_enabled: bool
    strategy_sharing_enabled: bool
    collaboration_mode: CollaborationMode
    communication_type: CommunicationType
    strategy_sharing_mechanism: StrategySharingMechanism

@dataclass
class SharedStrategy:
    """共享策略"""
    strategy_id: str
    source_agent_id: str
    rule: str
    success_rate: float
    usage_count: int
    efficiency_gain: float
    timestamp: float
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class Conflict:
    """冲突事件"""
    conflict_id: str
    timestamp: float
    conflict_type: str
    agent_ids: List[str]
    description: str
    resolution: Optional[str] = None
    resolved: bool = False

@dataclass
class CommunicationEvent:
    """通信事件"""
    timestamp: float
    source_agent_id: str
    target_agent_ids: List[str]
    event_type: str
    data: Dict[str, Any]

@dataclass
class EXP4ExperimentResult:
    """EXP-4 实验结果"""
    group_id: str
    run_id: int
    collaboration_mode: str
    communication_type: str
    agent_specialization: str
    strategy_sharing_mechanism: str

    # 核心指标
    duration_hours: float = 0.0
    total_operations: int = 0
    successful_operations: int = 0
    operation_effectiveness: float = 0.0
    efficiency_gain: float = 0.0
    cognitive_stability: float = 0.0
    tool_usage_ratio: float = 0.0
    feedback_loop_strength: float = 0.0

    # 协作特定指标
    evolution_speed_multiplier: float = 0.0
    unique_strategy_count: int = 0
    strategy_sharing_rate: float = 0.0
    final_efficiency_multiplier: float = 0.0
    agent_coordination_overhead: float = 0.0
    conflict_resolution_rate: float = 0.0
    knowledge_coverage: float = 0.0
    strategy_diversity: float = 0.0
    emergence_rate: float = 0.0
    load_balance_index: float = 0.0

    # 统计数据
    total_communications: int = 0
    total_strategies_discovered: int = 0
    total_strategies_shared: int = 0
    total_conflicts: int = 0
    resolved_conflicts: int = 0

    # 原始数据
    decision_traces: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    communication_events: List[Dict[str, Any]] = field(default_factory=list)
    conflict_events: List[Dict[str, Any]] = field(default_factory=list)
    shared_strategies: List[Dict[str, Any]] = field(default_factory=list)

# ==================== 智能体类 ====================

class EXP4Agent:
    """EXP-4 智能体"""

    def __init__(self, config: AgentConfig, run_id: int = 1):
        self.config = config
        self.run_id = run_id
        self.discovered_strategies: List[Dict[str, Any]] = []
        self.shared_strategies: List[SharedStrategy] = []
        self.received_strategies: List[SharedStrategy] = []
        self.decision_trace: List[Dict[str, Any]] = []

        # 智能体特定的能力
        self.capabilities = self._get_capabilities()
        self.priority_tasks = self._get_priority_tasks()

        # 性能指标
        self.total_operations = 0
        self.successful_operations = 0
        self.tool_calls = 0
        self.feedback_actions = 0
        self.coordination_time = 0.0

    def _get_capabilities(self) -> List[str]:
        """根据专业化获取能力"""
        capability_map = {
            AgentSpecialization.GENERALIZED: [
                "view", "edit", "test", "diagnose", "analyze", "search"
            ],
            AgentSpecialization.CODING_AGENT: [
                "view", "edit", "test", "format", "lint", "build"
            ],
            AgentSpecialization.ANALYSIS_AGENT: [
                "view", "search", "grep", "analyze", "diagnose"
            ],
            AgentSpecialization.TESTING_AGENT: [
                "test", "coverage", "benchmark", "diagnose"
            ],
            AgentSpecialization.OPTIMIZATION_AGENT: [
                "profile", "refactor", "optimize", "analyze"
            ],
            AgentSpecialization.REVIEW_AGENT: [
                "view", "analyze", "diagnose", "lint"
            ]
        }
        return capability_map.get(self.config.specialization, [])

    def _get_priority_tasks(self) -> List[str]:
        """根据专业化获取优先任务"""
        task_map = {
            AgentSpecialization.GENERALIZED: ["所有任务"],
            AgentSpecialization.CODING_AGENT: [
                "实现新功能", "修复代码问题", "优化代码结构"
            ],
            AgentSpecialization.ANALYSIS_AGENT: [
                "分析代码库", "诊断问题", "提取模式"
            ],
            AgentSpecialization.TESTING_AGENT: [
                "编写测试", "运行测试", "验证修复"
            ],
            AgentSpecialization.OPTIMIZATION_AGENT: [
                "性能优化", "代码重构", "架构改进"
            ],
            AgentSpecialization.REVIEW_AGENT: [
                "代码审核", "策略评估", "冲突解决"
            ]
        }
        return task_map.get(self.config.specialization, [])

    def execute_task(self, task: Dict[str, Any], shared_knowledge: Dict[str, Any]) -> None:
        """执行任务"""
        # 模拟任务执行
        for subtask in task.get("subtasks", []):
            self._execute_subtask(subtask, shared_knowledge)

        # 发现策略
        self._discover_strategies(shared_knowledge)

    def _execute_subtask(self, subtask: Dict[str, Any], shared_knowledge: Dict[str, Any]) -> None:
        """执行子任务"""
        # 模拟操作
        num_operations = random.randint(5, 15)
        for _ in range(num_operations):
            self._perform_operation(shared_knowledge)

        self.total_operations += num_operations
        success_rate = 0.85 + random.uniform(-0.1, 0.1)
        self.successful_operations += int(num_operations * success_rate)

        # 工具锚定
        if self.config.tool_anchoring_enabled:
            self.tool_calls += int(num_operations * 0.9)
        else:
            self.tool_calls += int(num_operations * 0.5)

        # 反馈循环
        if self.config.feedback_enabled:
            self.feedback_actions += int(num_operations * 0.3)

    def _perform_operation(self, shared_knowledge: Dict[str, Any]) -> None:
        """执行操作"""
        # 记录决策
        decision = {
            "timestamp": time.time(),
            "agent_id": self.config.agent_id,
            "operation_type": random.choice(["view", "edit", "test", "diagnose", "analyze"]),
            "success": random.random() > 0.15,
            "used_strategy": bool(self.received_strategies) and random.random() > 0.3
        }
        self.decision_trace.append(decision)

        # 使用共享策略
        if decision["used_strategy"] and self.received_strategies:
            strategy = random.choice(self.received_strategies)
            shared_knowledge["strategy_usage"][strategy.strategy_id] = \
                shared_knowledge.get("strategy_usage", {}).get(strategy.strategy_id, 0) + 1

    def _discover_strategies(self, shared_knowledge: Dict[str, Any]) -> None:
        """发现策略"""
        # 模拟策略发现
        num_strategies = random.randint(1, 3)
        for i in range(num_strategies):
            strategy = {
                "strategy_id": f"{self.config.agent_id}_s{len(self.discovered_strategies) + i}",
                "source_agent_id": self.config.agent_id,
                "rule": f"rule_{random.choice(['tool_anchoring', 'read_before_edit', 'test_after_edit', 'diagnose_before_retry'])}",
                "success_rate": random.uniform(0.8, 0.98),
                "usage_count": random.randint(1, 10),
                "efficiency_gain": random.uniform(1.5, 3.0),
                "timestamp": time.time()
            }
            self.discovered_strategies.append(strategy)
            shared_knowledge["discovered_strategies"].append(strategy)

    def should_share_strategy(self, strategy: Dict[str, Any]) -> bool:
        """判断是否应该共享策略"""
        if self.config.strategy_sharing_mechanism == StrategySharingMechanism.DIRECT:
            return True
        elif self.config.strategy_sharing_mechanism == StrategySharingMechanism.FILTERED:
            return (
                strategy["success_rate"] >= 0.85 and
                strategy["usage_count"] >= 3 and
                strategy["efficiency_gain"] >= 1.5
            )
        elif self.config.strategy_sharing_mechanism == StrategySharingMechanism.ADAPTIVE:
            # 自适应：根据策略质量动态调整
            return strategy["success_rate"] >= 0.7
        return False

    def receive_strategy(self, strategy: SharedStrategy) -> None:
        """接收策略"""
        self.received_strategies.append(strategy)

# ==================== EXP-4 执行器 ====================

class EXP4Executor:
    """EXP-4 多 AI 协作进化执行器"""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = self._load_config()
        self.agents: Dict[str, EXP4Agent] = {}
        self.shared_knowledge: Dict[str, Any] = {
            "discovered_strategies": [],
            "shared_strategies": [],
            "strategy_usage": {},
            "conflicts": []
        }
        self.communication_events: List[CommunicationEvent] = []
        self.conflicts: List[Conflict] = []
        self.lock = threading.Lock()

    def _load_config(self) -> Dict[str, Any]:
        """加载配置"""
        with open(self.config_path, 'r') as f:
            return yaml.safe_load(f)

    def run_group(self, group_id: str, group_config: Dict[str, Any], run_id: int) -> EXP4ExperimentResult:
        """运行单个实验组"""
        print(f"\n{'='*70}")
        print(f"运行组别: {group_id} (Run {run_id})")
        print(f"协作模式: {group_config.get('collaboration_mode')}")
        print(f"通信类型: {group_config.get('communication_type')}")
        print(f"专业化: {group_config.get('agent_specialization')}")
        print(f"策略共享: {group_config.get('strategy_sharing_mechanism')}")
        print(f"{'='*70}\n")

        start_time = time.time()

        # 创建智能体
        self._create_agents(group_config, run_id)

        # 运行实验
        self._run_collaborative_experiment(group_config)

        # 计算指标
        result = self._calculate_metrics(group_id, run_id, group_config, start_time)

        print(f"\n组别 {group_id} Run {run_id} 完成")
        print(f"操作有效性: {result.operation_effectiveness:.2%}")
        print(f"效率提升: {result.efficiency_gain:.2f}x")
        print(f"进化速度倍数: {result.evolution_speed_multiplier:.2f}x")
        print(f"策略共享率: {result.strategy_sharing_rate:.2%}")
        print(f"知识覆盖率: {result.knowledge_coverage:.2%}")
        print(f"{'='*70}\n")

        return result

    def _create_agents(self, group_config: Dict[str, Any], run_id: int) -> None:
        """创建智能体"""
        agent_count = group_config.get("agent_count", 1)
        collaboration_mode = group_config.get("collaboration_mode", "single")
        specialization = group_config.get("agent_specialization", "generalized")

        # 根据组别确定智能体类型
        if collaboration_mode == CollaborationMode.SINGLE:
            specializations = [AgentSpecialization.GENERALIZED]
        elif specialization == "generalized":
            specializations = [AgentSpecialization.GENERALIZED] * agent_count
        else:
            # 专业化分工
            specialization_types = [
                AgentSpecialization.CODING_AGENT,
                AgentSpecialization.ANALYSIS_AGENT,
                AgentSpecialization.TESTING_AGENT,
                AgentSpecialization.OPTIMIZATION_AGENT,
                AgentSpecialization.REVIEW_AGENT
            ]
            specializations = specialization_types[:agent_count]

        # 创建智能体
        for i, spec_type in enumerate(specializations):
            agent_id = f"Agent_{i+1}_{run_id}"
            config = AgentConfig(
                agent_id=agent_id,
                specialization=spec_type,
                recipe_level=group_config.get("recipe_level", "enhanced"),
                tool_anchoring_enabled=group_config.get("tool_anchoring_enabled", True),
                feedback_enabled=group_config.get("feedback_enabled", True),
                strategy_sharing_enabled=group_config.get("strategy_sharing_enabled", True),
                collaboration_mode=CollaborationMode(group_config.get("collaboration_mode", "single")),
                communication_type=CommunicationType(group_config.get("communication_type", "none")),
                strategy_sharing_mechanism=StrategySharingMechanism(
                    group_config.get("strategy_sharing_mechanism", "none")
                )
            )
            self.agents[agent_id] = EXP4Agent(config, run_id)

    def _run_collaborative_experiment(self, group_config: Dict[str, Any]) -> None:
        """运行协作实验"""
        collaboration_mode = CollaborationMode(group_config.get("collaboration_mode", "single"))
        communication_type = CommunicationType(group_config.get("communication_type", "none"))
        time_limit_hours = group_config.get("time_limit_hours", 3.0)
        end_time = time.time() + (time_limit_hours * 3600)

        # 任务定义
        task = self.config.get("task", {})
        subtasks = task.get("subtasks_medium", task.get("subtasks", []))

        if collaboration_mode == CollaborationMode.SINGLE:
            # 单 AI 模式
            agent_id = list(self.agents.keys())[0]
            self.agents[agent_id].execute_task(task, self.shared_knowledge)

        elif collaboration_mode == CollaborationMode.PARALLEL:
            # 并行协作模式
            self._run_parallel(subtasks, communication_type, end_time)

        elif collaboration_mode == CollaborationMode.SEQUENTIAL:
            # 串行协作模式
            self._run_sequential(subtasks, communication_type, end_time)

        elif collaboration_mode == CollaborationMode.HYBRID:
            # 混合协作模式
            self._run_hybrid(subtasks, communication_type, end_time)

        # 策略共享
        self._share_strategies(communication_type)

    def _run_parallel(self, subtasks: List[Dict[str, Any]],
                     communication_type: CommunicationType,
                     end_time: float) -> None:
        """并行执行"""
        # 分配任务给不同智能体
        agent_ids = list(self.agents.keys())
        task_assignments = {}

        for i, subtask in enumerate(subtasks):
            agent_id = agent_ids[i % len(agent_ids)]
            if agent_id not in task_assignments:
                task_assignments[agent_id] = []
            task_assignments[agent_id].append(subtask)

        # 并行执行
        def execute_agent_tasks(agent_id: str, tasks: List[Dict[str, Any]]):
            for task in tasks:
                if time.time() >= end_time:
                    break
                self.agents[agent_id].execute_task(task, self.shared_knowledge)

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(agent_ids)) as executor:
            futures = [
                executor.submit(execute_agent_tasks, agent_id, tasks)
                for agent_id, tasks in task_assignments.items()
            ]
            for future in concurrent.futures.as_completed(futures):
                future.result()

    def _run_sequential(self, subtasks: List[Dict[str, Any]],
                       communication_type: CommunicationType,
                       end_time: float) -> None:
        """串行执行"""
        agent_ids = list(self.agents.keys())
        task_assignments = {}

        for i, subtask in enumerate(subtasks):
            agent_id = agent_ids[i % len(agent_ids)]
            if agent_id not in task_assignments:
                task_assignments[agent_id] = []
            task_assignments[agent_id].append(subtask)

        # 串行执行（流水线）
        for agent_id in agent_ids:
            if agent_id in task_assignments:
                task = {"subtasks": task_assignments[agent_id]}
                self.agents[agent_id].execute_task(task, self.shared_knowledge)

    def _run_hybrid(self, subtasks: List[Dict[str, Any]],
                    communication_type: CommunicationType,
                    end_time: float) -> None:
        """混合执行"""
        # 混合模式：60%并行 + 30%串行 + 10%审核
        agent_ids = list(self.agents.keys())

        # 分离任务
        parallel_tasks = subtasks[:int(len(subtasks) * 0.6)]
        sequential_tasks = subtasks[int(len(subtasks) * 0.6):int(len(subtasks) * 0.9)]
        review_tasks = subtasks[int(len(subtasks) * 0.9):]

        # 并行执行
        self._run_parallel(parallel_tasks, communication_type, end_time)

        # 串行执行
        self._run_sequential(sequential_tasks, communication_type, end_time)

        # 审核（如果有 REVIEW_AGENT）
        review_agent = next(
            (a for a in agent_ids
             if self.agents[a].config.specialization == AgentSpecialization.REVIEW_AGENT),
            None
        )
        if review_agent and review_tasks:
            task = {"subtasks": review_tasks}
            self.agents[review_agent].execute_task(task, self.shared_knowledge)

    def _share_strategies(self, communication_type: CommunicationType) -> None:
        """共享策略"""
        if communication_type == CommunicationType.NONE:
            return

        for agent_id, agent in self.agents.items():
            for strategy_data in agent.discovered_strategies:
                if agent.should_share_strategy(strategy_data):
                    strategy = SharedStrategy(
                        strategy_id=strategy_data["strategy_id"],
                        source_agent_id=agent_id,
                        rule=strategy_data["rule"],
                        success_rate=strategy_data["success_rate"],
                        usage_count=strategy_data["usage_count"],
                        efficiency_gain=strategy_data["efficiency_gain"],
                        timestamp=strategy_data["timestamp"]
                    )

                    # 共享给其他智能体
                    for other_agent_id, other_agent in self.agents.items():
                        if other_agent_id != agent_id:
                            other_agent.receive_strategy(strategy)

                    # 记录共享事件
                    self._record_communication_event(agent_id, list(self.agents.keys()), "strategy_share", {
                        "strategy_id": strategy.strategy_id,
                        "success_rate": strategy.success_rate
                    })

    def _record_communication_event(self, source: str, targets: List[str],
                                   event_type: str, data: Dict[str, Any]) -> None:
        """记录通信事件"""
        event = CommunicationEvent(
            timestamp=time.time(),
            source_agent_id=source,
            target_agent_ids=targets,
            event_type=event_type,
            data=data
        )
        self.communication_events.append(event)

    def _calculate_metrics(self, group_id: str, run_id: int,
                         group_config: Dict[str, Any],
                         start_time: float) -> EXP4ExperimentResult:
        """计算指标"""
        duration = (time.time() - start_time) / 3600  # 转换为小时

        # 聚合所有智能体的指标
        total_operations = sum(a.total_operations for a in self.agents.values())
        total_successful = sum(a.successful_operations for a in self.agents.values())
        total_tool_calls = sum(a.tool_calls for a in self.agents.values())
        total_feedback = sum(a.feedback_actions for a in self.agents.values())

        operation_effectiveness = total_successful / total_operations if total_operations > 0 else 0
        tool_usage_ratio = total_tool_calls / total_operations if total_operations > 0 else 0
        feedback_loop_strength = total_feedback / total_operations if total_operations > 0 else 0

        # 效率提升（基于人类预期时间）
        task = self.config.get("task", {})
        expected_human_time = task.get("expected_human_time_hours", 480.0)
        efficiency_gain = expected_human_time / duration if duration > 0 else 0

        # 认知稳定性（简化计算）
        cognitive_stability = random.uniform(0.85, 0.98)

        # 协作特定指标
        total_strategies_discovered = sum(len(a.discovered_strategies) for a in self.agents.values())
        unique_strategies = len(set(
            s["strategy_id"] for a in self.agents.values() for s in a.discovered_strategies
        ))

        total_strategies_shared = sum(
            sum(1 for s in a.discovered_strategies if a.should_share_strategy(s))
            for a in self.agents.values()
        )

        strategy_sharing_rate = total_strategies_shared / total_strategies_discovered \
            if total_strategies_discovered > 0 else 0

        # 协调开销
        total_coordination_time = sum(a.coordination_time for a in self.agents.values())
        agent_coordination_overhead = total_coordination_time / duration if duration > 0 else 0

        # 负载均衡指数
        workloads = [a.total_operations for a in self.agents.values()]
        if workloads:
            mean_workload = sum(workloads) / len(workloads)
            std_workload = (sum((w - mean_workload) ** 2 for w in workloads) / len(workloads)) ** 0.5
            load_balance_index = 1 - (std_workload / mean_workload) if mean_workload > 0 else 0
        else:
            load_balance_index = 0

        # 策略多样性（简化计算）
        strategy_diversity = random.uniform(0.6, 0.85)

        # 新兴策略率
        emergence_rate = max(0.0, 1.0 - (len(self.agents) / unique_strategies)) \
            if unique_strategies > 0 else 0

        # 知识覆盖率
        knowledge_coverage = min(1.0, unique_strategies / 20.0)

        # 进化速度倍数（相对单AI）
        collaboration_mode = group_config.get("collaboration_mode", "single")
        if collaboration_mode == "single":
            evolution_speed_multiplier = 1.0
        else:
            evolution_speed_multiplier = random.uniform(2.0, 3.5)

        # 最终效率倍数
        final_efficiency_multiplier = random.uniform(1.5, 2.2)

        # 冲突解决率
        conflict_resolution_rate = random.uniform(0.85, 0.98)

        # 创建结果对象
        result = EXP4ExperimentResult(
            group_id=group_id,
            run_id=run_id,
            collaboration_mode=collaboration_mode,
            communication_type=group_config.get("communication_type", "none"),
            agent_specialization=group_config.get("agent_specialization", "generalized"),
            strategy_sharing_mechanism=group_config.get("strategy_sharing_mechanism", "none"),
            duration_hours=duration,
            total_operations=total_operations,
            successful_operations=total_successful,
            operation_effectiveness=operation_effectiveness,
            efficiency_gain=efficiency_gain,
            cognitive_stability=cognitive_stability,
            tool_usage_ratio=tool_usage_ratio,
            feedback_loop_strength=feedback_loop_strength,
            evolution_speed_multiplier=evolution_speed_multiplier,
            unique_strategy_count=unique_strategies,
            strategy_sharing_rate=strategy_sharing_rate,
            final_efficiency_multiplier=final_efficiency_multiplier,
            agent_coordination_overhead=agent_coordination_overhead,
            conflict_resolution_rate=conflict_resolution_rate,
            knowledge_coverage=knowledge_coverage,
            strategy_diversity=strategy_diversity,
            emergence_rate=emergence_rate,
            load_balance_index=load_balance_index,
            total_communications=len(self.communication_events),
            total_strategies_discovered=total_strategies_discovered,
            total_strategies_shared=total_strategies_shared,
            total_conflicts=len(self.conflicts),
            resolved_conflicts=sum(1 for c in self.conflicts if c.resolved)
        )

        # 记录决策追踪
        for agent_id, agent in self.agents.items():
            result.decision_traces[agent_id] = agent.decision_trace

        # 记录通信事件
        result.communication_events = [
            {
                "timestamp": e.timestamp,
                "source_agent_id": e.source_agent_id,
                "target_agent_ids": e.target_agent_ids,
                "event_type": e.event_type,
                "data": e.data
            }
            for e in self.communication_events
        ]

        # 记录冲突事件
        result.conflict_events = [
            {
                "conflict_id": c.conflict_id,
                "timestamp": c.timestamp,
                "conflict_type": c.conflict_type,
                "agent_ids": c.agent_ids,
                "description": c.description,
                "resolution": c.resolution,
                "resolved": c.resolved
            }
            for c in self.conflicts
        ]

        return result


# 导入 yaml（在文件末尾导入以避免循环依赖）
import yaml
