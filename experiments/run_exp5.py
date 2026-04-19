#!/usr/bin/env python3
"""
EXP-5 执行脚本

运行跨任务泛化实验（EXP-5）
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

from exp5_executor import EXP5Executor, EXP5ExperimentResult

# ==================== 主函数 ====================

def main():
    parser = argparse.ArgumentParser(description="运行 EXP-5 跨任务泛化实验")
    parser.add_argument("--config", type=str, default="EXP-005_config.yaml",
                        help="实验配置文件路径")
    parser.add_argument("--output", type=str, default="results/EXP-005.json",
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
    print("EXP-5: 跨任务泛化实验")
    print("="*70)
    print(f"配置文件: {args.config}")
    print(f"输出文件: {args.output}")
    print(f"运行次数: {args.runs}")
    print(f"并行运行: {args.parallel}")
    print("="*70 + "\n")

    # 创建执行器
    executor = EXP5Executor(args.config)

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
                    "source_task_id": result.source_task_id,
                    "target_task_id": result.target_task_id,
                    "transfer_strategy": result.transfer_strategy,
                    "recipe_complexity": result.recipe_complexity,
                    "task_similarity": result.task_similarity,

                    # 核心指标
                    "duration_hours": result.duration_hours,
                    "total_operations": result.total_operations,
                    "successful_operations": result.successful_operations,
                    "operation_effectiveness": result.operation_effectiveness,
                    "efficiency_gain": result.efficiency_gain,
                    "cognitive_stability": result.cognitive_stability,
                    "tool_usage_ratio": result.tool_usage_ratio,
                    "feedback_loop_strength": result.feedback_loop_strength,

                    # 泛化特定指标
                    "performance_retention_rate": result.performance_retention_rate,
                    "similarity_correlation": result.similarity_correlation,
                    "adaptive_vs_direct_improvement": result.adaptive_vs_direct_improvement,
                    "transfer_speed": result.transfer_speed,
                    "adaptation_overhead": result.adaptation_overhead,
                    "rule_effectiveness_rate": result.rule_effectiveness_rate,
                    "cross_domain_transfer_success": result.cross_domain_transfer_success,
                    "recipe_stability": result.recipe_stability,
                    "transfer_distance": result.transfer_distance,
                    "generalization_gap": result.generalization_gap,

                    # 统计数据
                    "total_rules": result.total_rules,
                    "effective_rules": result.effective_rules,
                    "adaptations_made": result.adaptations_made,
                    "adaptation_time_hours": result.adaptation_time_hours,

                    # 原始数据
                    "source_performance": result.source_performance,
                    "target_performance": result.target_performance,
                    "adaptation_logs": result.adaptation_logs,
                    "rule_comparison": result.rule_comparison
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
        "experiment_id": "EXP-005",
        "experiment_name": "跨任务泛化实验",
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
        "summary_by_similarity": {},
        "summary_by_strategy": {},
        "hypothesis_validation": {},
        "key_findings": []
    }

    # 按组分析
    for group_id, group_results in results.items():
        if not group_results:
            continue

        avg_effectiveness = sum(r["operation_effectiveness"] for r in group_results) / len(group_results)
        avg_efficiency = sum(r["efficiency_gain"] for r in group_results) / len(group_results)
        avg_retention_rate = sum(r["performance_retention_rate"] for r in group_results) / len(group_results)
        avg_adaptation_overhead = sum(r["adaptation_overhead"] for r in group_results) / len(group_results)
        avg_rule_effectiveness = sum(r["rule_effectiveness_rate"] for r in group_results) / len(group_results)

        analysis["summary_by_group"][group_id] = {
            "avg_operation_effectiveness": avg_effectiveness,
            "avg_efficiency_gain": avg_efficiency,
            "avg_performance_retention_rate": avg_retention_rate,
            "avg_adaptation_overhead": avg_adaptation_overhead,
            "avg_rule_effectiveness_rate": avg_rule_effectiveness,
            "num_runs": len(group_results)
        }

    # 按相似度分析
    high_similarity_groups = ["HD-S", "HA-S", "HA-M", "HA-C"]
    medium_similarity_groups = ["MD-M", "MA-M", "MH-M"]
    low_similarity_groups = ["LD-M", "LA-M"]

    for similarity_type, group_ids in [
        ("high", high_similarity_groups),
        ("medium", medium_similarity_groups),
        ("low", low_similarity_groups)
    ]:
        similarity_results = []
        for group_id in group_ids:
            if group_id in results:
                similarity_results.extend(results[group_id])

        if similarity_results:
            avg_retention = sum(r["performance_retention_rate"] for r in similarity_results) / len(similarity_results)
            analysis["summary_by_similarity"][similarity_type] = {
                "avg_performance_retention_rate": avg_retention,
                "num_results": len(similarity_results)
            }

    # 按迁移策略分析
    direct_groups = ["HD-S", "MD-M", "LD-M"]
    adaptive_groups = ["HA-S", "HA-M", "HA-C", "MA-M", "LA-M"]
    hybrid_groups = ["MH-M"]

    for strategy_type, group_ids in [
        ("direct", direct_groups),
        ("adaptive", adaptive_groups),
        ("hybrid", hybrid_groups)
    ]:
        strategy_results = []
        for group_id in group_ids:
            if group_id in results:
                strategy_results.extend(results[group_id])

        if strategy_results:
            avg_retention = sum(r["performance_retention_rate"] for r in strategy_results) / len(strategy_results)
            avg_improvement = sum(r["adaptive_vs_direct_improvement"] for r in strategy_results) / len(strategy_results)
            analysis["summary_by_strategy"][strategy_type] = {
                "avg_performance_retention_rate": avg_retention,
                "avg_adaptive_improvement": avg_improvement,
                "num_results": len(strategy_results)
            }

    # 验证假设 H5a: 配方可跨任务泛化（≥80%性能保持）
    all_retention_rates = [
        r["performance_retention_rate"]
        for group_results in results.values()
        for r in group_results
    ]

    if all_retention_rates:
        avg_retention_rate = sum(all_retention_rates) / len(all_retention_rates)
        min_retention_rate = min(all_retention_rates)
        groups_above_threshold = sum(1 for r in all_retention_rates if r >= 0.80)

        analysis["hypothesis_validation"]["H5a"] = {
            "hypothesis": "配方可跨任务泛化（≥80%性能保持）",
            "expected_threshold": 0.80,
            "avg_retention_rate": avg_retention_rate,
            "min_retention_rate": min_retention_rate,
            "groups_above_threshold": groups_above_threshold,
            "total_groups": len(all_retention_rates),
            "passed": avg_retention_rate >= 0.80 and min_retention_rate >= 0.70
        }

    # 验证假设 H5b: 任务相似度越高，泛化效果越好
    if all(key in analysis["summary_by_similarity"] for key in ["high", "medium", "low"]):
        high_retention = analysis["summary_by_similarity"]["high"]["avg_performance_retention_rate"]
        medium_retention = analysis["summary_by_similarity"]["medium"]["avg_performance_retention_rate"]
        low_retention = analysis["summary_by_similarity"]["low"]["avg_performance_retention_rate"]

        correlation_higher = high_retention > medium_retention > low_retention

        analysis["hypothesis_validation"]["H5b"] = {
            "hypothesis": "任务相似度越高，泛化效果越好",
            "high_similarity_retention": high_retention,
            "medium_similarity_retention": medium_retention,
            "low_similarity_retention": low_retention,
            "trend_correct": correlation_higher,
            "passed": correlation_higher
        }

    # 验证假设 H5c: 适应迁移比直接转移更有效
    if "direct" in analysis["summary_by_strategy"] and "adaptive" in analysis["summary_by_strategy"]:
        direct_retention = analysis["summary_by_strategy"]["direct"]["avg_performance_retention_rate"]
        adaptive_retention = analysis["summary_by_strategy"]["adaptive"]["avg_performance_retention_rate"]
        adaptive_improvement = analysis["summary_by_strategy"]["adaptive"]["avg_adaptive_improvement"]

        improvement = adaptive_retention - direct_retention

        analysis["hypothesis_validation"]["H5c"] = {
            "hypothesis": "适应迁移比直接转移更有效",
            "direct_retention_rate": direct_retention,
            "adaptive_retention_rate": adaptive_retention,
            "improvement": improvement,
            "expected_threshold": 0.05,
            "passed": improvement >= 0.05
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

    # 相似度影响发现
    if "high" in analysis["summary_by_similarity"] and "low" in analysis["summary_by_similarity"]:
        high_retention = analysis["summary_by_similarity"]["high"]["avg_performance_retention_rate"]
        low_retention = analysis["summary_by_similarity"]["low"]["avg_performance_retention_rate"]
        gap = high_retention - low_retention
        analysis["key_findings"].append(
            f"相似度影响: 高相似度({high_retention:.2%}) vs 低相似度({low_retention:.2%}), 差距 {gap:.2%}"
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
        print(f"{'组别':<10} {'有效性':<10} {'效率':<10} {'保持率':<10} {'适应开销':<12}")
        print("-" * 65)
        for group_id, summary in sorted(analysis["summary_by_group"].items()):
            print(f"{group_id:<10} "
                  f"{summary['avg_operation_effectiveness']:.2%}  "
                  f"{summary['avg_efficiency_gain']:.2f}x     "
                  f"{summary['avg_performance_retention_rate']:.2%}  "
                  f"{summary['avg_adaptation_overhead']:.2%}    ")

    if analysis["summary_by_similarity"]:
        print("\n按相似度分析:")
        print(f"{'相似度':<10} {'平均保持率':<15}")
        print("-" * 30)
        for similarity, summary in sorted(analysis["summary_by_similarity"].items()):
            print(f"{similarity:<10} {summary['avg_performance_retention_rate']:.2%}    ")

    if analysis["summary_by_strategy"]:
        print("\n按迁移策略分析:")
        print(f"{'策略':<10} {'平均保持率':<15} {'平均改进':<15}")
        print("-" * 45)
        for strategy, summary in sorted(analysis["summary_by_strategy"].items()):
            improvement = summary.get('avg_adaptive_improvement', 0.0)
            print(f"{strategy:<10} {summary['avg_performance_retention_rate']:.2%}    "
                  f"{improvement:.2%}    ")

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
