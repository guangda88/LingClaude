#!/usr/bin/env python3
"""
EXP-002 实时监控脚本
"""

import json
import time
from pathlib import Path
from datetime import datetime

class EXP002Monitor:
    def __init__(self):
        self.base_dir = Path(__file__).parent.parent.parent / "data" / "EXP-002"
        self.current_run_id = 0

    def start_run(self, group: str) -> str:
        """开始新的实验运行"""
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 初始化运行状态
        run_state = {
            "run_id": run_id,
            "group": group,
            "started_at": datetime.now().isoformat(),
            "status": "running",
            "metrics": [],
            "checkpoints": []
        }

        # 保存运行状态
        state_file = self.base_dir / f"group_{group}" / f"run_{run_id}.json"
        with open(state_file, "w") as f:
            json.dump(run_state, f, indent=2)

        print(f"✓ Started run {run_id} for group {group}")
        return run_id

    def record_metric(self, group: str, run_id: str, metric_name: str, value: Any):
        """记录指标"""
        timestamp = datetime.now().isoformat()

        metric_entry = {
            "timestamp": timestamp,
            "metric_name": metric_name,
            "value": value
        }

        # 追加到metrics日志
        metrics_file = self.base_dir / f"group_{group}" / "metrics.jsonl"
        with open(metrics_file, "a") as f:
            f.write(json.dumps(metric_entry) + "\n")

    def record_operation(self, group: str, run_id: str, operation: dict):
        """记录操作"""
        operation["timestamp"] = datetime.now().isoformat()
        operation["run_id"] = run_id

        # 追加到operations日志
        ops_file = self.base_dir / f"group_{group}" / "operations.jsonl"
        with open(ops_file, "a") as f:
            f.write(json.dumps(operation) + "\n")

    def checkpoint(self, group: str, run_id: str, elapsed_hours: float):
        """创建检查点"""
        checkpoint = {
            "timestamp": datetime.now().isoformat(),
            "elapsed_hours": elapsed_hours,
            "metrics": self._calculate_current_metrics(group)
        }

        # 追加到检查点列表
        checkpoints_file = self.base_dir / f"group_{group}" / "checkpoints.json"
        with open(checkpoints_file, "r") as f:
            checkpoints = json.load(f)

        checkpoints.append(checkpoint)

        with open(checkpoints_file, "w") as f:
            json.dump(checkpoints, f, indent=2)

        print(f"✓ Checkpoint created for group {group} at {elapsed_hours}h")

    def _calculate_current_metrics(self, group: str) -> dict:
        """计算当前指标"""
        metrics_file = self.base_dir / f"group_{group}" / "metrics.jsonl"

        if not metrics_file.exists():
            return {}

        metrics = []
        with open(metrics_file, "r") as f:
            for line in f:
                metrics.append(json.loads(line))

        # 计算核心指标
        if not metrics:
            return {}

        # 聚合指标
        result = {}
        metric_values = {}

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
        """生成报告"""
        metrics = self._calculate_current_metrics(group)

        report = {
            "experiment_id": "EXP-002",
            "group": group,
            "run_id": run_id,
            "generated_at": datetime.now().isoformat(),
            "metrics": metrics
        }

        return report


if __name__ == "__main__":
    monitor = EXP002Monitor()

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
