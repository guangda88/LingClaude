"""任务聚合演示脚本（原 task_aggregation.main，已从核心模块剥离）。

保留演示能力但不占核心模块行数——灵元减法：核心只留真职责，演示归 scripts/。
运行：python -m scripts.task_aggregation_demo
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lingclaude.core.task_aggregation import TaskAggregator, TaskPriority


def main() -> int:
    """主函数：测试任务聚合"""
    print("=" * 80)
    print("📦 任务聚合测试")
    print("=" * 80)

    # 创建聚合器
    aggregator = TaskAggregator(max_group_size=5, max_wait_seconds=30)

    # 添加测试任务
    print("\n➕ 添加测试任务...")

    # 相关任务 1（同一目录的代码生成）
    aggregator.add_task(
        query="写一个 hello world 函数",
        task_type="code_generation",
        context={"files": ["/home/ai/project/utils.py"]},
        priority=TaskPriority.HIGH,
    )

    # 相关任务 2（同一目录的代码生成）
    aggregator.add_task(
        query="写一个日志函数",
        task_type="code_generation",
        context={"files": ["/home/ai/project/utils.py"]},
        priority=TaskPriority.HIGH,
    )

    # 相关任务 3（同一目录的代码生成）
    aggregator.add_task(
        query="写一个配置加载函数",
        task_type="code_generation",
        context={"files": ["/home/ai/project/config.py"]},
        priority=TaskPriority.MEDIUM,
    )

    # 独立任务（不同目录）
    aggregator.add_task(
        query="分析整个项目架构",
        task_type="analysis",
        context={"files": ["/home/ai/project/"]},
        priority=TaskPriority.LOW,
    )

    print("✓ 已添加 4 个任务")

    # 聚合任务
    print("\n🔄 聚合任务...")
    groups = aggregator.aggregate_tasks()

    print("✓ 创建了", len(groups), "个任务组")

    # 显示任务组
    for i, group in enumerate(groups, 1):
        print(f"\n任务组 {i}:")
        print(f"  ID: {group.id}")
        print(f"  任务数: {group.size}")
        print(f"  状态: {group.status}")
        print("  合并查询（前 200 字符）:")
        for j, task in enumerate(group.tasks, 1):
            print(f"    {j}. {task.query[:100]}...")

    # 统计
    print("\n" + "=" * 80)
    print("📊 聚合统计")
    print("=" * 80)
    stats = aggregator.get_stats()
    print(f"  总任务数: {stats.total_tasks}")
    print(f"  总组数: {stats.total_groups}")
    print(f"  批量任务数: {stats.batched_tasks}")
    print(f"  独立任务数: {stats.standalone_tasks}")
    print(f"  平均组大小: {stats.avg_group_size:.1f}")
    print(f"  估算节省 tokens: {stats.tokens_saved:,}")

    # 模拟处理完成
    print("\n✅ 标记任务组为完成...")
    for group in groups:
        aggregator.mark_group_completed(group.id)
    print(f"✓ 已完成 {len(groups)} 个任务组")

    print("\n" + "=" * 80)
    print("✅ 任务聚合测试完成！")
    print("=" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(main())
