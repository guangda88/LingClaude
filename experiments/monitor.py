"""
AI自我进化实验监控工具

功能：
- 实时监控实验进度和指标
- 记录工具调用和决策
- 计算核心指标
- 生成可视化图表
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np


@dataclass
class ToolCall:
    """工具调用记录"""
    tool_name: str
    timestamp: float
    args: Dict[str, Any]
    success: bool
    duration_ms: float
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "timestamp": self.timestamp,
            "args": self.args,
            "success": self.success,
            "duration_ms": self.duration_ms,
            "error": self.error
        }


@dataclass
class Decision:
    """决策记录"""
    decision_id: str
    timestamp: float
    context: Dict[str, Any]
    reasoning: str
    action: str
    outcome: str  # success, failure, partial

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "timestamp": self.timestamp,
            "context": self.context,
            "reasoning": self.reasoning,
            "action": self.action,
            "outcome": self.outcome
        }


@dataclass
class MetricSnapshot:
    """指标快照"""
    timestamp: float
    operation_effectiveness: float
    efficiency_gain: float
    cognitive_stability: float
    parallel_speedup: float
    strategy_count: int
    attempts: int
    successes: int
    failures: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "operation_effectiveness": self.operation_effectiveness,
            "efficiency_gain": self.efficiency_gain,
            "cognitive_stability": self.cognitive_stability,
            "parallel_speedup": self.parallel_speedup,
            "strategy_count": self.strategy_count,
            "attempts": self.attempts,
            "successes": self.successes,
            "failures": self.failures
        }


@dataclass
class Strategy:
    """提取的策略"""
    strategy_id: str
    name: str
    description: str
    pattern: str
    success_rate: float
    avg_duration: float
    usage_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "description": self.description,
            "pattern": self.pattern,
            "success_rate": self.success_rate,
            "avg_duration": self.avg_duration,
            "usage_count": self.usage_count
        }


class ExperimentMonitor:
    """实验监控器"""

    def __init__(
        self,
        experiment_id: str,
        group_id: str,
        output_dir: Path,
        sample_interval: float = 1800.0  # 30分钟
    ):
        self.experiment_id = experiment_id
        self.group_id = group_id
        self.output_dir = Path(output_dir)
        self.sample_interval = sample_interval

        # 数据存储
        self.tool_calls: List[ToolCall] = []
        self.decisions: List[Decision] = []
        self.metrics_snapshots: List[MetricSnapshot] = []
        self.strategies: List[Strategy] = []

        # 计数器
        self.attempts = 0
        self.successes = 0
        self.failures = 0
        self.parallel_operations = 0
        self.sequential_operations = 0

        # 时间追踪
        self.start_time = time.time()
        self.last_sample_time = self.start_time

        # 任务期望时间（用于计算效率提升）
        self.expected_human_time: float = 0.0  # 小时

        # 认知稳定性追踪（重复操作的一致性）
        self.repeated_operations: Dict[str, List[float]] = {}

        # 策略缓存（用于识别重复模式）
        self.pattern_cache: Dict[str, Dict] = {}

    def set_expected_human_time(self, hours: float) -> None:
        """设置预期人工时间"""
        self.expected_human_time = hours

    def record_tool_call(
        self,
        tool_name: str,
        args: Dict[str, Any],
        success: bool,
        duration_ms: float,
        error: Optional[str] = None
    ) -> None:
        """记录工具调用"""
        call = ToolCall(
            tool_name=tool_name,
            timestamp=time.time(),
            args=args,
            success=success,
            duration_ms=duration_ms,
            error=error
        )
        self.tool_calls.append(call)
        self.attempts += 1

        if success:
            self.successes += 1
        else:
            self.failures += 1

        # 自动采样
        self._auto_sample()

    def record_decision(
        self,
        context: Dict[str, Any],
        reasoning: str,
        action: str,
        outcome: str
    ) -> str:
        """记录决策"""
        decision = Decision(
            decision_id=f"DEC-{len(self.decisions):06d}",
            timestamp=time.time(),
            context=context,
            reasoning=reasoning,
            action=action,
            outcome=outcome
        )
        self.decisions.append(decision)
        return decision.decision_id

    def record_parallel_operation(self) -> None:
        """记录并行操作"""
        self.parallel_operations += 1

    def record_sequential_operation(self) -> None:
        """记录串行操作"""
        self.sequential_operations += 1

    def record_repeated_operation(self, operation_key: str, consistency_score: float) -> None:
        """记录重复操作（用于认知稳定性）"""
        if operation_key not in self.repeated_operations:
            self.repeated_operations[operation_key] = []
        self.repeated_operations[operation_key].append(consistency_score)

    def add_strategy(
        self,
        name: str,
        description: str,
        pattern: str,
        success_rate: float,
        avg_duration: float
    ) -> None:
        """添加提取的策略"""
        # 检查是否已存在相似策略
        for existing in self.strategies:
            if existing.pattern == pattern:
                # 更新现有策略
                existing.usage_count += 1
                # 重新计算成功率和平均时间
                existing.success_rate = (existing.success_rate * (existing.usage_count - 1) + success_rate) / existing.usage_count
                existing.avg_duration = (existing.avg_duration * (existing.usage_count - 1) + avg_duration) / existing.usage_count
                return

        # 创建新策略
        strategy = Strategy(
            strategy_id=f"STR-{len(self.strategies):06d}",
            name=name,
            description=description,
            pattern=pattern,
            success_rate=success_rate,
            avg_duration=avg_duration,
            usage_count=1
        )
        self.strategies.append(strategy)

    def _auto_sample(self) -> None:
        """自动采样（检查是否达到采样间隔）"""
        now = time.time()
        if now - self.last_sample_time >= self.sample_interval:
            self.capture_snapshot()
            self.last_sample_time = now

    def capture_snapshot(self) -> MetricSnapshot:
        """捕获指标快照"""
        snapshot = MetricSnapshot(
            timestamp=time.time(),
            operation_effectiveness=self.compute_operation_effectiveness(),
            efficiency_gain=self.compute_efficiency_gain(),
            cognitive_stability=self.compute_cognitive_stability(),
            parallel_speedup=self.compute_parallel_speedup(),
            strategy_count=len(self.strategies),
            attempts=self.attempts,
            successes=self.successes,
            failures=self.failures
        )
        self.metrics_snapshots.append(snapshot)
        return snapshot

    def compute_operation_effectiveness(self) -> float:
        """计算操作有效性"""
        if self.attempts == 0:
            return 0.0
        return self.successes / self.attempts

    def compute_efficiency_gain(self) -> float:
        """计算效率提升"""
        if self.expected_human_time == 0:
            return 0.0

        actual_ai_hours = (time.time() - self.start_time) / 3600.0
        if actual_ai_hours == 0:
            return 0.0

        return self.expected_human_time / actual_ai_hours

    def compute_cognitive_stability(self) -> float:
        """计算认知稳定性（重复操作的一致性）"""
        if not self.repeated_operations:
            return 1.0  # 无重复操作，默认稳定

        # 计算所有重复操作的平均一致性
        consistency_scores = []
        for scores in self.repeated_operations.values():
            if len(scores) > 1:
                # 计算标准差，转换为一致性分数
                std = np.std(scores)
                consistency = 1.0 / (1.0 + std)
                consistency_scores.append(consistency)

        if not consistency_scores:
            return 1.0

        return np.mean(consistency_scores)

    def compute_parallel_speedup(self) -> float:
        """计算并行加速比"""
        if self.parallel_operations == 0:
            return 1.0

        # 估计：假设每个并行操作节省50%时间
        estimated_sequential_time = self.parallel_operations * 1.5 + self.sequential_operations
        estimated_parallel_time = self.parallel_operations + self.sequential_operations

        if estimated_parallel_time == 0:
            return 1.0

        return estimated_sequential_time / estimated_parallel_time

    def get_tool_usage_distribution(self) -> Dict[str, int]:
        """获取工具使用分布"""
        distribution: Dict[str, int] = {}
        for call in self.tool_calls:
            tool_name = call.tool_name
            distribution[tool_name] = distribution.get(tool_name, 0) + 1
        return distribution

    def get_failure_modes(self) -> Dict[str, int]:
        """获取失败模式"""
        modes: Dict[str, int] = {
            "exact_match": 0,
            "network": 0,
            "timeout": 0,
            "permission": 0,
            "other": 0
        }

        for call in self.tool_calls:
            if not call.success and call.error:
                error = call.error.lower()
                if "not found" in error or "match" in error:
                    modes["exact_match"] += 1
                elif "network" in error or "connection" in error or "429" in error:
                    modes["network"] += 1
                elif "timeout" in error:
                    modes["timeout"] += 1
                elif "permission" in error or "denied" in error:
                    modes["permission"] += 1
                else:
                    modes["other"] += 1

        return modes

    def get_learning_curve(self) -> List[Tuple[float, float]]:
        """获取学习曲线（时间 vs 有效性）"""
        if not self.metrics_snapshots:
            return []

        return [(s.timestamp - self.start_time, s.operation_effectiveness) for s in self.metrics_snapshots]

    def save_checkpoint(self) -> None:
        """保存检查点"""
        checkpoint = {
            "experiment_id": self.experiment_id,
            "group_id": self.group_id,
            "timestamp": time.time(),
            "attempts": self.attempts,
            "successes": self.successes,
            "failures": self.failures,
            "tool_calls": [c.to_dict() for c in self.tool_calls],
            "decisions": [d.to_dict() for d in self.decisions],
            "strategies": [s.to_dict() for s in self.strategies],
            "metrics_snapshots": [s.to_dict() for s in self.metrics_snapshots]
        }

        checkpoint_file = self.output_dir / f"{self.experiment_id}_{self.group_id}_checkpoint.json"
        checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        with open(checkpoint_file, 'w', encoding='utf-8') as f:
            json.dump(checkpoint, f, indent=2, ensure_ascii=False)

    def load_checkpoint(self) -> bool:
        """加载检查点"""
        checkpoint_file = self.output_dir / f"{self.experiment_id}_{self.group_id}_checkpoint.json"
        if not checkpoint_file.exists():
            return False

        with open(checkpoint_file, 'r', encoding='utf-8') as f:
            checkpoint = json.load(f)

        # 恢复数据
        self.attempts = checkpoint["attempts"]
        self.successes = checkpoint["successes"]
        self.failures = checkpoint["failures"]

        self.tool_calls = [ToolCall(**c) for c in checkpoint["tool_calls"]]
        self.decisions = [Decision(**d) for d in checkpoint["decisions"]]
        self.strategies = [Strategy(**s) for s in checkpoint["strategies"]]
        self.metrics_snapshots = [MetricSnapshot(**m) for m in checkpoint["metrics_snapshots"]]

        return True

    def generate_report(self) -> Dict[str, Any]:
        """生成实验报告"""
        current_snapshot = self.capture_snapshot()

        return {
            "experiment_id": self.experiment_id,
            "group_id": self.group_id,
            "duration_hours": (time.time() - self.start_time) / 3600.0,
            "attempts": self.attempts,
            "successes": self.successes,
            "failures": self.failures,
            "operation_effectiveness": current_snapshot.operation_effectiveness,
            "efficiency_gain": current_snapshot.efficiency_gain,
            "cognitive_stability": current_snapshot.cognitive_stability,
            "parallel_speedup": current_snapshot.parallel_speedup,
            "strategy_count": current_snapshot.strategy_count,
            "tool_usage_distribution": self.get_tool_usage_distribution(),
            "failure_modes": self.get_failure_modes(),
            "learning_curve": self.get_learning_curve(),
            "strategies": [s.to_dict() for s in self.strategies]
        }

    def save_report(self, filepath: Optional[Path] = None) -> None:
        """保存实验报告"""
        report = self.generate_report()

        if filepath is None:
            filepath = self.output_dir / f"{self.experiment_id}_{self.group_id}_report.json"

        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)


class MultiGroupMonitor:
    """多组实验监控器"""

    def __init__(self, experiment_id: str, output_dir: Path):
        self.experiment_id = experiment_id
        self.output_dir = Path(output_dir)
        self.monitors: Dict[str, ExperimentMonitor] = {}

    def add_group(self, group_id: str, monitor: ExperimentMonitor) -> None:
        """添加实验组"""
        self.monitors[group_id] = monitor

    def get_monitor(self, group_id: str) -> Optional[ExperimentMonitor]:
        """获取实验组监控器"""
        return self.monitors.get(group_id)

    def compare_groups(self, metric_name: str) -> Dict[str, float]:
        """对比各组指标"""
        comparison = {}
        for group_id, monitor in self.monitors.items():
            snapshot = monitor.capture_snapshot()
            value = getattr(snapshot, metric_name, 0.0)
            comparison[group_id] = value
        return comparison

    def generate_comparison_report(self) -> Dict[str, Any]:
        """生成对比报告"""
        return {
            "experiment_id": self.experiment_id,
            "groups": list(self.monitors.keys()),
            "operation_effectiveness": self.compare_groups("operation_effectiveness"),
            "efficiency_gain": self.compare_groups("efficiency_gain"),
            "cognitive_stability": self.compare_groups("cognitive_stability"),
            "parallel_speedup": self.compare_groups("parallel_speedup"),
            "strategy_count": self.compare_groups("strategy_count"),
            "success_rates": {
                gid: monitor.compute_operation_effectiveness()
                for gid, monitor in self.monitors.items()
            }
        }

    def save_all_reports(self) -> None:
        """保存所有组的报告"""
        for monitor in self.monitors.values():
            monitor.save_report()

        # 保存对比报告
        comparison = self.generate_comparison_report()
        comparison_file = self.output_dir / f"{self.experiment_id}_comparison_report.json"
        with open(comparison_file, 'w', encoding='utf-8') as f:
            json.dump(comparison, f, indent=2, ensure_ascii=False)
