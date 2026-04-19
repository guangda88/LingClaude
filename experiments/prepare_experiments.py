#!/usr/bin/env python3
"""
EXP-001 & EXP-002 实验准备脚本
准备实验环境和数据收集基础设施
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Any


class ExperimentPrep:
    """实验准备器"""

    def __init__(self, base_dir: Path = Path("/home/ai/LingClaude/experiments")):
        self.base_dir = base_dir
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    def create_directory_structure(self, experiment_id: str) -> None:
        """创建实验目录结构"""
        exp_dir = self.base_dir / "data" / experiment_id
        report_dir = self.base_dir / "reports" / experiment_id
        plot_dir = self.base_dir / "plots" / experiment_id

        # 创建目录
        for directory in [exp_dir, report_dir, plot_dir]:
            directory.mkdir(parents=True, exist_ok=True)
            print(f"✓ Created: {directory}")

        # 创建子目录
        for group in ["A", "B", "C", "D", "E", "F"]:
            group_dir = exp_dir / f"group_{group}"
            group_dir.mkdir(exist_ok=True)
            print(f"  - Created: {group_dir}")

        print(f"\n✓ Directory structure ready for {experiment_id}")

    def initialize_data_collection(self, experiment_id: str, groups: list[str]) -> None:
        """初始化数据收集文件"""
        exp_dir = self.base_dir / "data" / experiment_id

        for group in groups:
            group_dir = exp_dir / f"group_{group}"

            # 初始化指标日志
            metrics_file = group_dir / "metrics.jsonl"
            if not metrics_file.exists():
                metrics_file.touch()
                print(f"  - Created: {metrics_file}")

            # 初始化操作日志
            operations_file = group_dir / "operations.jsonl"
            if not operations_file.exists():
                operations_file.touch()
                print(f"  - Created: {operations_file}")

            # 初始化决策日志
            decisions_file = group_dir / "decisions.jsonl"
            if not decisions_file.exists():
                decisions_file.touch()
                print(f"  - Created: {decisions_file}")

            # 初始化检查点文件
            checkpoints_file = group_dir / "checkpoints.json"
            if not checkpoints_file.exists():
                with open(checkpoints_file, "w") as f:
                    json.dump([], f)
                print(f"  - Created: {checkpoints_file}")

            # 初始化元数据
            metadata = {
                "experiment_id": experiment_id,
                "group": group,
                "created_at": datetime.now().isoformat(),
                "status": "initialized",
                "runs": [],
                "metrics": {
                    "operation_effectiveness": None,
                    "efficiency_gain": None,
                    "cognitive_stability": None,
                    "parallel_speedup": None,
                    "strategy_count": 0
                }
            }

            metadata_file = group_dir / "metadata.json"
            with open(metadata_file, "w") as f:
                json.dump(metadata, f, indent=2)
            print(f"  - Created: {metadata_file}")

        print(f"\n✓ Data collection initialized for {experiment_id}")

    def create_monitoring_templates(self, experiment_id: str) -> None:
        """创建监控模板"""
        monitoring_dir = self.base_dir / "monitoring" / experiment_id
        monitoring_dir.mkdir(parents=True, exist_ok=True)

        # 实时监控脚本模板
        monitor_template = f"""#!/usr/bin/env python3
\"\"\"
{experiment_id} 实时监控脚本
\"\"\"

import json
import time
from pathlib import Path
from datetime import datetime

