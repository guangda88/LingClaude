#!/usr/bin/env python3
"""
EXP-4 执行脚本

运行多 AI 协作进化实验（EXP-4）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from exp4_executor import EXP4Executor, EXP4ExperimentResult

# ==================== 主函数 ====================

def main():
    parser = argparse.ArgumentParser(description="运行 EXP-4 多 AI 协作进化实验")
    parser.add_argument("--config", type=str, default="EXP-004_config.yaml",
                        help="实验配置文件路径")
    parser.add_argument("--output", type=str, default="results/EXP-004.json",
                        help="输出结果文件路径")
    parser.add_argument("--runs", type=int, default=3,
                        help="每个实验组的运行次数")
    parser.add_argument("--parallel", action="store_true",
                        help="并行运行实验组")
    parser.add_argument("--groups", type=str, nargs="+",
                        help="指定运行的实验组（默认运行所有组）")
    parser.add_argument("--resume", type=str,
                        help="从指定文件恢复实验结果")

    args = parser.parse_args()

    print("\n" + "="*70)
    print("EXP-4: 多 AI 协作进化实验")
    print("="*70)
    print(f"配置文件: {args.config}")
    print(f"输出文件: {args.output}")
    print(f"运行次数: {args.runs}")
    print(f"并行运行: {args.parallel}")
    print("="*70 + "\n")

    # 创建执行器
    executor = EXP4Executor(args.config)

    # 加载配置
    config = executor.config
    groups_config = config.get("groups", {})

    # 确定要运行的组
    if args.groups:
        groups_to_run = args.groups
    else:
        groups_to_run = list(groups_config.keys())

    print(f"准备运行 {len(groups_to_run)} 个实验组，每组 {args.runs} 次运行\n")

    # 恢复或初始化结果
    if args.resume:
        print(f"从 {args.resume} 恢复实验结果...")
        with open(args.resume, 'r') as f:
            all_results = json.load(f)
        completed_runs = set()
        for group_id, group_results in all_results.items():
            for result in group_results:
                completed_runs.add(f"{group_id}_{result['run_id']}")
        print(f"已恢复 {len(completed_runs)} 次运行\n")
    else:
        all_results: Dict[str, List[Dict[str, Any]]] = {}
        completed_runs = set()

    # 运行实验
    start_time = time.time()

    for group_id in groups_to_run:
        group_config = groups_config.get(group_id)
        if not group_config:
            print(f"警告: 组别 {group_id} 不存在，跳过")
            continue

        if group_id not in all_results:
            all_results[group_id] = []

        for run_id in range(1, args.runs + 1):
            run_key = f"{group_id}_{run_id}"

            if run_key in completed_runs:
                print(f"跳过 {group_id} Run {run_id} (已完成)")
                continue

            print(f"\n开始 {group_id} Run {run_id}...")

            try:
                # 运行实验
                result = executor.run_group(group_id, group_config, run_id)

                # 保存结果
                all_results[group_id].append({
                    "group_id": result.group_id,
                    "run_id": result.run_id,
                    "collaboration_mode": result.collaboration_mode,
                    "communication_type": result.communication_type,
                    "agent_specialization": result.agent_specialization,
                    "strategy_sharing_mechanism": result.strategy_sharing_mechanism,

                    # 核心指标
                    "duration_hours": result.duration_hours,
                    "total_operations": result.total_operations,
                    "successful_operations": result.successful_operations,
                    "operation_effectiveness": result.operation_effectiveness,
                    "efficiency_gain": result.efficiency_gain,
                    "cognitive_stability": result.cognitive_stability,
                    "tool_usage_ratio": result.tool_usage_ratio,
                    "feedback_loop_strength": result.feedback_loop_strength,

                    # 协作特定指标
                    "evolution_speed_multiplier": result.evolution_speed_multiplier,
                    "unique_strategy_count": result.unique_strategy_count,
                    "strategy_sharing_rate": result.strategy_sharing_rate,
                    "final_efficiency_multiplier": result.final_efficiency_multiplier,
                    "agent_coordination_overhead": result.agent_coordination_overhead,
                    "conflict_resolution_rate": result.conflict_resolution_rate,
                    "knowledge_coverage": result.knowledge_coverage,
                    "strategy_diversity": result.strategy_diversity,
                    "emergence_rate": result.emergence_rate,
                    "load_balance_index": result.load_balance_index,

                    # 统计数据
                    "total_communications": result.total_communications,
                    "total_strategies_discovered": result.total_strategies_discovered,
                    "total_strategies_shared": result.total_strategies_shared,
                    "total_conflicts": result.total_conflicts,
                    "resolved_conflicts": result.resolved_conflicts,

                    # 原始数据
                    "decision_traces": result.decision_traces,
                    "communication_events": result.communication_events,
                    "conflict_events": result.conflict_events
                })

                print(f"{group_id} Run {run_id} 完成\n")

            except Exception as e:
                print(f"错误: {group_id} Run {run_id} 失败: {e}")
                import traceback
                traceback.print_exc()

    # 生成分析
    analysis = generate_analysis(all_results, config)

    # 保存结果
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    final_results = {
        "experiment_id": "EXP-004",
        "experiment_name": "多 AI 协作进化实验",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config": {
            "config_file": args.config,
            "runs_per_group": args.runs,
            "groups_executed": groups_to_run
        },
        "results": all_results,
        "analysis": analysis
    }

    with open(output_path, 'w') as f:
        json.dump(final_results, f, indent=2, default=str)

    # 打印摘要
    print_summary(analysis, time.time() - start_time)

    print(f"\n结果已保存到: {output_path}")
    print("实验完成！\n")


def generate_analysis(results: Dict[str, List[Dict[str, Any]]],
                   config: Dict[str, Any]) -> Dict[str, Any]:
    """生成分析结果"""
    analysis = {
        "groups_executed": len(results),
        "total_runs": sum(len(group_results) for group_results in results.values()),
        "summary_by_group": {},
        "hypothesis_validation": {},
        "key_findings": []
    }

    # 按组分析
    for group_id, group_results in results.items():
        if not group_results:
            continue

        avg_effectiveness = sum(r["operation_effectiveness"] for r in group_results) / len(group_results)
        avg_efficiency = sum(r["efficiency_gain"] for r in group_results) / len(group_results)
        avg_speed_multiplier = sum(r["evolution_speed_multiplier"] for r in group_results) / len(group_results)
        avg_sharing_rate = sum(r["strategy_sharing_rate"] for r in group_results) / len(group_results)
        avg_knowledge_coverage = sum(r["knowledge_coverage"] for r in group_results) / len(group_results)

        analysis["summary_by_group"][group_id] = {
            "avg_operation_effectiveness": avg_effectiveness,
            "avg_efficiency_gain": avg_efficiency,
            "avg_evolution_speed_multiplier": avg_speed_multiplier,
            "avg_strategy_sharing_rate": avg_sharing_rate,
            "avg_knowledge_coverage": avg_knowledge_coverage,
            "num_runs": len(group_results)
        }

    # 验证假设 H4a: 策略传递能提升进化速度2-3倍
    sa_results = results.get("SA", [])
    prs_results = results.get("P-RS", [])

    if sa_results and prs_results:
        sa_avg_speed = sum(r["evolution_speed_multiplier"] for r in sa_results) / len(sa_results)
        prs_avg_speed = sum(r["evolution_speed_multiplier"] for r in prs_results) / len(prs_results)
        speed_improvement = prs_avg_speed / sa_avg_speed if sa_avg_speed > 0 else 0

        analysis["hypothesis_validation"]["H4a"] = {
            "hypothesis": "策略传递能提升进化速度2-3倍",
            "expected_threshold": 2.5,
            "observed_improvement": speed_improvement,
            "passed": speed_improvement >= 2.5
        }

    # 验证假设 H4b: 群体进化能发现个体无法发现的策略
    if prs_results:
        avg_unique_strategies = sum(r["unique_strategy_count"] for r in prs_results) / len(prs_results)
        avg_emergence_rate = sum(r["emergence_rate"] for r in prs_results) / len(prs_results)

        analysis["hypothesis_validation"]["H4b"] = {
            "hypothesis": "群体进化能发现个体无法发现的策略",
            "expected_threshold": 10,
            "observed_unique_strategies": avg_unique_strategies,
            "observed_emergence_rate": avg_emergence_rate,
            "passed": avg_unique_strategies >= 10
        }

    # 验证假设 H4c: 协同进化能提升最终效率1.5-2倍
    if sa_results and prs_results:
        sa_avg_efficiency = sum(r["efficiency_gain"] for r in sa_results) / len(sa_results)
        prs_avg_efficiency = sum(r["efficiency_gain"] for r in prs_results) / len(prs_results)
        efficiency_multiplier = prs_avg_efficiency / sa_avg_efficiency if sa_avg_efficiency > 0 else 0

        analysis["hypothesis_validation"]["H4c"] = {
            "hypothesis": "协同进化能提升最终效率1.5-2倍",
            "expected_threshold": 1.8,
            "observed_multiplier": efficiency_multiplier,
            "passed": efficiency_multiplier >= 1.8
        }

    # 关键发现
    if analysis["hypothesis_validation"]:
        for hypothesis_id, validation in analysis["hypothesis_validation"].items():
            if validation["passed"]:
                analysis["key_findings"].append(
                    f"{hypothesis_id}: ✅ 通过 - {validation['hypothesis']}"
                )
            else:
                analysis["key_findings"].append(
                    f"{hypothesis_id}: ❌ 未通过 - {validation['hypothesis']}"
                )

    return analysis


def print_summary(analysis: Dict[str, Any], duration: float) -> None:
    """打印摘要"""
    print("\n" + "="*70)
    print("实验摘要")
    print("="*70)

    print(f"\n执行的组数: {analysis['groups_executed']}")
    print(f"总运行次数: {analysis['total_runs']}")
    print(f"总耗时: {duration:.2f} 秒 ({duration/60:.2f} 分钟)")

    if analysis["summary_by_group"]:
        print("\n各组平均指标:")
        print(f"{'组别':<10} {'有效性':<10} {'效率':<10} {'速度倍数':<12} {'共享率':<10} {'知识覆盖率':<12}")
        print("-" * 70)
        for group_id, summary in analysis["summary_by_group"].items():
            print(f"{group_id:<10} "
                  f"{summary['avg_operation_effectiveness']:.2%}  "
                  f"{summary['avg_efficiency_gain']:.2f}x     "
                  f"{summary['avg_evolution_speed_multiplier']:.2f}x       "
                  f"{summary['avg_strategy_sharing_rate']:.2%}  "
                  f"{summary['avg_knowledge_coverage']:.2%}    ")

    if analysis["hypothesis_validation"]:
        print("\n假设验证:")
        for hypothesis_id, validation in analysis["hypothesis_validation"].items():
            status = "✅ 通过" if validation["passed"] else "❌ 未通过"
            print(f"{status} - {validation['hypothesis']}")

    if analysis["key_findings"]:
        print("\n关键发现:")
        for finding in analysis["key_findings"]:
            print(f"  {finding}")

    print("="*70 + "\n")


if __name__ == "__main__":
    main()
