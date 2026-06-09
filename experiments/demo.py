# AI自我进化实验演示脚本

"""
本脚本演示如何使用AI自我进化实验框架
"""

from __future__ import annotations

import time
from pathlib import Path

from monitor import ExperimentMonitor
from analyzer import ExperimentAnalyzer


def demo_monitor():
    """演示监控器功能"""
    print("\n" + "="*60)
    print("演示 1: 实验监控器")
    print("="*60 + "\n")

    # 创建监控器
    monitor = ExperimentMonitor(
        experiment_id="DEMO-001",
        group_id="enhanced",
        output_dir=Path("experiments/data"),
        sample_interval=60  # 60秒采样
    )

    # 设置预期人工时间
    monitor.set_expected_human_time(960.0)  # 6个月 × 160小时/月

    print("模拟执行任务...")

    # 模拟工具调用
    tools = ["view", "edit", "bash", "grep", "glob", "test"]
    for i in range(50):
        tool_name = tools[i % len(tools)]
        success = (i % 5 != 0)  # 80% 成功率
        duration = 100.0 + (i % 10) * 10  # 100-200ms

        monitor.record_tool_call(
            tool_name=tool_name,
            args={"index": i},
            success=success,
            duration_ms=duration,
            error=None if success else f"Error in {tool_name}"
        )

        # 模拟决策
        if i % 5 == 0:
            monitor.record_decision(
                context={"step": i},
                reasoning=f"Need to {tool_name} file",
                action=f"Call {tool_name}",
                outcome="success" if success else "failure"
            )

        # 模拟并行操作
        if success and tool_name in ["view", "grep"]:
            monitor.record_parallel_operation()
        else:
            monitor.record_sequential_operation()

        # 模拟重复操作（认知稳定性）
        if i % 10 == 0:
            monitor.record_repeated_operation(
                operation_key=f"read_file_{i // 10}",
                consistency_score=0.85 + (i % 3) * 0.05
            )

        time.sleep(0.01)

    # 添加策略
    monitor.add_strategy(
        name="view_edit_test_pattern",
        description="Read file, edit, then test",
        pattern="view -> edit -> test",
        success_rate=0.92,
        avg_duration=150.0
    )

    # 捕获快照
    snapshot = monitor.capture_snapshot()

    print(f"\n指标快照:")
    print(f"  操作有效性: {snapshot.operation_effectiveness:.2%}")
    print(f"  效率提升: {snapshot.efficiency_gain:.2f}x")
    print(f"  认知稳定性: {snapshot.cognitive_stability:.3f}")
    print(f"  并行加速比: {snapshot.parallel_speedup:.2f}x")
    print(f"  策略数量: {snapshot.strategy_count}")

    print(f"\n工具使用分布:")
    distribution = monitor.get_tool_usage_distribution()
    for tool, count in sorted(distribution.items(), key=lambda x: x[1], reverse=True):
        print(f"  {tool}: {count}")

    print(f"\n失败模式:")
    failure_modes = monitor.get_failure_modes()
    for mode, count in sorted(failure_modes.items(), key=lambda x: x[1], reverse=True):
        print(f"  {mode}: {count}")

    # 保存报告
    monitor.save_report()
    print(f"\n报告已保存到: experiments/data/{monitor.experiment_id}_{monitor.group_id}_report.json")


def demo_analyzer():
    """演示分析器功能"""
    print("\n" + "="*60)
    print("演示 2: 实验分析器")
    print("="*60 + "\n")

    # 创建分析器
    analyzer = ExperimentAnalyzer(
        data_dir=Path("experiments/data"),
        plot_dir=Path("experiments/plots")
    )

    # 加载数据（如果有）
    try:
        analyzer.load_data("DEMO-001")
    except (FileNotFoundError, ValueError, KeyError):
        print("没有找到实验数据，跳过分析器演示")
        return

    # 生成摘要
    summary = analyzer.generate_summary()
    print("实验摘要:")
    print(f"  实验ID: {summary.get('experiment_id', 'unknown')}")
    print(f"  实验组数: {summary['groups']}")
    print(f"  组别: {', '.join(summary['group_ids'])}")

    # 生成 Markdown 报告
    report_path = Path("experiments/reports/DEMO-001_analysis.md")
    analyzer.generate_markdown_report(report_path)
    print(f"\nMarkdown 报告已保存到: {report_path}")

    # 生成图表
    print("\n生成可视化图表...")
    plot_files = analyzer.generate_plots()
    if plot_files:
        print("图表已生成:")
        for plot_file in plot_files:
            print(f"  - {plot_file}")
    else:
        print("没有生成图表（需要安装 matplotlib）")


def demo_multi_group():
    """演示多组对比"""
    print("\n" + "="*60)
    print("演示 3: 多组对比实验")
    print("="*60 + "\n")

    from monitor import MultiGroupMonitor

    # 创建多组监控器
    multi_monitor = MultiGroupMonitor(
        experiment_id="DEMO-002",
        output_dir=Path("experiments/data")
    )

    # 创建三个实验组
    group_configs = {
        "control": {"expected": 0.65, "efficiency": 2.5},
        "basic": {"expected": 0.82, "efficiency": 8.0},
        "enhanced": {"expected": 0.94, "efficiency": 25.0}
    }

    for group_id, config in group_configs.items():
        monitor = ExperimentMonitor(
            experiment_id="DEMO-002",
            group_id=group_id,
            output_dir=Path("experiments/data"),
            sample_interval=60
        )
        monitor.set_expected_human_time(960.0)

        # 模拟不同的性能
        success_rate = config["expected"]
        efficiency = config["efficiency"]

        # 模拟工具调用
        for i in range(100):
            success = (i % 100 < success_rate * 100)
            monitor.record_tool_call(
                tool_name=f"tool_{i % 5}",
                args={"index": i},
                success=success,
                duration_ms=100.0,
                error=None if success else "simulated error"
            )

        multi_monitor.add_group(group_id, monitor)

    # 对比各组
    comparison = multi_monitor.generate_comparison_report()

    print("各组对比:")
    print(f"  操作有效性:")
    for group_id, value in comparison["operation_effectiveness"].items():
        print(f"    {group_id}: {value:.2%}")

    print(f"\n  效率提升:")
    for group_id, value in comparison["efficiency_gain"].items():
        print(f"    {group_id}: {value:.2f}x")

    print(f"\n  成功率:")
    for group_id, value in comparison["success_rates"].items():
        print(f"    {group_id}: {value:.2%}")

    # 保存对比报告
    multi_monitor.save_all_reports()
    print(f"\n对比报告已保存到: experiments/data/DEMO-002_comparison_report.json")


def main():
    """主函数"""
    print("\n" + "="*60)
    print("AI自我进化实验框架 - 演示")
    print("="*60)

    # 演示1: 监控器
    demo_monitor()

    # 演示2: 分析器
    demo_analyzer()

    # 演示3: 多组对比
    demo_multi_group()

    print("\n" + "="*60)
    print("演示完成")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
