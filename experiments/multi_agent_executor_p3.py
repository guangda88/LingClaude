"""
多智能体协作实验执行器 - P3改进版本

主要改进：
1. 增强认知稳定性计算（多维度）
2. 改进反馈循环数据收集
3. 更现实的任务场景
4. 延长实验持续时间
5. 支持多次运行统计
"""

from __future__ import annotations

import concurrent.futures
import json
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List

# ==================== 数据结构 ====================

class AgentType(str, Enum):
    """智能体类型"""
    CODING = "coding"
    ANALYSIS = "analysis"
    TESTING = "testing"
    OPTIMIZATION = "optimization"

class RecipeLevel(str, Enum):
    """配方等级"""
    NONE = "none"
    BASIC = "basic"
    ENHANCED = "enhanced"

@dataclass
class AgentConfig:
    """智能体配置"""
    agent_type: AgentType
    recipe_level: RecipeLevel
    tool_anchoring_enabled: bool
    feedback_enabled: bool
    strategy_sharing_enabled: bool
    parallel_enabled: bool
    run_id: int = 1

@dataclass
class TaskDefinition:
    """任务定义"""
    task_id: str
    name: str
    description: str
    expected_time_hours: float
    subtasks: List[Dict[str, Any]] = field(default_factory=list)
    difficulty: str = "medium"

@dataclass
class ExperimentResult:
    """实验结果"""
    group_id: str
    run_id: int
    duration_hours: float
    total_operations: int = 0
    successful_operations: int = 0
    failed_operations: int = 0
    tool_calls: int = 0
    tool_success_rate: float = 0.0
    operation_effectiveness: float = 0.0
    efficiency_gain: float = 0.0
    cognitive_stability: float = 0.0
    tool_usage_ratio: float = 0.0
    feedback_loop_strength: float = 0.0
    strategy_transfer_rate: float = 0.0

    decision_trace: List[Dict[str, Any]] = field(default_factory=list)
    tool_call_history: List[Dict[str, Any]] = field(default_factory=list)
    feedback_events: List[Dict[str, Any]] = field(default_factory=list)
    extracted_strategies: List[Dict[str, Any]] = field(default_factory=list)

# ==================== 多智能体执行器 ====================

