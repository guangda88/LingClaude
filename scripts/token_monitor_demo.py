#!/usr/bin/env python3
"""token_monitor 演示 CLI（零引用演示代码，自 token_monitor.py 剥离，2026-09-15）。

与 scripts/task_aggregation_demo.py 同构：核心模块只留真职责，演示脚本独立可运行。
"""
from __future__ import annotations

import sys

from lingclaude.core.token_monitor import TARGET_MODEL, TokenMonitor


def main() -> int:
    """主函数：生成报告"""
    print("=" * 80)
    print("📊 GLM Token 使用监控")
    print("=" * 80)

    # 创建监控器
    monitor = TokenMonitor()

    # 添加示例数据（仅用于演示）
    print("\n📝 添加示例数据...")
    monitor.record_usage(
        model=TARGET_MODEL,
        task_type="code_generation",
        total_tokens=15000,
        input_tokens=5000,
        output_tokens=10000,
    )
    monitor.record_usage(
        model="GLM-5.1",
        task_type="analysis",
        total_tokens=25000,
        input_tokens=10000,
        output_tokens=15000,
    )
    monitor.record_usage(
        model=TARGET_MODEL,
        task_type="search",
        total_tokens=8000,
        input_tokens=3000,
        output_tokens=5000,
    )

    # 记录文件读取
    monitor.record_file_read("/path/to/file1.py", "content1")
    monitor.record_file_read("/path/to/file1.py", "content1")  # 重复
    monitor.record_file_read("/path/to/file2.py", "content2")

    # 生成报告
    print("\n📈 生成 HTML 报告...")
    html_path = monitor.generate_html_report()
    print(f"✓ HTML 报告：{html_path}")

    print("\n📝 生成 Markdown 报告...")
    md_path = monitor.generate_markdown_report()
    print(f"✓ Markdown 报告：{md_path}")

    # 显示统计
    print("\n📊 今日统计：")
    stats = monitor.get_daily_stats()
    metrics = monitor.get_efficiency_metrics()

    print(f"  总 Token 数：{stats.total_tokens:,}")
    print(f"  Prompt 数量：{stats.prompt_count:,}")
    print(f"  {TARGET_MODEL} 使用率：{metrics.glm_4_7_ratio * 100:.1f}%")
    print(f"  重复读取率：{metrics.duplicate_read_ratio * 100:.1f}%")

    print("\n" + "=" * 80)
    print("✅ 监控报告生成完成！")
    print("=" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(main())
