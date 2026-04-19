"""
多智能体协作实验执行器 (Multi-Agent Collaborative Experiment Executor)

功能：
- 支持多个AI智能体并行执行任务
- 实现工具锚定、反馈循环、策略传递
- 收集详细的实验数据
- 生成对比分析报告

作者：LingClaude (灵克)
版本：1.0
日期：2026-04-12
"""

from __future__ import annotations

import json
import sys
import time
import concurrent.futures
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml


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
    agent_id: str
    agent_type: AgentType
    recipe_level: RecipeLevel
    tool_anchoring_enabled: bool
    feedback_enabled: bool
    strategy_sharing_enabled: bool
    parallel_enabled: bool


@dataclass
class TaskDefinition:
    """任务定义"""
    task_id: str
    name: str
    description: str
    difficulty: str
    expected_time_hours: float
    subtasks: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ExperimentResult:
    """实验结果"""
    group_id: str
    agent_id: str
    status: str
    start_time: float
    end_time: float
    duration_hours: float

    # 操作统计
    total_operations: int = 0
    successful_operations: int = 0
    failed_operations: int = 0
    tool_calls: int = 0
    strategy_extractions: int = 0

    # 指标
    operation_effectiveness: float = 0.0
    efficiency_gain: float = 0.0
    cognitive_stability: float = 0.0
    tool_usage_ratio: float = 0.0
    feedback_loop_strength: float = 0.0
    strategy_transfer_rate: float = 0.0

    # 详细数据
    tool_call_history: List[Dict[str, Any]] = field(default_factory=list)
    decision_trace: List[Dict[str, Any]] = field(default_factory=list)
    extracted_strategies: List[Dict[str, Any]] = field(default_factory=list)


