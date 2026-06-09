#!/usr/bin/env python3
"""GLM 套餐最大化使用工具

功能：
- 自动生成批量编程任务
- 按频率执行任务
- 监控配额使用情况
- 达到目标后停止
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List


class GLMQuotaOptimizer:
    """GLM 配额优化器"""

    def __init__(self, target_ratio: float = 0.8):
        """初始化优化器

        Args:
            target_ratio: 目标配额比例 (0.8 = 80%)
        """
        self.target_ratio = target_ratio
        self.quota_limit = 160000  # 5小时周期配额
        self.db_path = Path.home() / ".lingclaude" / "token_monitor.db"

    def get_current_usage(self) -> dict:
        """获取当前使用情况

        Returns:
            使用情况字典
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        # 获取今日统计
        today = datetime.now(timezone.utc).date().isoformat()
        cursor.execute("""
            SELECT SUM(total_tokens), SUM(input_tokens), SUM(output_tokens), COUNT(*)
            FROM usage_records
            WHERE DATE(timestamp) = ?
        """, (today,))

        result = cursor.fetchone()
        conn.close()

        return {
            "total_tokens": result[0] or 0,
            "input_tokens": result[1] or 0,
            "output_tokens": result[2] or 0,
            "prompt_count": result[3] or 0,
        }

    def get_remaining_tokens(self) -> int:
        """获取剩余可用 tokens

        Returns:
            剩余 tokens 数量
        """
        usage = self.get_current_usage()
        return self.quota_limit - usage["total_tokens"]

    def is_quota_reached(self) -> bool:
        """检查是否达到目标

        Returns:
            是否达到目标
        """
        usage = self.get_current_usage()
        current_ratio = usage["total_tokens"] / self.quota_limit
        return current_ratio >= self.target_ratio

    def generate_tasks(self, count: int = 10) -> List[str]:
        """生成编程任务

        Args:
            count: 任务数量

        Returns:
            任务列表
        """
        tasks = [
            # 算法类
            "写一个快速排序函数，使用 Python",
            "实现二分查找算法",
            "写一个归并排序函数",
            "实现深度优先搜索 (DFS)",
            "实现广度优先搜索 (BFS)",
            "写一个堆排序函数",
            "实现动态规划求解斐波那契数列",
            "写一个 KMP 字符串匹配算法",

            # 数据结构类
            "实现一个单链表类",
            "实现一个双端队列",
            "写一个哈希表实现",
            "实现一个二叉搜索树",
            "写一个图的数据结构",

            # 实用工具类
            "写一个 CSV 文件读取器",
            "实现一个简单的 JSON 解析器",
            "写一个 HTTP 请求封装类",
            "实现一个日志记录器",
            "写一个配置文件解析器",

            # 代码优化类
            "优化这个列表去重算法",
            "改进这个字符串处理函数",
            "优化这个循环嵌套",
            "改进这个递归函数的效率",
            "优化这个数据库查询",

            # 测试类
            "为一个排序函数写单元测试",
            "为一个 API 端点写测试用例",
            "为一个数据库操作写集成测试",
            "为一个工具函数写测试",
            "为一个类写完整的测试套件",

            # 代码生成类
            "生成一个用户认证模块",
            "写一个 REST API 框架的基础结构",
            "实现一个简单的 ORM",
            "写一个任务调度器",
            "实现一个缓存系统",

            # 文档生成类
            "为一个函数生成完整的文档字符串",
            "为一个类生成使用示例",
            "为整个模块生成 API 文档",
            "写一个 README.md 模板",
            "生成 CHANGELOG 模板",

            # 重构类
            "重构这个函数，使其更易读",
            "重构这个类，使用设计模式",
            "优化这个模块的代码结构",
            "重构这个循环，使用列表推导式",
            "重构这个条件判断，使其更清晰",

            # Debug 类
            "分析这个异常的原因",
            "找出这段代码的性能瓶颈",
            "诊断这个内存泄漏问题",
            "分析这个并发 bug",
            "调试这个递归栈溢出",
        ]

        # 如果需要更多任务，循环使用
        while len(tasks) < count:
            tasks.extend(tasks[:count])

        return tasks[:count]

    def calculate_prompts_needed(self) -> dict:
        """计算需要的 prompts 数量

        Returns:
            需要的 prompts 信息
        """
        usage = self.get_current_usage()
        target_tokens = int(self.quota_limit * self.target_ratio)
        remaining_tokens = target_tokens - usage["total_tokens"]

        avg_tokens_per_prompt = 409  # 从历史统计
        prompts_needed = int(remaining_tokens / avg_tokens_per_prompt)

        return {
            "target_tokens": target_tokens,
            "remaining_tokens": remaining_tokens,
            "prompts_needed": prompts_needed,
            "current_ratio": usage["total_tokens"] / self.quota_limit,
            "target_ratio": self.target_ratio,
        }

    def print_status(self):
        """打印当前状态"""
        usage = self.get_current_usage()
        prompts_needed = self.calculate_prompts_needed()

        print("\n" + "=" * 80)
        print("📊 GLM 配额使用状态")
        print("=" * 80)
        print("\n当前使用：")
        print(f"  总 Token: {usage['total_tokens']:,} / {self.quota_limit:,}")
        print(f"  利用率: {usage['total_tokens'] / self.quota_limit * 100:.1f}%")
        print(f"  Prompts: {usage['prompt_count']}")
        print("\n目标：")
        print(f"  目标配额: {prompts_needed['target_tokens']:,} tokens ({prompts_needed['target_ratio'] * 100:.0f}%)")
        print(f"  还需要: {prompts_needed['remaining_tokens']:,} tokens")
        print(f"  还需要: {prompts_needed['prompts_needed']} prompts")
        print("\n预估：")
        avg_tokens = usage['total_tokens'] / max(usage['prompt_count'], 1)
        print(f"  平均 Token/Prompt: {avg_tokens:.0f}")
        print(f"  预计时间: {prompts_needed['prompts_needed']} prompts × 5秒 = {prompts_needed['prompts_needed'] * 5 // 60} 分钟")

        if self.is_quota_reached():
            print("\n✅ 已达到目标！")
        else:
            remaining_prompts = prompts_needed['prompts_needed']
            if remaining_prompts > 0:
                print(f"\n⏳ 继续执行 {remaining_prompts} 个 prompts 以达到目标")

        print("=" * 80)