class {experiment_id.replace("-", "")}Monitor:
    def __init__(self):
        self.base_dir = Path(__file__).parent.parent.parent / "data" / "{experiment_id}"
        self.current_run_id = 0

    def start_run(self, group: str) -> str:
        \"\"\"开始新的实验运行\"\"\"
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 初始化运行状态
        run_state = {{
            "run_id": run_id,
            "group": group,
            "started_at": datetime.now().isoformat(),
            "status": "running",
            "metrics": [],
            "checkpoints": []
        }}

        # 保存运行状态
        state_file = self.base_dir / f"group_{{group}}" / f"run_{{run_id}}.json"
        with open(state_file, "w") as f:
            json.dump(run_state, f, indent=2)

        print(f"✓ Started run {{run_id}} for group {{group}}")
        return run_id

    def record_metric(self, group: str, run_id: str, metric_name: str, value: Any):
        \"\"\"记录指标\"\"\"
        timestamp = datetime.now().isoformat()

        metric_entry = {{
            "timestamp": timestamp,
            "metric_name": metric_name,
            "value": value
        }}

        # 追加到metrics日志
        metrics_file = self.base_dir / f"group_{{group}}" / "metrics.jsonl"
        with open(metrics_file, "a") as f:
            f.write(json.dumps(metric_entry) + "\\n")

    def record_operation(self, group: str, run_id: str, operation: dict):
        \"\"\"记录操作\"\"\"
        operation["timestamp"] = datetime.now().isoformat()
        operation["run_id"] = run_id

        # 追加到operations日志
        ops_file = self.base_dir / f"group_{{group}}" / "operations.jsonl"
        with open(ops_file, "a") as f:
            f.write(json.dumps(operation) + "\\n")

    def checkpoint(self, group: str, run_id: str, elapsed_hours: float):
        \"\"\"创建检查点\"\"\"
        checkpoint = {{
            "timestamp": datetime.now().isoformat(),
            "elapsed_hours": elapsed_hours,
            "metrics": self._calculate_current_metrics(group)
        }}

        # 追加到检查点列表
        checkpoints_file = self.base_dir / f"group_{{group}}" / "checkpoints.json"
        with open(checkpoints_file, "r") as f:
            checkpoints = json.load(f)

        checkpoints.append(checkpoint)

        with open(checkpoints_file, "w") as f:
            json.dump(checkpoints, f, indent=2)

        print(f"✓ Checkpoint created for group {{group}} at {{elapsed_hours}}h")

    def _calculate_current_metrics(self, group: str) -> dict:
        \"\"\"计算当前指标\"\"\"
        metrics_file = self.base_dir / f"group_{{group}}" / "metrics.jsonl"

        if not metrics_file.exists():
            return {{}}

        metrics = []
        with open(metrics_file, "r") as f:
            for line in f:
                metrics.append(json.loads(line))

        # 计算核心指标
        if not metrics:
            return {{}}

        # 聚合指标
        result = {{}}
        metric_values = {{}}

        for m in metrics:
            name = m["metric_name"]
            value = m["value"]

            if name not in metric_values:
                metric_values[name] = []

            metric_values[name].append(value)

        # 计算平均值
        for name, values in metric_values.items():
            result[name] = sum(values) / len(values)

        return result

    def generate_report(self, group: str, run_id: str) -> dict:
        \"\"\"生成报告\"\"\"
        metrics = self._calculate_current_metrics(group)

        report = {{
            "experiment_id": "{experiment_id}",
            "group": group,
            "run_id": run_id,
            "generated_at": datetime.now().isoformat(),
            "metrics": metrics
        }}

        return report


if __name__ == "__main__":
    monitor = {experiment_id.replace("-", "")}Monitor()

    # 示例：启动运行
    run_id = monitor.start_run("A")

    # 示例：记录指标
    monitor.record_metric("A", run_id, "operation_effectiveness", 0.65)
    monitor.record_metric("A", run_id, "efficiency_gain", 2.5)

    # 示例：创建检查点
    monitor.checkpoint("A", run_id, 1.0)

    # 示例：生成报告
    report = monitor.generate_report("A", run_id)
    print(json.dumps(report, indent=2))
"""

        monitor_file = monitoring_dir / "monitor.py"
        with open(monitor_file, "w") as f:
            f.write(monitor_template)
        print(f"✓ Created: {monitor_file}")

        # 批处理脚本模板
        batch_template = f"""#!/bin/bash
# {experiment_id} 批处理脚本
# 并行运行多个实验组

set -e

echo "Starting {experiment_id} experiments..."

# 创建日志目录
LOG_DIR="logs/{experiment_id}"
mkdir -p "$LOG_DIR"

# 并行运行实验组
# 组A
python3 -m experiments.runner EXP-001 A > "$LOG_DIR/group_A.log" 2>&1 &
PID_A=$!