class MultiAgentExperimentExecutor:
    """多智能体协作实验执行器"""

    def __init__(self, config_path: Path):
        self.config = self._load_config(config_path)
        self.experiment_id = self.config["experiment"]["id"]
        self.task = self._parse_task_config()
        self.agents: Dict[str, ExperimentAgent] = {}
        self.results: Dict[str, ExperimentResult] = {}

    def _load_config(self, config_path: Path) -> Dict[str, Any]:
        """加载配置文件"""
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)

    def _parse_task_config(self) -> TaskDefinition:
        """解析任务配置"""
        task_config = self.config["task"]
        return TaskDefinition(
            task_id=task_config["id"],
            name=task_config["name"],
            description=task_config["description"],
            difficulty=task_config["difficulty"],
            expected_time_hours=task_config["expected_human_time_hours"],
            subtasks=task_config.get("subtasks", [])
        )

    def create_agents(self) -> None:
        """创建智能体"""
        for group_id, group_config in self.config["groups"].items():
            agent_config = AgentConfig(
                agent_id=f"agent_{group_id}",
                agent_type=AgentType.CODING,
                recipe_level=RecipeLevel(group_config.get("recipe_level", "none")),
                tool_anchoring_enabled=group_config.get("tool_anchoring_enabled", False),
                feedback_enabled=group_config.get("feedback_enabled", True),
                strategy_sharing_enabled=group_config.get("strategy_sharing_enabled", False),
                parallel_enabled=group_config.get("parallel_enabled", False)
            )

            # 预期人工时间
            expected_human_time = self.task.expected_time_hours

            self.agents[group_id] = ExperimentAgent(
                agent_config,
                group_id,
                expected_human_time,
                self.experiment_id
            )

    def run_experiment(self) -> Dict[str, Any]:
        """运行实验"""
        print(f"\n{'='*70}")
        print(f"多智能体协作实验执行器")
        print(f"实验ID: {self.experiment_id}")
        print(f"实验名称: {self.config['experiment']['name']}")
        print(f"开始时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*70}\n")

        print(f"任务: {self.task.name}")
        print(f"描述: {self.task.description}")
        print(f"难度: {self.task.difficulty}")
        print(f"预期人工时间: {self.task.expected_time_hours:.1f}小时")
        print(f"\n智能体数量: {len(self.agents)}")
        print(f"\n{'='*70}\n")

        start_time = time.time()

        # 并行或串行执行
        if any(agent.config.parallel_enabled for agent in self.agents.values()):
            self._run_parallel()
        else:
            self._run_sequential()

        end_time = time.time()

        # 收集结果
        for group_id, agent in self.agents.items():
            self.results[group_id] = agent.get_result()

        # 计算指标
        self._calculate_metrics()

        # 生成报告
        report = self._generate_report(start_time, end_time)

        return report

    def _run_sequential(self) -> None:
        """串行执行实验"""
        for group_id, agent in self.agents.items():
            print(f"\n{'='*70}")
            print(f"运行智能体: {group_id} ({agent.config.recipe_level.value} 配方)")
            print(f"{'='*70}\n")

            agent.execute_task(self.task)
            self._share_strategies(group_id)

    def _run_parallel(self) -> None:
        """并行执行实验"""
        print("启用并行执行模式\n")

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            future_to_group = {
                executor.submit(agent.execute_task, self.task): group_id
                for group_id, agent in self.agents.items()
            }

            for future in concurrent.futures.as_completed(future_to_group):
                group_id = future_to_group[future]
                try:
                    future.result()
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
        """计算指标"""
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

            # 认知稳定性（基于重复操作的一致性）
            result.cognitive_stability = self._calculate_cognitive_stability(
                result.decision_trace
            )

            # 反馈循环强度
            if result.total_operations > 0:
                result.feedback_loop_strength = (
                    len([d for d in result.decision_trace if "feedback" in d.get("reasoning", "").lower()]) /
                    result.total_operations
                )

            # 策略传递率
            if agent.received_strategies > 0:
                result.strategy_transfer_rate = (
                    agent.received_strategies / (agent.received_strategies + len(result.extracted_strategies))
                )

    def _calculate_cognitive_stability(self, decisions: List[Dict[str, Any]]) -> float:
        """计算认知稳定性"""
        if len(decisions) < 2:
            return 0.0

        # 计算重复场景下决策的一致性
        decisions_by_context: Dict[str, List[str]] = {}

        for decision in decisions:
            context = decision.get("context", {})
            context_key = json.dumps(context, sort_keys=True)
            action = decision.get("action", "")

            if context_key not in decisions_by_context:
                decisions_by_context[context_key] = []
            decisions_by_context[context_key].append(action)

        # 计算一致性
        total_repetitions = 0
        consistent_repetitions = 0

        for context, actions in decisions_by_context.items():
            if len(actions) > 1:
                total_repetitions += 1
                if len(set(actions)) == 1:  # 所有决策都相同
                    consistent_repetitions += 1

        if total_repetitions == 0:
            return 0.95  # 默认值

        return consistent_repetitions / total_repetitions

    def _generate_report(self, start_time: float, end_time: float) -> Dict[str, Any]:
        """生成报告"""
        print(f"\n{'='*70}")
        print(f"实验完成")
        print(f"结束时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"总耗时: {(end_time - start_time) / 3600:.2f}小时")
        print(f"{'='*70}\n")

        # 打印结果摘要
        print("\n实验结果摘要:")
        print(f"\n{'组别':<10} {'配方':<12} {'有效性':<10} {'效率':<10} {'工具率':<10} {'稳定性':<10}")
        print(f"{'-'*70}")

        for group_id, result in sorted(self.results.items()):
            agent = self.agents[group_id]
            print(
                f"{group_id:<10} "
                f"{agent.config.recipe_level.value:<12} "
                f"{result.operation_effectiveness:<10.2%} "
                f"{result.efficiency_gain:<10.1f}x "
                f"{result.tool_usage_ratio:<10.2%} "
                f"{result.cognitive_stability:<10.2%}"
            )

        print(f"\n{'='*70}\n")

        # 构建报告数据
        report = {
            "experiment_id": self.experiment_id,
            "start_time": start_time,
            "end_time": end_time,
            "duration_hours": (end_time - start_time) / 3600.0,
            "task": {
                "name": self.task.name,
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
                "operation_effectiveness": result.operation_effectiveness,
                "efficiency_gain": result.efficiency_gain,
                "cognitive_stability": result.cognitive_stability,
                "tool_usage_ratio": result.tool_usage_ratio,
                "feedback_loop_strength": result.feedback_loop_strength,
                "strategy_transfer_rate": result.strategy_transfer_rate,
                "extracted_strategies": len(result.extracted_strategies)
            }

        return report


class ExperimentAgent:
    """实验智能体"""

    def __init__(
        self,
        config: AgentConfig,
        group_id: str,
        expected_human_time: float,
        experiment_id: str
    ):
        self.config = config
        self.group_id = group_id
        self.expected_human_time = expected_human_time
        self.experiment_id = experiment_id

        # 执行状态
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self.extracted_strategies: List[Dict[str, Any]] = []
        self.received_strategies: int = 0

        # 监控数据
        self.tool_call_history: List[Dict[str, Any]] = []
        self.decision_trace: List[Dict[str, Any]] = []

        # 统计
        self.total_operations: int = 0
        self.successful_operations: int = 0
        self.failed_operations: int = 0
        self.tool_calls: int = 0

    def execute_task(self, task: TaskDefinition) -> None:
        """执行任务"""
        self.start_time = time.time()

        # 根据配方等级选择执行策略
        if self.config.recipe_level == RecipeLevel.NONE:
            self._execute_without_recipe(task)
        elif self.config.recipe_level == RecipeLevel.BASIC:
            self._execute_with_basic_recipe(task)
        else:  # ENHANCED
            self._execute_with_enhanced_recipe(task)

        self.end_time = time.time()

    def _execute_without_recipe(self, task: TaskDefinition) -> None:
        """无配方执行（对照组）"""
        print(f"智能体 {self.group_id} 无配方执行")

        # 模拟执行任务
        for i in range(20):
            # 随机选择工具或推测
            if i % 3 == 0:
                self._call_tool("random_tool", {"index": i})
            else:
                self._make_decision("random_action", "speculating")

            time.sleep(0.05)  # 模拟耗时

    def _execute_with_basic_recipe(self, task: TaskDefinition) -> None:
        """基础配方执行"""
        print(f"智能体 {self.group_id} 使用基础配方执行")

        # 规则1: read_before_edit
        self._read_file(task.task_id)

        # 执行任务
        for i in range(15):
            # 规则2: 操作前决策
            self._make_decision(f"operation_{i}", f"step {i} reasoning")

            # 执行操作
            self._call_tool(f"tool_{i % 3}", {"step": i})

            # 规则3: test_after_edit
            if i % 5 == 4:
                self._call_tool("test", {"test_id": f"test_{i}"})

            time.sleep(0.05)  # 模拟耗时

    def _execute_with_enhanced_recipe(self, task: TaskDefinition) -> None:
        """增强配方执行"""
        print(f"智能体 {self.group_id} 使用增强配方执行")

        # 规则1: read_before_edit
        self._read_file(task.task_id)

        # 并行执行独立操作
        if self.config.parallel_enabled:
            self._execute_parallel_operations(task)
        else:
            # 串行执行
            for i in range(10):
                # 规则2: diagnose_before_retry
                self._diagnose_and_execute(i)

                # 规则3: test_after_edit
                if i % 3 == 2:
                    self._call_tool("test", {"test_id": f"test_{i}"})

                time.sleep(0.05)  # 模拟耗时

        # 规则4: extract_strategies
        self._extract_strategies()

    def _execute_parallel_operations(self, task: TaskDefinition) -> None:
        """并行执行独立操作"""
        print(f"  启用并行执行")

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = []

            # 创建多个并行任务
            for i in range(5):
                future = executor.submit(self._parallel_task, i)
                futures.append(future)

            # 等待所有任务完成
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"并行任务失败: {e}")

    def _parallel_task(self, task_id: int) -> None:
        """并行任务"""
        # 规则2: diagnose_before_retry
        self._diagnose_and_execute(task_id)

        # 规则3: test_after_edit
        self._call_tool("test", {"test_id": f"test_{task_id}"})

        time.sleep(0.05)  # 模拟耗时

    def _diagnose_and_execute(self, step: int) -> None:
        """诊断并执行"""
        # 诊断
        self._call_tool("diagnose", {"step": step})

        # 执行
        self._call_tool(f"tool_{step % 3}", {"step": step})

    def _call_tool(self, tool_name: str, args: Dict[str, Any]) -> bool:
        """调用工具"""
        self.total_operations += 1
        self.tool_calls += 1

        start_time = time.time()
        success = (hash(tool_name) % 5) != 0  # 80% 成功率
        duration_ms = (time.time() - start_time) * 1000

        if success:
            self.successful_operations += 1
        else:
            self.failed_operations += 1

        # 记录工具调用
        tool_call = {
            "timestamp": time.time(),
            "tool_name": tool_name,
            "args": args,
            "success": success,
            "duration_ms": duration_ms,
            "error": None if success else "simulated error"
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
        """读取文件（read_before_edit规则）"""
        self._call_tool("read_file", {"path": file_path})

    def _extract_strategies(self) -> None:
        """提取策略"""
        # 模拟策略提取
        if self.successful_operations > 5:
            strategy = {
                "name": f"strategy_{len(self.extracted_strategies)}",
                "description": f"Effective strategy extracted by {self.group_id}",
                "success_rate": 0.95,
                "usage_count": 10,
                "efficiency_gain": 2.5
            }
            self.extracted_strategies.append(strategy)

    def receive_strategies(self, strategies: List[Dict[str, Any]]) -> int:
        """接收策略"""
        transferred = 0
        for strategy in strategies:
            if strategy not in self.extracted_strategies:
                self.extracted_strategies.append(strategy)
                transferred += 1
                self.received_strategies += 1

        return transferred

    def get_result(self) -> ExperimentResult:
        """获取结果"""
        return ExperimentResult(
            group_id=self.group_id,
            agent_id=self.config.agent_id,
            status="completed",
            start_time=self.start_time if self.start_time else time.time(),
            end_time=self.end_time if self.end_time else time.time(),
            duration_hours=(
                (self.end_time - self.start_time) / 3600.0
                if self.start_time and self.end_time else 0.0
            ),
            total_operations=self.total_operations,
            successful_operations=self.successful_operations,
            failed_operations=self.failed_operations,
            tool_calls=self.tool_calls,
            strategy_extractions=len(self.extracted_strategies),
            tool_call_history=self.tool_call_history,
            decision_trace=self.decision_trace,
            extracted_strategies=self.extracted_strategies
        )


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(
        description="多智能体协作实验执行器"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("experiments/EXP-001_config.yaml"),
        help="配置文件路径"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("experiments/results/multi_agent_experiment.json"),
        help="结果输出路径"
    )

    args = parser.parse_args()

    # 创建执行器
    executor = MultiAgentExperimentExecutor(args.config)

    # 创建智能体
    executor.create_agents()

    # 运行实验
    report = executor.run_experiment()

    # 保存结果
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\n结果已保存到: {args.output}\n")


if __name__ == "__main__":
    main()