def main():
    """主函数：交互式使用"""
    import argparse

    parser = argparse.ArgumentParser(description="GLM 配额优化工具")
    parser.add_argument("--check", action="store_true", help="检查当前状态")
    parser.add_argument("--tasks", type=int, default=10, help="生成任务数量")
    parser.add_argument("--target", type=float, default=0.8, help="目标配额比例 (默认 0.8)")

    args = parser.parse_args()

    optimizer = GLMQuotaOptimizer(target_ratio=args.target)

    if args.check:
        optimizer.print_status()
    else:
        # 生成任务
        tasks = optimizer.generate_tasks(count=args.tasks)

        print("\n" + "=" * 80)
        print(f"📋 生成了 {len(tasks)} 个编程任务")
        print("=" * 80)

        for i, task in enumerate(tasks, 1):
            print(f"\n{i}. {task}")

        print("\n" + "=" * 80)
        print("💡 使用建议")
        print("=" * 80)
        print("\n方法 1: 手动执行")
        print("  将上面的任务复制到 lingclaude 中逐个执行")

        print("\n方法 2: 批量执行")
        print("  使用 lingclaude 的 TaskScheduler 批量处理")

        print("\n方法 3: 定时执行")
        print("  设置定时任务，每隔 X 分钟执行一批任务")

        # 显示当前状态
        optimizer.print_status()

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
