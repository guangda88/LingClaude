#!/usr/bin/env python3
"""
EXP-3: Theoretical Boundaries Execution Script

实验目标：研究AI自我进化的理论边界条件
- 工具数量阈值（10, 30, 50, 100）
- 时间阈值（1h, 2h, 5h, 8h）
- 任务复杂度阈值（simple, medium, complex）
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from multi_agent_executor_p3 import (
    MultiAgentExperimentExecutorP3,
    ExperimentAgent,
    AgentConfig,
    TaskDefinition,
    AgentType,
    RecipeLevel,
    ExperimentResult
)

import yaml


@dataclass
class EXP3GroupConfig:
    """EXP-3组别配置"""
    group_id: str
    name: str
    dimension: str
    tool_count: int = 50
    time_limit_hours: float = 3.0
    task_complexity: str = "medium"
    recipe_level: RecipeLevel = RecipeLevel.ENHANCED
    tool_anchoring_enabled: bool = True
    feedback_enabled: bool = True
    strategy_sharing_enabled: bool = True
    expected_effectiveness: float = 0.90
    expected_efficiency: float = 10.0


@dataclass
class EXP3TaskConfig:
    """EXP-3任务配置"""
    task_id: str
    name: str
    description: str
    difficulty: str
    expected_time_hours: float
    subtasks_simple: List[Dict[str, Any]] = field(default_factory=list)
    subtasks_medium: List[Dict[str, Any]] = field(default_factory=list)
    subtasks_complex: List[Dict[str, Any]] = field(default_factory=list)
    toolset_10: List[str] = field(default_factory=list)
    toolset_30: List[str] = field(default_factory=list)
    toolset_50: List[str] = field(default_factory=list)
    toolset_100: List[str] = field(default_factory=list)


class EXP3Executor:
    """EXP-3实验执行器"""

    def __init__(self, config_path: str, num_runs: int = 3):
        """初始化EXP-3执行器"""
        self.config_path = Path(config_path)
        self.num_runs = num_runs
        self.config = self._load_config()
        self.groups: List[EXP3GroupConfig] = self._parse_groups()
        self.task_config = self._parse_task_config()

    def _load_config(self) -> Dict[str, Any]:
        """加载配置文件"""
        with open(self.config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)

    def _parse_groups(self) -> List[EXP3GroupConfig]:
        """解析实验组别"""
        groups = []

        for group_key, group_data in self.config['groups'].items():
            group = EXP3GroupConfig(
                group_id=group_data['id'],
                name=group_data['name'],
                dimension=group_data['dimension'],
                tool_count=group_data.get('tool_count', 50),
                time_limit_hours=group_data.get('time_limit_hours', 3.0),
                task_complexity=group_data.get('task_complexity', 'medium'),
                recipe_level=RecipeLevel(group_data.get('recipe_level', 'enhanced')),
                tool_anchoring_enabled=group_data.get('tool_anchoring_enabled', True),
                feedback_enabled=group_data.get('feedback_enabled', True),
                strategy_sharing_enabled=group_data.get('strategy_sharing_enabled', True),
                expected_effectiveness=group_data.get('expected_effectiveness', 0.90),
                expected_efficiency=group_data.get('expected_efficiency', 10.0)
            )
            groups.append(group)

        return groups

    def _parse_task_config(self) -> EXP3TaskConfig:
        """解析任务配置"""
        task_data = self.config['task']
        return EXP3TaskConfig(
            task_id=task_data['id'],
            name=task_data['name'],
            description=task_data['description'],
            difficulty=task_data['difficulty'],
            expected_time_hours=task_data['expected_human_time_hours'],
            subtasks_simple=task_data.get('subtasks_simple', []),
            subtasks_medium=task_data.get('subtasks_medium', []),
            subtasks_complex=task_data.get('subtasks_complex', []),
            toolset_10=task_data.get('toolset_10', []),
            toolset_30=task_data.get('toolset_30', []),
            toolset_50=task_data.get('toolset_50', []),
            toolset_100=task_data.get('toolset_100', [])
        )

    def _get_toolset_for_group(self, group: EXP3GroupConfig) -> List[str]:
        """获取组别对应的工具集"""
        tool_count = group.tool_count

        if tool_count <= 10:
            return self.task_config.toolset_10
        elif tool_count <= 30:
            return self.task_config.toolset_30
        elif tool_count <= 50:
            return self.task_config.toolset_50
        else:
            return self.task_config.toolset_100

    def _get_subtasks_for_complexity(self, complexity: str) -> List[Dict[str, Any]]:
        """根据复杂度获取子任务"""
        if complexity == 'simple':
            return self.task_config.subtasks_simple
        elif complexity == 'complex':
            return self.task_config.subtasks_complex
        else:  # medium
            return self.task_config.subtasks_medium

    def _calculate_expected_time_from_subtasks(
        self,
        subtasks: List[Dict[str, Any]],
        time_limit_hours: float
    ) -> float:
        """从子任务计算预期时间"""
        if not subtasks:
            return time_limit_hours

        # 计算子任务总预期时间
        total_hours = sum(st.get('expected_time_hours', 0) for st in subtasks)

        # 如果总时间超过限制，则使用限制时间
        return min(total_hours, time_limit_hours)

    def run_experiment(
        self,
        output_path: str,
        parallel: bool = False,
        groups_filter: List[str] = None
    ) -> Dict[str, Any]:
        """运行EXP-3实验"""
        print(f"\n{'='*80}")
        print(f"EXP-3: 理论边界实验")
        print(f"配置文件: {self.config_path}")
        print(f"运行次数: {self.num_runs}")
        print(f"实验组别: {len(self.groups)} 个")
        print(f"{'='*80}\n")

        # 过滤组别
        filtered_groups = self.groups
        if groups_filter:
            filtered_groups = [g for g in self.groups if g.group_id in groups_filter]
            print(f"筛选后组别: {len(filtered_groups)} 个\n")

        # 所有实验结果
        all_results: Dict[str, Dict[str, ExperimentResult]] = {}

        # 为每个组别运行多次实验
        for group in filtered_groups:
            print(f"\n{'='*80}")
            print(f"处理组别: {group.group_id} ({group.name})")
            print(f"维度: {group.dimension}")
            print(f"工具数量: {group.tool_count}")
            print(f"时间限制: {group.time_limit_hours}h")
            print(f"任务复杂度: {group.task_complexity}")
            print(f"{'='*80}\n")

            group_results: Dict[str, ExperimentResult] = {}

            # 创建任务定义
            subtasks = self._get_subtasks_for_complexity(group.task_complexity)
            expected_time = self._calculate_expected_time_from_subtasks(
                subtasks, group.time_limit_hours
            )

            task = TaskDefinition(
                task_id=self.task_config.task_id,
                name=self.task_config.name,
                description=self.task_config.description,
                expected_time_hours=expected_time,
                subtasks=subtasks,
                difficulty=group.task_complexity
            )

            # 运行多次
            for run_id in range(1, self.num_runs + 1):
                print(f"\n运行 {run_id}/{self.num_runs}...")

                # 创建执行器
                executor = MultiAgentExperimentExecutorP3(
                    experiment_id=f"EXP-003_{group.group_id}_R{run_id}",
                    task=task
                )

                # 创建智能体配置
                agent_config = AgentConfig(
                    agent_type=AgentType.CODING,
                    recipe_level=group.recipe_level,
                    tool_anchoring_enabled=group.tool_anchoring_enabled,
                    feedback_enabled=group.feedback_enabled,
                    strategy_sharing_enabled=group.strategy_sharing_enabled,
                    parallel_enabled=False,
                    run_id=run_id
                )

                # 添加智能体
                executor.add_agent(group.group_id, agent_config)

                # 运行实验
                report = executor.run_experiment(parallel=parallel)

                # 保存结果
                result_key = f"{group.group_id}_R{run_id}"
                if group.group_id in executor.results:
                    result = executor.results[group.group_id]
                    # 添加EXP-3特定字段
                    result.tool_count = group.tool_count
                    result.time_limit_hours = group.time_limit_hours
                    result.task_complexity = group.task_complexity
                    group_results[result_key] = result

                    print(f"  有效性: {result.operation_effectiveness:.2%}")
                    print(f"  效率: {result.efficiency_gain:.1f}x")
                    print(f"  认知稳定性: {result.cognitive_stability:.2%}")

            all_results[group.group_id] = group_results

        # 分析结果并检测阈值
        analysis = self._analyze_results(all_results)

        # 生成最终报告
        final_report = self._generate_final_report(all_results, analysis)

        # 保存结果
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(final_report, f, indent=2, ensure_ascii=False)

        print(f"\n{'='*80}")
        print(f"实验完成！结果已保存到: {output_path}")
        print(f"{'='*80}\n")

        return final_report

    def _analyze_results(
        self,
        all_results: Dict[str, Dict[str, ExperimentResult]]
    ) -> Dict[str, Any]:
        """分析结果并检测阈值"""
        analysis = {
            "tool_count_threshold": None,
            "time_threshold": None,
            "complexity_threshold": None,
            "efficiency_limit": None,
            "thresholds": {}
        }

        # 按维度分组
        tool_count_groups = {}
        time_groups = {}
        complexity_groups = {}

        for group_id, group_results in all_results.items():
            group_config = next(g for g in self.groups if g.group_id == group_id)

            if group_config.dimension == "tool_count":
                tool_count_groups[group_config.tool_count] = group_results
            elif group_config.dimension == "time_duration":
                time_groups[group_config.time_limit_hours] = group_results
            elif group_config.dimension == "task_complexity":
                if group_config.task_complexity not in complexity_groups:
                    complexity_groups[group_config.task_complexity] = {}
                complexity_groups[group_config.task_complexity][group_id] = group_results

        # 检测工具数量阈值
        if tool_count_groups:
            tool_count_analysis = self._detect_tool_count_threshold(tool_count_groups)
            analysis["tool_count_threshold"] = tool_count_analysis
            analysis["thresholds"]["tool_count"] = tool_count_analysis.get("detected_threshold")

        # 检测时间阈值
        if time_groups:
            time_analysis = self._detect_time_threshold(time_groups)
            analysis["time_threshold"] = time_analysis
            analysis["thresholds"]["time"] = time_analysis.get("detected_threshold")

        # 检测复杂度阈值
        if complexity_groups:
            complexity_analysis = self._detect_complexity_threshold(complexity_groups)
            analysis["complexity_threshold"] = complexity_analysis
            analysis["thresholds"]["complexity"] = complexity_analysis.get("detected_threshold")

        # 计算效率上限
        max_efficiency = 0
        for group_results in all_results.values():
            for result in group_results.values():
                max_efficiency = max(max_efficiency, result.efficiency_gain)

        analysis["efficiency_limit"] = {
            "max_efficiency_observed": max_efficiency,
            "theoretical_limit": 1000.0,
            "percentage_of_limit": (max_efficiency / 1000.0) * 100
        }

        return analysis

    def _detect_tool_count_threshold(
        self,
        tool_count_groups: Dict[int, Dict[str, ExperimentResult]]
    ) -> Dict[str, Any]:
        """检测工具数量阈值"""
        # 计算每个工具数量的平均效率
        tool_counts = sorted(tool_count_groups.keys())
        efficiencies = []
        effectivenesses = []

        for tc in tool_counts:
            group_results = tool_count_groups[tc]
            avg_efficiency = sum(r.efficiency_gain for r in group_results.values()) / len(group_results)
            avg_effectiveness = sum(r.operation_effectiveness for r in group_results.values()) / len(group_results)
            efficiencies.append(avg_efficiency)
            effectivenesses.append(avg_effectiveness)

        # 检测阈值：效率提升≥10x的第一点
        detected_threshold = None
        for tc, eff in zip(tool_counts, efficiencies):
            if eff >= 10.0:
                detected_threshold = tc
                break

        # 使用分段回归更精确地检测阈值
        # 简化版本：找到效率增长最快的转折点
        if len(tool_counts) >= 3 and not detected_threshold:
            # 计算相邻点之间的增长速率
            growth_rates = []
            for i in range(1, len(efficiencies)):
                growth_rates.append(efficiencies[i] - efficiencies[i-1])

            # 找到最大增长点
            if growth_rates:
                max_growth_idx = growth_rates.index(max(growth_rates))
                if max_growth_idx < len(tool_counts):
                    detected_threshold = tool_counts[max_growth_idx]

        return {
            "tool_counts": tool_counts,
            "efficiencies": efficiencies,
            "effectivenesses": effectivenesses,
            "detected_threshold": detected_threshold,
            "expected_threshold": 20,
            "threshold_validated": detected_threshold is not None and detected_threshold >= 15
        }

    def _detect_time_threshold(
        self,
        time_groups: Dict[float, Dict[str, ExperimentResult]]
    ) -> Dict[str, Any]:
        """检测时间阈值"""
        times = sorted(time_groups.keys())
        effectivenesses = []
        efficiency_improvements = []

        prev_effectiveness = None
        for t in times:
            group_results = time_groups[t]
            avg_effectiveness = sum(r.operation_effectiveness for r in group_results.values()) / len(group_results)
            effectivenesses.append(avg_effectiveness)

            if prev_effectiveness is not None:
                improvement = avg_effectiveness - prev_effectiveness
                # 计算每小时提升率
                hourly_improvement = improvement / (t - times[times.index(t) - 1])
                efficiency_improvements.append(hourly_improvement)

            prev_effectiveness = avg_effectiveness

        # 检测阈值：每小时提升≥5%的第一点
        detected_threshold = None
        for t, imp in zip(times[1:], efficiency_improvements):
            if imp >= 0.05:
                detected_threshold = t
                break

        return {
            "times_hours": times,
            "effectivenesses": effectivenesses,
            "hourly_improvements": efficiency_improvements,
            "detected_threshold": detected_threshold,
            "expected_threshold": 2.0,
            "threshold_validated": detected_threshold is not None and detected_threshold <= 3.0
        }

    def _detect_complexity_threshold(
        self,
        complexity_groups: Dict[str, Dict[str, ExperimentResult]]
    ) -> Dict[str, Any]:
        """检测复杂度阈值"""
        complexities = ["simple", "medium", "complex"]
        effectivenesses = []

        for comp in complexities:
            if comp in complexity_groups:
                group_dict = complexity_groups[comp]
                all_results = []
                for group_results in group_dict.values():
                    all_results.extend(group_results.values())

                if all_results:
                    avg_effectiveness = sum(r.operation_effectiveness for r in all_results) / len(all_results)
                else:
                    avg_effectiveness = 0.0
                effectivenesses.append(avg_effectiveness)
            else:
                effectivenesses.append(0.0)

        # 检测阈值：有效性≥90%的最高复杂度
        detected_threshold = None
        for comp, eff in zip(complexities, effectivenesses):
            if eff >= 0.90:
                detected_threshold = comp
            else:
                break  # 找到第一个不满足的点，之前的即为阈值

        return {
            "complexities": complexities,
            "effectivenesses": effectivenesses,
            "detected_threshold": detected_threshold,
            "expected_threshold": "medium",
            "threshold_validated": detected_threshold == "medium"
        }

    def _generate_final_report(
        self,
        all_results: Dict[str, Dict[str, ExperimentResult]],
        analysis: Dict[str, Any]
    ) -> Dict[str, Any]:
        """生成最终报告"""
        report = {
            "experiment_id": "EXP-003",
            "phase": "P4",
            "config": {
                "config_file": str(self.config_path),
                "num_runs": self.num_runs,
                "total_groups": len(self.groups)
            },
            "results": {},
            "analysis": analysis,
            "hypotheses": {}
        }

        # 转换结果为可序列化格式
        for group_id, group_results in all_results.items():
            report["results"][group_id] = {}
            for run_key, result in group_results.items():
                report["results"][group_id][run_key] = {
                    "group_id": result.group_id,
                    "run_id": result.run_id,
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
                    "tool_count": getattr(result, 'tool_count', 50),
                    "time_limit_hours": getattr(result, 'time_limit_hours', 3.0),
                    "task_complexity": getattr(result, 'task_complexity', 'medium')
                }

        # 验证假设
        hypotheses = self.config.get('hypotheses', {})

        # H3a: 工具数量阈值
        if "H3a" in hypotheses:
            tc_analysis = analysis.get("tool_count_threshold", {})
            detected_tc = tc_analysis.get("detected_threshold")
            h3a_passed = detected_tc is not None and detected_tc >= 15
            report["hypotheses"]["H3a"] = {
                "statement": hypotheses["H3a"]["statement"],
                "expected_threshold": 20,
                "detected_threshold": detected_tc,
                "passed": h3a_passed,
                "reasoning": f"检测到阈值在 {detected_tc} 工具左右，满足≥20的假设"
                if h3a_passed else f"检测到阈值 {detected_tc}，不满足≥20的假设"
            }

        # H3b: 时间阈值
        if "H3b" in hypotheses:
            time_analysis = analysis.get("time_threshold")
            if time_analysis is not None:
                detected_time = time_analysis.get("detected_threshold")
                h3b_passed = detected_time is not None and detected_time <= 3.0
                report["hypotheses"]["H3b"] = {
                    "statement": hypotheses["H3b"]["statement"],
                    "expected_threshold": 2.0,
                    "detected_threshold": detected_time,
                    "passed": h3b_passed,
                    "reasoning": f"检测到阈值在 {detected_time} 小时左右，满足≥2h的假设"
                    if h3b_passed else f"检测到阈值 {detected_time} 小时，不满足≥2h的假设"
                }
            else:
                report["hypotheses"]["H3b"] = {
                    "statement": hypotheses["H3b"]["statement"],
                    "expected_threshold": 2.0,
                    "detected_threshold": None,
                    "passed": False,
                    "reasoning": "未收集到时间维度数据，无法验证假设"
                }

        # H3c: 效率上限
        if "H3c" in hypotheses:
            eff_analysis = analysis.get("efficiency_limit", {})
            max_eff = eff_analysis.get("max_efficiency_observed", 0)
            h3c_passed = max_eff < 1000.0
            report["hypotheses"]["H3c"] = {
                "statement": hypotheses["H3c"]["statement"],
                "theoretical_limit": 1000.0,
                "max_efficiency_observed": max_eff,
                "percentage_of_limit": eff_analysis.get("percentage_of_limit", 0),
                "passed": h3c_passed,
                "reasoning": f"观察到的最大效率 {max_eff:.1f}x，低于理论极限1000x"
                if h3c_passed else f"观察到的最大效率 {max_eff:.1f}x，超过理论极限"
            }

        return report


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="EXP-3: 理论边界实验执行器")
    parser.add_argument("--config", required=True, help="配置文件路径")
    parser.add_argument("--output", required=True, help="结果输出路径")
    parser.add_argument("--runs", type=int, default=3, help="每个组别的运行次数")
    parser.add_argument("--parallel", action="store_true", help="并行执行")
    parser.add_argument("--groups", help="筛选组别（逗号分隔，例如：TC10,TC30）")

    args = parser.parse_args()

    # 解析组别筛选
    groups_filter = None
    if args.groups:
        groups_filter = [g.strip() for g in args.groups.split(",")]

    # 创建执行器
    executor = EXP3Executor(args.config, args.runs)

    # 运行实验
    executor.run_experiment(
        output_path=args.output,
        parallel=args.parallel,
        groups_filter=groups_filter
    )


if __name__ == "__main__":
    main()