# 组B
python3 -m experiments.runner EXP-001 B > "$LOG_DIR/group_B.log" 2>&1 &
PID_B=$!

# 组C
python3 -m experiments.runner EXP-001 C > "$LOG_DIR/group_C.log" 2>&1 &
PID_C=$!

echo "Started all groups. PIDs: $PID_A, $PID_B, $PID_C"
echo "Monitoring progress..."

# 等待所有组完成
wait $PID_A
echo "✓ Group A completed"

wait $PID_B
echo "✓ Group B completed"

wait $PID_C
echo "✓ Group C completed"

echo "All groups completed. Generating reports..."
python3 -m experiments.analyzer {experiment_id}

echo "✓ {experiment_id} completed"
"""

        batch_file = monitoring_dir / "batch_run.sh"
        with open(batch_file, "w") as f:
            f.write(batch_template)
        os.chmod(batch_file, 0o755)
        print(f"✓ Created: {batch_file}")

        print(f"\n✓ Monitoring templates created for {experiment_id}")

    def create_experiment_summary(self, experiment_id: str, config_file: Path) -> None:
        """创建实验摘要"""
        summary_dir = self.base_dir / "summaries"
        summary_dir.mkdir(exist_ok=True)

        # 创建摘要（不需要读取完整YAML配置）
        summary = {
            "experiment_id": experiment_id,
            "name": experiment_id.replace("-", " ").title(),
            "prepared_at": datetime.now().isoformat(),
            "status": "ready"
        }

        summary_file = summary_dir / f"{experiment_id}_summary.json"
        with open(summary_file, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"✓ Created: {summary_file}")

    def prepare_experiment(self, experiment_id: str, config_file: Path, groups: list[str]) -> None:
        """准备完整实验"""
        print(f"\n{'='*60}")
        print(f"Preparing {experiment_id}")
        print(f"{'='*60}\n")

        # 创建目录结构
        self.create_directory_structure(experiment_id)

        # 初始化数据收集
        self.initialize_data_collection(experiment_id, groups)

        # 创建监控模板
        self.create_monitoring_templates(experiment_id)

        # 创建摘要
        self.create_experiment_summary(experiment_id, config_file)

        print(f"\n{'='*60}")
        print(f"✓ {experiment_id} preparation complete")
        print(f"{'='*60}\n")


def main():
    """主函数"""
    prep = ExperimentPrep()

    # 准备EXP-001
    print("\n" + "="*60)
    print("PREPARING EXP-001: Recipe Effectiveness Validation")
    print("="*60)

    exp001_config = prep.base_dir / "EXP-001_config.yaml"
    if not exp001_config.exists():
        print(f"✗ Config file not found: {exp001_config}")
        sys.exit(1)

    prep.prepare_experiment("EXP-001", exp001_config, ["A", "B", "C"])

    # 准备EXP-002
    print("\n" + "="*60)
    print("PREPARING EXP-002: Evolution Mechanism Isolation")
    print("="*60)

    exp002_config = prep.base_dir / "EXP-002_config.yaml"
    if not exp002_config.exists():
        print(f"✗ Config file not found: {exp002_config}")
        sys.exit(1)

    prep.prepare_experiment("EXP-002", exp002_config, ["D", "E", "F"])

    print("\n" + "="*60)
    print("ALL EXPERIMENTS PREPARED")
    print("="*60)
    print("\nNext steps:")
    print("1. Review experiment configurations")
    print("2. Run experiments:")
    print("   python3 -m experiments.runner EXP-001 A")
    print("   python3 -m experiments.runner EXP-001 B")
    print("   python3 -m experiments.runner EXP-001 C")
    print("   python3 -m experiments.runner EXP-002 D")
    print("   python3 -m experiments.runner EXP-002 E")
    print("   python3 -m experiments.runner EXP-002 F")
    print("3. Analyze results:")
    print("   python3 -m experiments.analyzer EXP-001")
    print("   python3 -m experiments.analyzer EXP-002")


if __name__ == "__main__":
    main()