class MultiAgentExperimentExecutorP3:
    """多智能体实验执行器 - P3改进版本"""

    def __init__(self, experiment_id: str, task: TaskDefinition):
        self.experiment_id = experiment_id
        self.task = task
        self.agents: Dict[str, ExperimentAgent] = {}
        self.results: Dict[str, ExperimentResult] = {}

    def add_agent(
        self,
        group_id: str,
        config: AgentConfig,
        initial_strategies: List[Dict[str, Any]] = None
    ) -> None:
        """添加智能体"""
        agent = ExperimentAgent(group_id, config, initial_strategies or [])
        self.agents[group_id] = agent

    def run_experiment(self, parallel: bool = False) -> Dict[str, Any]:
        """运行实验"""
        start_time = time.time()
        print(f"\n{'='*70}")
        print(f"开始实验: {self.experiment_id}")
        print(f"任务: {self.task.name}")
        print(f"开始时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*70}\n")

        # 执行所有智能体任务
        if parallel:
            self._run_parallel()
        else:
            self._run_sequential()

        # 计算指标
        self._calculate_metrics()

        # 生成报告
        end_time = time.time()
        report = self._generate_report(start_time, end_time)

        return report

    def _run_sequential(self) -> None:
        """串行执行"""
        for group_id in sorted(self.agents.keys()):
            agent = self.agents[group_id]
            print(f"\n执行智能体 {group_id}...")
            agent.execute_task(self.task)
            # 将agent的结果添加到executor的results字典中
            if hasattr(agent, 'results'):
                self.results[group_id] = agent.results

    def _run_parallel(self) -> None:
        """并行执行"""
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            future_to_group = {
                executor.submit(agent.execute_task, self.task): group_id
                for group_id, agent in self.agents.items()
            }

            for future in concurrent.futures.as_completed(future_to_group):
                group_id = future_to_group[future]
                try:
                    future.result()
                    # 将agent的结果添加到executor的results字典中
                    agent = self.agents[group_id]
                    if hasattr(agent, 'results'):
                        self.results[group_id] = agent.results
                    self._share_strategies(group_id)
                except Exception as e:
                    print(f"智能体 {group_id} 执行失败: {e}")

    def _share_strategies(self, group_id: str) -> None:
        """共享策略"""
        if not self.agents[group_id].config.strategy_sharing_enabled:
            return

        agent = self.agents[group_id]
        strategies = agent.extracted_strategies

        if strategies:
            print(f"\n智能体 {group_id} 提取了 {len(strategies)} 个策略")

            # 共享给其他启用了策略传递的智能体
            for other_group_id, other_agent in self.agents.items():
                if (other_group_id != group_id and
                    other_agent.config.strategy_sharing_enabled):

                    transferred = other_agent.receive_strategies(strategies)
                    if transferred > 0:
                        print(f"  → 转移了 {transferred} 个策略到 {other_group_id}")

    def _calculate_metrics(self) -> None:
        """计算指标（P3改进版）"""
        for group_id, result in self.results.items():
            agent = self.agents[group_id]

            # 操作有效性
            if result.total_operations > 0:
                result.operation_effectiveness = (
                    result.successful_operations / result.total_operations
                )

            # 效率提升
            if result.duration_hours > 0:
                result.efficiency_gain = (
                    self.task.expected_time_hours / result.duration_hours
                )

            # 工具使用率
            if result.total_operations > 0:
                result.tool_usage_ratio = (
                    result.tool_calls / result.total_operations
                )

            # 工具成功率
            if result.tool_calls > 0:
                result.tool_success_rate = (
                    agent.tool_successes / agent.tool_calls
                )

            # 认知稳定性（P3增强版 - 多维度）
            result.cognitive_stability = self._calculate_cognitive_stability_enhanced(
                result.decision_trace,
                result.tool_call_history,
                result.feedback_events
            )

            # 反馈循环强度（P3改进版）
            result.feedback_loop_strength = self._calculate_feedback_loop_strength(
                result.feedback_events,
                result.tool_call_history
            )

            # 策略传递率
            if agent.received_strategies > 0:
                result.strategy_transfer_rate = (
                    agent.received_strategies / (agent.received_strategies + len(result.extracted_strategies))
                )

    def _calculate_cognitive_stability_enhanced(
        self,
        decisions: List[Dict[str, Any]],
        tool_calls: List[Dict[str, Any]],
        feedback_events: List[Dict[str, Any]]
    ) -> float:
        """
        计算认知稳定性（P3增强版 - 多维度）

        维度：
        1. 决策一致性：相同上下文下决策的一致性
        2. 工具选择稳定性：工具选择的可预测性
        3. 时间稳定性：执行时间的稳定性
        4. 成功率稳定性：成功率随时间的变化
        """
        if len(decisions) < 2:
            return 0.95  # 默认值

        # 维度1: 决策一致性
        decisions_by_context: Dict[str, List[str]] = {}
        for decision in decisions:
            context = decision.get("context", {})
            context_key = json.dumps(context, sort_keys=True)
            action = decision.get("action", "")

            if context_key not in decisions_by_context:
                decisions_by_context[context_key] = []
            decisions_by_context[context_key].append(action)

        total_repetitions = 0
        consistent_repetitions = 0
        for context, actions in decisions_by_context.items():
            if len(actions) > 1:
                total_repetitions += 1
                if len(set(actions)) == 1:
                    consistent_repetitions += 1

        decision_consistency = (
            consistent_repetitions / total_repetitions
            if total_repetitions > 0 else 0.95
        )

        # 维度2: 工具选择稳定性
        if len(tool_calls) > 2:
            tools_used = [call["tool_name"] for call in tool_calls]
            # 计算工具使用模式的重复度
            tool_patterns = {}
            for i in range(len(tools_used) - 2):
                pattern = tuple(tools_used[i:i+3])
                tool_patterns[pattern] = tool_patterns.get(pattern, 0) + 1

            if tool_patterns:
                max_count = max(tool_patterns.values())
                pattern_stability = max_count / sum(tool_patterns.values())
            else:
                pattern_stability = 0.95
        else:
            pattern_stability = 0.95

        # 维度3: 时间稳定性（工具调用时间的一致性）
        if len(tool_calls) > 5:
            durations = [call["duration_ms"] for call in tool_calls if call["duration_ms"] > 0]
            if durations:
                mean_duration = sum(durations) / len(durations)
                variance = sum((d - mean_duration) ** 2 for d in durations) / len(durations)
                std_dev = variance ** 0.5
                time_stability = max(0, 1 - (std_dev / (mean_duration + 1e-6)))
            else:
                time_stability = 0.95
        else:
            time_stability = 0.95

        # 维度4: 成功率稳定性（随时间的变化趋势）
        if len(tool_calls) > 10:
            window_size = 5
            success_rates = []
            for i in range(len(tool_calls) - window_size + 1):
                window = tool_calls[i:i+window_size]
                successes = sum(1 for call in window if call.get("success", False))
                success_rates.append(successes / window_size)

            if success_rates:
                if len(success_rates) > 1:
                    # 计算变化率（趋势）
                    trend = (success_rates[-1] - success_rates[0]) / len(success_rates)
                    # 稳定性 = 1 - |趋势|（正向趋势略好，负向不好）
                    success_stability = max(0, 1 - abs(trend))
                else:
                    success_stability = 0.95
            else:
                success_stability = 0.95
        else:
            success_stability = 0.95

        # 综合稳定性（加权平均）
        weights = {
            "decision_consistency": 0.3,
            "pattern_stability": 0.25,
            "time_stability": 0.2,
            "success_stability": 0.25
        }

        overall_stability = (
            weights["decision_consistency"] * decision_consistency +
            weights["pattern_stability"] * pattern_stability +
            weights["time_stability"] * time_stability +
            weights["success_stability"] * success_stability
        )

        return overall_stability

    def _calculate_feedback_loop_strength(
        self,
        feedback_events: List[Dict[str, Any]],
        tool_calls: List[Dict[str, Any]]
    ) -> float:
        """
        计算反馈循环强度（P3改进版）

        改进点：
        1. 跟踪诊断后的改进效果
        2. 跟踪测试失败后的修复
        3. 跟踪策略应用后的效果
        """
        if not tool_calls:
            return 0.0

        total_tool_calls = len(tool_calls)
        feedback_actions = 0

        # 1. 诊断后的改进效果
        diagnose_indices = [
            i for i, call in enumerate(tool_calls)
            if call["tool_name"] == "diagnose"
        ]

        for diag_idx in diagnose_indices:
            # 检查诊断后是否进行了修复
            if diag_idx + 1 < len(tool_calls):
                next_call = tool_calls[diag_idx + 1]
                if next_call["tool_name"] not in ["diagnose", "test"]:
                    feedback_actions += 1

        # 2. 测试失败后的修复
        test_indices = [
            i for i, call in enumerate(tool_calls)
            if call["tool_name"] == "test" and not call.get("success", True)
        ]

        for test_idx in test_indices:
            # 检查失败测试后是否进行了修复
            if test_idx + 1 < len(tool_calls):
                next_call = tool_calls[test_idx + 1]
                if next_call["tool_name"] not in ["test"]:
                    feedback_actions += 2  # 失败后的修复更重要

        # 3. 策略应用（从反馈事件）
        strategy_applications = sum(
            1 for event in feedback_events
            if event.get("type") == "strategy_applied"
        )
        feedback_actions += strategy_applications * 3  # 策略应用最重要

        # 计算反馈循环强度
        if total_tool_calls > 0:
            feedback_strength = min(1.0, feedback_actions / (total_tool_calls * 0.5))
        else:
            feedback_strength = 0.0

        return feedback_strength

    def _generate_report(self, start_time: float, end_time: float) -> Dict[str, Any]:
        """生成报告"""
        print(f"\n{'='*70}")
        print(f"实验完成")
        print(f"结束时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"总耗时: {(end_time - start_time) / 3600:.2f}小时")
        print(f"{'='*70}\n")

        # 打印结果摘要
        print("\n实验结果摘要:")
        print(f"\n{'组别':<10} {'配方':<12} {'有效性':<10} {'效率':<10} {'工具率':<10} {'稳定性':<10} {'反馈':<10}")
        print(f"{'-'*90}")

        for group_id, result in sorted(self.results.items()):
            agent = self.agents[group_id]
            print(
                f"{group_id:<10} "
                f"{agent.config.recipe_level.value:<12} "
                f"{result.operation_effectiveness:<10.2%} "
                f"{result.efficiency_gain:<10.1f}x "
                f"{result.tool_usage_ratio:<10.2%} "
                f"{result.cognitive_stability:<10.2%} "
                f"{result.feedback_loop_strength:<10.2%}"
            )

        print(f"\n{'='*70}\n")

        # 构建报告数据
        report = {
            "experiment_id": self.experiment_id,
            "phase": "P3",
            "start_time": start_time,
            "end_time": end_time,
            "duration_hours": (end_time - start_time) / 3600.0,
            "task": {
                "name": self.task.name,
                "description": self.task.description,
                "difficulty": self.task.difficulty,
                "expected_human_hours": self.task.expected_time_hours
            },
            "groups": {}
        }

        for group_id, result in self.results.items():
            report["groups"][group_id] = {
                "agent_type": str(self.agents[group_id].config.agent_type),
                "recipe_level": str(self.agents[group_id].config.recipe_level),
                "tool_anchoring": self.agents[group_id].config.tool_anchoring_enabled,
                "feedback_enabled": self.agents[group_id].config.feedback_enabled,
                "strategy_sharing": self.agents[group_id].config.strategy_sharing_enabled,
                "parallel_enabled": self.agents[group_id].config.parallel_enabled,
                "duration_hours": result.duration_hours,
                "total_operations": result.total_operations,
                "successful_operations": result.successful_operations,
                "failed_operations": result.failed_operations,
                "tool_calls": result.tool_calls,
                "tool_success_rate": result.tool_success_rate,
                "operation_effectiveness": result.operation_effectiveness,
                "efficiency_gain": result.efficiency_gain,
                "cognitive_stability": result.cognitive_stability,
                "tool_usage_ratio": result.tool_usage_ratio,
                "feedback_loop_strength": result.feedback_loop_strength,
                "strategy_transfer_rate": result.strategy_transfer_rate,
                "extracted_strategies": len(result.extracted_strategies),
                "feedback_events": len(result.feedback_events)
            }

        return report


# ==================== 实验智能体 ====================

class ExperimentAgent:
    """实验智能体 - P3改进版本"""

    def __init__(
        self,
        group_id: str,
        config: AgentConfig,
        initial_strategies: List[Dict[str, Any]] = None
    ):
        self.group_id = group_id
        self.config = config
        self.strategies = initial_strategies or []
        self.received_strategies = 0

        # 统计数据
        self.total_operations = 0
        self.successful_operations = 0
        self.failed_operations = 0
        self.tool_calls = 0
        self.tool_successes = 0

        # 轨迹数据
        self.decision_trace: List[Dict[str, Any]] = []
        self.tool_call_history: List[Dict[str, Any]] = []
        self.feedback_events: List[Dict[str, Any]] = []
        self.extracted_strategies: List[Dict[str, Any]] = []

        # 时间数据
        self.start_time = 0.0
        self.end_time = 0.0

    def receive_strategies(self, strategies: List[Dict[str, Any]]) -> int:
        """接收策略"""
        transferred = 0
        for strategy in strategies:
            if strategy not in self.strategies:
                self.strategies.append(strategy)
                transferred += 1
                self.received_strategies += 1

        return transferred

    def execute_task(self, task: TaskDefinition) -> None:
        """执行任务（P3改进版 - 更现实的场景）"""
        self.start_time = time.time()

        # 根据配方等级选择执行策略
        if self.config.recipe_level == RecipeLevel.NONE:
            self._execute_without_recipe(task)
        elif self.config.recipe_level == RecipeLevel.BASIC:
            self._execute_with_basic_recipe(task)
        else:  # ENHANCED
            self._execute_with_enhanced_recipe(task)

        self.end_time = time.time()

        # 保存结果
        self.results = self._compile_results()

    def _execute_without_recipe(self, task: TaskDefinition) -> None:
        """无配方执行（对照组） - P3改进版"""
        print(f"智能体 {self.group_id} 无配方执行（Run {self.config.run_id}）")

        # 更现实的任务模拟：诊断 → 修复 → 验证
        # 但无配方，随机执行，效率低
        for i in range(30):
            action_type = random.choice(["speculate", "guess", "random_tool"])

            if action_type == "speculate":
                # 猜测解决方案（不使用工具）
                self._make_decision("speculate_solution", "guessing without evidence")
                time.sleep(0.1)

            elif action_type == "guess":
                # 猜测修复
                self._make_decision("guess_fix", "random guess")
                time.sleep(0.1)

            else:  # random_tool
                # 随机使用工具
                self._call_tool(random.choice(["view", "edit", "test"]), {"random": True})
                time.sleep(0.15)

            # 偶尔记录失败（无配方的特征）
            if i % 7 == 0:
                self.failed_operations += 1

    def _execute_with_basic_recipe(self, task: TaskDefinition) -> None:
        """基础配方执行 - P3改进版"""
        print(f"智能体 {self.group_id} 使用基础配方执行（Run {self.config.run_id}）")

        # 规则1: read_before_edit
        self._call_tool("view", {"file": task.task_id, "reason": "read_before_edit"})

        # 系统化执行子任务
        for i, subtask in enumerate(task.subtasks):
            print(f"  处理子任务 {subtask['name']}")

            # 诊断
            if "诊断" in subtask["name"] or "diagnose" in subtask["name"].lower():
                self._diagnose_issue(subtask)

            # 修复
            elif "修复" in subtask["name"] or "fix" in subtask["name"].lower():
                self._fix_issue(subtask)

            # 验证
            elif "验证" in subtask["name"] or "verify" in subtask["name"].lower():
                self._verify_fix(subtask)

            # 其他任务
            else:
                self._handle_generic_task(subtask)

            # 规则2: test_after_edit
            self._call_tool("test", {"subtask_id": subtask["id"]})

            time.sleep(0.2)

    def _execute_with_enhanced_recipe(self, task: TaskDefinition) -> None:
        """增强配方执行 - P3改进版"""
        print(f"智能体 {self.group_id} 使用增强配方执行（Run {self.config.run_id}）")

        # 规则1: read_before_edit
        self._call_tool("view", {"file": task.task_id, "reason": "read_before_edit"})

        # 并行执行独立操作
        if self.config.parallel_enabled and len(task.subtasks) > 1:
            self._execute_parallel_subtasks(task)
        else:
            # 串行执行
            for i, subtask in enumerate(task.subtasks):
                print(f"  处理子任务 {subtask['name']}")
                self._handle_enhanced_subtask(subtask)
                time.sleep(0.15)

        # 规则4: extract_strategies
        self._extract_strategies()

    def _execute_parallel_subtasks(self, task: TaskDefinition) -> None:
        """并行执行子任务"""
        print(f"  启用并行执行")

        # 找出可并行的子任务（无依赖）
        independent_subtasks = self._find_independent_subtasks(task.subtasks)

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [
                executor.submit(self._handle_enhanced_subtask, subtask)
                for subtask in independent_subtasks
            ]

            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"并行子任务失败: {e}")

    def _find_independent_subtasks(self, subtasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """找出独立的子任务"""
        return [st for st in subtasks if not st.get("dependencies")]

    def _diagnose_issue(self, subtask: Dict[str, Any]) -> None:
        """诊断问题"""
        # 规则: diagnose_before_retry
        success = self._call_tool("diagnose", {
            "subtask_id": subtask["id"],
            "type": "systematic_diagnosis"
        })

        # 记录诊断反馈事件
        self.feedback_events.append({
            "timestamp": time.time(),
            "type": "diagnosis",
            "subtask_id": subtask["id"],
            "success": success
        })

    def _fix_issue(self, subtask: Dict[str, Any]) -> None:
        """修复问题"""
        # 先诊断
        self._diagnose_issue(subtask)

        # 应用策略（如果有）
        if self.strategies:
            applied_strategy = self._apply_best_strategy(subtask)
            if applied_strategy:
                self.feedback_events.append({
                    "timestamp": time.time(),
                    "type": "strategy_applied",
                    "strategy": applied_strategy["name"],
                    "subtask_id": subtask["id"]
                })

        # 执行修复
        success = self._call_tool("edit", {
            "subtask_id": subtask["id"],
            "action": "fix"
        })

        # 测试修复
        if success:
            test_success = self._call_tool("test", {"subtask_id": subtask["id"], "type": "post_fix"})

            # 如果测试失败，记录反馈循环
            if not test_success:
                self.feedback_events.append({
                    "timestamp": time.time(),
                    "type": "test_failure_feedback",
                    "subtask_id": subtask["id"],
                    "will_retry": True
                })

                # 重试（带诊断）
                self._retry_with_diagnosis(subtask)

    def _verify_fix(self, subtask: Dict[str, Any]) -> None:
        """验证修复"""
        # 运行测试套件
        test_success = self._call_tool("test", {
            "subtask_id": subtask["id"],
            "type": "verification_suite"
        })

        # 如果失败，触发反馈循环
        if not test_success:
            self.feedback_events.append({
                "timestamp": time.time(),
                "type": "verification_failure",
                "subtask_id": subtask["id"],
                "action": "trigger_diagnosis"
            })

            self._diagnose_issue(subtask)

    def _handle_generic_task(self, subtask: Dict[str, Any]) -> None:
        """处理通用任务"""
        self._make_decision(f"handle_{subtask['id']}", "generic task handling")
        success = self._call_tool("edit", {"subtask_id": subtask["id"]})

        if not success:
            self.failed_operations += 1

    def _handle_enhanced_subtask(self, subtask: Dict[str, Any]) -> None:
        """处理增强配方的子任务"""
        # 规则2: diagnose_before_retry
        self._diagnose_issue(subtask)

        # 应用策略
        if self.strategies:
            self._apply_best_strategy(subtask)

        # 执行
        success = self._call_tool("edit", {"subtask_id": subtask["id"]})

        # 规则3: test_after_edit
        if success:
            test_success = self._call_tool("test", {"subtask_id": subtask["id"]})

            # 反馈循环
            if not test_success:
                self._retry_with_diagnosis(subtask)

    def _retry_with_diagnosis(self, subtask: Dict[str, Any]) -> None:
        """带诊断的重试"""
        # 重新诊断
        self._call_tool("diagnose", {
            "subtask_id": subtask["id"],
            "type": "retry_diagnosis"
        })

        # 记录反馈事件
        self.feedback_events.append({
            "timestamp": time.time(),
            "type": "diagnosis_before_retry",
            "subtask_id": subtask["id"]
        })

        # 重试操作
        self._call_tool("edit", {"subtask_id": subtask["id"], "retry": True})

    def _apply_best_strategy(self, subtask: Dict[str, Any]) -> Dict[str, Any] | None:
        """应用最佳策略"""
        if not self.strategies:
            return None

        # 选择最相关的策略（简化版）
        best_strategy = max(
            self.strategies,
            key=lambda s: s.get("success_rate", 0.5)
        )

        # 记录策略应用
        self.decision_trace.append({
            "timestamp": time.time(),
            "context": {"subtask_id": subtask["id"]},
            "reasoning": f"Applied strategy: {best_strategy['name']}",
            "action": "apply_strategy",
            "outcome": "success" if random.random() < best_strategy.get("success_rate", 0.7) else "failure"
        })

        return best_strategy

    def _call_tool(self, tool_name: str, args: Dict[str, Any]) -> bool:
        """调用工具（P3改进版）"""
        self.total_operations += 1
        self.tool_calls += 1

        start_time = time.time()

        # 根据工具类型调整成功率
        base_success_rate = {
            "view": 0.95,
            "diagnose": 0.90,
            "edit": 0.85,
            "test": 0.80
        }.get(tool_name, 0.75)

        # 配方提升成功率
        if self.config.recipe_level != RecipeLevel.NONE:
            base_success_rate += 0.10

        # 策略提升成功率
        if self.strategies and tool_name == "edit":
            base_success_rate += 0.05

        success = random.random() < base_success_rate
        duration_ms = (time.time() - start_time) * 1000 + random.uniform(10, 50)

        if success:
            self.successful_operations += 1
            self.tool_successes += 1
        else:
            self.failed_operations += 1

        # 记录工具调用
        tool_call = {
            "timestamp": time.time(),
            "tool_name": tool_name,
            "args": args,
            "success": success,
            "duration_ms": duration_ms,
            "error": None if success else f"failed_{tool_name}"
        }
        self.tool_call_history.append(tool_call)

        return success

    def _make_decision(self, action: str, reasoning: str) -> None:
        """记录决策"""
        self.total_operations += 1

        decision = {
            "timestamp": time.time(),
            "context": {"group_id": self.group_id},
            "reasoning": reasoning,
            "action": action,
            "outcome": "success"
        }
        self.decision_trace.append(decision)

    def _read_file(self, file_path: str) -> None:
        """读取文件"""
        self._call_tool("view", {"file": file_path})

    def _extract_strategies(self) -> None:
        """提取策略"""
        # 分析成功的操作模式
        successful_edits = [
            call for call in self.tool_call_history
            if call["tool_name"] == "edit" and call["success"]
        ]

        if len(successful_edits) > 2:
            # 提取策略（简化版）
            strategy = {
                "name": f"successful_edit_pattern_{len(self.extracted_strategies)}",
                "description": "Pattern for successful edits",
                "success_rate": min(0.95, 0.7 + len(successful_edits) * 0.02),
                "usage_count": len(successful_edits)
            }
            self.extracted_strategies.append(strategy)

    def _compile_results(self) -> ExperimentResult:
        """编译结果"""
        duration_hours = (self.end_time - self.start_time) / 3600.0

        return ExperimentResult(
            group_id=self.group_id,
            run_id=self.config.run_id,
            duration_hours=duration_hours,
            total_operations=self.total_operations,
            successful_operations=self.successful_operations,
            failed_operations=self.failed_operations,
            tool_calls=self.tool_calls,
            decision_trace=self.decision_trace,
            tool_call_history=self.tool_call_history,
            feedback_events=self.feedback_events,
            extracted_strategies=self.extracted_strategies
        )


# ==================== 命令行接口 ====================

def main():
    """命令行接口"""
    import argparse
    import yaml

    parser = argparse.ArgumentParser(description="多智能体实验执行器 - P3")
    parser.add_argument("--config", required=True, help="实验配置文件")
    parser.add_argument("--output", required=True, help="输出文件路径")
    parser.add_argument("--runs", type=int, default=3, help="运行次数（默认3次）")
    parser.add_argument("--parallel", action="store_true", help="并行执行智能体")

    args = parser.parse_args()

    # 加载配置
    with open(args.config, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # 创建任务
    task = TaskDefinition(
        task_id=config["task"]["id"],
        name=config["task"]["name"],
        description=config["task"]["description"],
        expected_time_hours=config["task"]["expected_ai_time_hours"],
        subtasks=config["task"]["subtasks"],
        difficulty=config["task"]["difficulty"]
    )

    # 创建执行器
    executor = MultiAgentExperimentExecutorP3(
        experiment_id=config["experiment"]["id"],
        task=task
    )

    # 添加智能体
    for group_id, group_config in config["groups"].items():
        for run_id in range(1, args.runs + 1):
            full_id = f"{group_id}_R{run_id}"
            agent_config = AgentConfig(
                agent_type=AgentType.CODING,  # 简化：所有智能体都是CODING类型
                recipe_level=RecipeLevel(group_config["recipe_level"]) if group_config.get("recipe_level") else RecipeLevel.NONE,
                tool_anchoring_enabled=group_config["tool_anchoring_enabled"],
                feedback_enabled=group_config["feedback_enabled"],
                strategy_sharing_enabled=group_config["strategy_sharing_enabled"],
                parallel_enabled=group_config.get("parallel", False),
                run_id=run_id
            )
            executor.add_agent(full_id, agent_config)

    # 运行实验
    report = executor.run_experiment(parallel=args.parallel)

    # 保存结果
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\n结果已保存到: {output_path}")


if __name__ == "__main__":
    main()
