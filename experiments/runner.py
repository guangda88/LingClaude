"""
AI自我进化实验运行器

功能：
- 配置和启动实验
- 管理多组实验
- 执行实验任务
- 收集实验数据
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from monitor import ExperimentMonitor, MultiGroupMonitor


class ExperimentRunner:
    """实验运行器"""

    def __init__(self, config_path: Path):
        self.config = self._load_config(config_path)
        self.monitor = MultiGroupMonitor(
            self.config["experiment"]["id"],
            Path(self.config["output"]["data_dir"])
        )
        self.groups: Dict[str, ExperimentGroup] = {}

    def _load_config(self, config_path: Path) -> Dict[str, Any]:
        """加载配置文件"""
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)

    def setup_groups(self) -> None:
        """设置实验组"""
        for group_id, group_config in self.config["groups"].items():
            monitor = ExperimentMonitor(
                experiment_id=self.config["experiment"]["id"],
                group_id=group_id,
                output_dir=Path(self.config["output"]["data_dir"]),
                sample_interval=self.config["metrics"]["sample_interval"]
            )

            # 设置预期人工时间
            task_id = self.config["tasks"][0]["id"]  # 使用第一个任务
            for task in self.config["tasks"]:
                if task["id"] == task_id:
                    monitor.set_expected_human_time(task["expected_human_time_months"] * 160.0)  # 月转小时（假设160小时/月）
                    break

            self.monitor.add_group(group_id, monitor)
            self.groups[group_id] = ExperimentGroup(group_id, group_config, monitor)

    def run_experiment(
        self,
        task_executor: Callable[[str, Any], Dict[str, Any]],
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """运行实验"""
        print(f"\n{'='*60}")
        print(f"实验ID: {self.config['experiment']['id']}")
        print(f"实验名称: {self.config['experiment']['name']}")
        print(f"开始时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}\n")

        if dry_run:
            print("DRY RUN模式 - 仅显示配置，不执行实验")
            self._print_config()
            return {"status": "dry_run", "config": self.config}

        results = {}

        # 运行各组实验
        for group_id, group in self.groups.items():
            print(f"\n{'='*60}")
            print(f"运行实验组: {group_id} ({group.name})")
            print(f"{'='*60}\n")

            result = group.run(task_executor)
            results[group_id] = result

        # 生成对比报告
        comparison = self.monitor.generate_comparison_report()

        # 保存所有报告
        self.monitor.save_all_reports()

        print(f"\n{'='*60}")
        print(f"实验完成")
        print(f"结束时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}\n")

        return {
            "status": "completed",
            "group_results": results,
            "comparison": comparison
        }

    def _print_config(self) -> None:
        """打印配置"""
        print("\n实验组别:")
        for group_id, group_config in self.config["groups"].items():
            print(f"\n  {group_id}: {group_config['name']}")
            print(f"    描述: {group_config['description']}")
            print(f"    配方启用: {group_config['recipe_enabled']}")
            print(f"    时间限制: {group_config['time_limit_hours']}小时")
            print(f"    预期有效性: {group_config['expected_effectiveness']}")
            print(f"    预期效率: {group_config['expected_efficiency']}x")

        print("\n实验任务:")
        for task in self.config["tasks"]:
            print(f"\n  {task['id']}: {task['name']}")
            print(f"    难度: {task['difficulty']}")
            print(f"    预期人工时间: {task['expected_human_time_months']}月")

        print("\n测量指标:")
        for metric in self.config["metrics"]["core"]:
            print(f"  - {metric['name']}: {metric['formula']} (目标: {metric['target']} {metric['unit']})")


class ExperimentGroup:
    """实验组"""

    def __init__(
        self,
        group_id: str,
        config: Dict[str, Any],
        monitor: ExperimentMonitor
    ):
        self.group_id = group_id
        self.name = config["name"]
        self.config = config
        self.monitor = monitor

        # 配方规则
        self.recipe_enabled = config.get("recipe_enabled", False)
        self.recipe_level = config.get("recipe_level", "none")
        self.parallel_enabled = config.get("parallel_enabled", False)
        self.workflow_enforced = config.get("workflow_enforced", False)

        # 时间限制
        self.time_limit_hours = config.get("time_limit_hours", 5.0)

    def run(
        self,
        task_executor: Callable[[str, Any], Dict[str, Any]]
    ) -> Dict[str, Any]:
        """运行实验组"""
        start_time = time.time()

        # 执行任务
        result = self._execute_with_rules(task_executor)

        duration = (time.time() - start_time) / 3600.0

        # 生成报告
        report = self.monitor.generate_report()
        report["duration_hours"] = duration

        return report

    def _execute_with_rules(
        self,
        task_executor: Callable[[str, Any], Dict[str, Any]]
    ) -> Dict[str, Any]:
        """使用配方规则执行任务"""
        if not self.recipe_enabled:
            # 无配方：直接执行
            return task_executor(self.group_id, {})

        # 有配方：应用规则
        recipe = self.config.get("recipe_rules", {})

        # 规则1: read_before_edit
        if recipe.get("read_before_edit"):
            self._enforce_read_before_edit(task_executor)

        # 规则2: test_after_edit
        if recipe.get("test_after_edit"):
            self._enforce_test_after_edit(task_executor)

        # 规则3: parallel_independent
        if recipe.get("parallel_independent") and self.parallel_enabled:
            self._enforce_parallel_operations(task_executor)

        # 执行主任务
        return task_executor(self.group_id, {
            "recipe_level": self.recipe_level,
            "workflow_enforced": self.workflow_enforced
        })

    def _enforce_read_before_edit(self, task_executor: Callable) -> None:
        """强制：编辑前读取"""
        # 在实际执行中，这会在工具调用层面强制执行
        pass

    def _enforce_test_after_edit(self, task_executor: Callable) -> None:
        """强制：编辑后测试"""
        # 在实际执行中，这会在每次编辑后自动运行测试
        pass

    def _enforce_parallel_operations(self, task_executor: Callable) -> None:
        """强制：独立操作并行执行"""
        # 在实际执行中，这会自动识别独立操作并并行执行
        pass

    def record_tool_call(
        self,
        tool_name: str,
        args: Dict[str, Any],
        success: bool,
        duration_ms: float,
        error: Optional[str] = None
    ) -> None:
        """记录工具调用"""
        self.monitor.record_tool_call(tool_name, args, success, duration_ms, error)

    def record_decision(
        self,
        context: Dict[str, Any],
        reasoning: str,
        action: str,
        outcome: str
    ) -> str:
        """记录决策"""
        return self.monitor.record_decision(context, reasoning, action, outcome)


class SimpleTaskExecutor:
    """简单任务执行器（用于演示）"""

    def __init__(self, monitor: ExperimentMonitor):
        self.monitor = monitor

    def execute(self, group_id: str, task_config: Dict[str, Any]) -> Dict[str, Any]:
        """执行任务"""
        print(f"执行任务（组别: {group_id}）")

        # 模拟任务执行
        for i in range(10):
            # 模拟工具调用
            tool_name = f"tool_{i % 3}"
            success = (i % 4 != 0)  # 75% 成功率

            self.monitor.record_tool_call(
                tool_name=tool_name,
                args={"index": i},
                success=success,
                duration_ms=100.0,
                error=None if success else "simulated error"
            )

            # 记录决策
            self.monitor.record_decision(
                context={"step": i},
                reasoning=f"Step {i} reasoning",
                action=f"action_{i}",
                outcome="success" if success else "failure"
            )

            time.sleep(0.1)  # 模拟耗时

        return {"status": "completed", "steps": 10}


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="AI自我进化实验运行器")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("experiments/config.yaml"),
        help="配置文件路径"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="干运行模式（仅显示配置）"
    )
    parser.add_argument(
        "--simple",
        action="store_true",
        help="使用简单任务执行器（演示用）"
    )

    args = parser.parse_args()

    # 创建运行器
    runner = ExperimentRunner(args.config)
    runner.setup_groups()

    # 创建任务执行器
    if args.simple:
        # 简单执行器（演示用）
        task_executor = lambda group_id, config: SimpleTaskExecutor(
            runner.monitor.get_monitor(group_id)
        ).execute(group_id, config)
    else:
        # 实际执行器（需要实现）
        print("错误：请实现实际的任务执行器，或使用 --simple 标志进行演示")
        sys.exit(1)

    # 运行实验
    results = runner.run_experiment(task_executor, dry_run=args.dry_run)

    # 输出结果
    if args.dry_run:
        print("\n干运行完成")
    else:
        print("\n实验结果:")
        print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
