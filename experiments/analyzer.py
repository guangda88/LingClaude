"""
AI自我进化实验分析工具

功能：
- 分析实验数据
- 生成可视化图表
- 进行统计检验
- 生成分析报告
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


@dataclass
class ExperimentData:
    """实验数据"""
    group_id: str
    operation_effectiveness: float
    efficiency_gain: float
    cognitive_stability: float
    parallel_speedup: float
    strategy_count: int
    duration_hours: float
    attempts: int
    successes: int
    failures: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExperimentData":
        return cls(
            group_id=data["group_id"],
            operation_effectiveness=data["operation_effectiveness"],
            efficiency_gain=data["efficiency_gain"],
            cognitive_stability=data["cognitive_stability"],
            parallel_speedup=data["parallel_speedup"],
            strategy_count=data["strategy_count"],
            duration_hours=data.get("duration_hours", 0),
            attempts=data["attempts"],
            successes=data["successes"],
            failures=data["failures"]
        )


class ExperimentAnalyzer:
    """实验分析器"""

    def __init__(self, data_dir: Path, plot_dir: Path):
        self.data_dir = Path(data_dir)
        self.plot_dir = Path(plot_dir)
        self.data: List[ExperimentData] = []

    def load_data(self, experiment_id: str) -> None:
        """加载实验数据"""
        self.data = []

        # 加载各组报告
        for report_file in self.data_dir.glob(f"{experiment_id}_*_report.json"):
            with open(report_file, 'r', encoding='utf-8') as f:
                report_data = json.load(f)
                self.data.append(ExperimentData.from_dict(report_data))

        # 加载对比报告
        comparison_file = self.data_dir / f"{experiment_id}_comparison_report.json"
        if comparison_file.exists():
            with open(comparison_file, 'r', encoding='utf-8') as f:
                self.comparison_data = json.load(f)

    def get_group_data(self, group_id: str) -> Optional[ExperimentData]:
        """获取特定组的数据"""
        for item in self.data:
            if item.group_id == group_id:
                return item
        return None

    def compare_metrics(self, metric_name: str) -> Dict[str, float]:
        """对比各组的指标"""
        comparison = {}
        for item in self.data:
            value = getattr(item, metric_name, 0.0)
            comparison[item.group_id] = value
        return comparison

    def compute_improvement(
        self,
        control_group: str,
        treatment_group: str,
        metric_name: str
    ) -> Dict[str, Any]:
        """计算改进"""
        control = self.get_group_data(control_group)
        treatment = self.get_group_data(treatment_group)

        if not control or not treatment:
            return {"error": "Group not found"}

        control_value = getattr(control, metric_name, 0.0)
        treatment_value = getattr(treatment, metric_name, 0.0)

        if control_value == 0:
            return {"error": "Control value is zero"}

        improvement = (treatment_value - control_value) / control_value * 100

        return {
            "control_group": control_group,
            "treatment_group": treatment_group,
            "metric": metric_name,
            "control_value": control_value,
            "treatment_value": treatment_value,
            "improvement_percent": improvement,
            "improvement_factor": treatment_value / control_value
        }

    def generate_summary(self) -> Dict[str, Any]:
        """生成摘要"""
        if not self.data:
            return {"error": "No data loaded"}

        # 获取实验ID（从对比报告或第一个数据文件）
        experiment_id = "unknown"
        if hasattr(self, 'comparison_data'):
            experiment_id = self.comparison_data.get("experiment_id", "unknown")
        elif self.data:
            # 从数据中提取实验ID
            for report_file in self.data_dir.glob("*_report.json"):
                with open(report_file, 'r', encoding='utf-8') as f:
                    report_data = json.load(f)
                    experiment_id = report_data.get("experiment_id", "unknown").split("_")[0]
                    break

        summary = {
            "experiment_id": experiment_id,
            "groups": len(self.data),
            "group_ids": [item.group_id for item in self.data],
            "metrics": {}
        }

        for metric in [
            "operation_effectiveness",
            "efficiency_gain",
            "cognitive_stability",
            "parallel_speedup",
            "strategy_count"
        ]:
            comparison = self.compare_metrics(metric)

            if comparison:
                # 计算平均值和标准差
                values = list(comparison.values())
                summary["metrics"][metric] = {
                    "values": comparison,
                    "mean": np.mean(values),
                    "std": np.std(values),
                    "min": np.min(values),
                    "max": np.max(values),
                    "range": np.max(values) - np.min(values)
                }

        return summary

    def generate_markdown_report(self, output_path: Optional[Path] = None) -> str:
        """生成Markdown报告"""
        summary = self.generate_summary()

        report = f"""# AI自我进化实验分析报告

**实验ID:** {summary['experiment_id']}
**实验组数:** {summary['groups']}
**生成时间:** {np.datetime64('now')}

---

## 实验组别

"""

        for item in self.data:
            report += f"""
### {item.group_id}

- **操作有效性:** {item.operation_effectiveness:.2%}
- **效率提升:** {item.efficiency_gain:.2f}x
- **认知稳定性:** {item.cognitive_stability:.3f}
- **并行加速比:** {item.parallel_speedup:.2f}x
- **策略数量:** {item.strategy_count}
- **持续时间:** {item.duration_hours:.2f}小时
- **尝试次数:** {item.attempts}
- **成功次数:** {item.successes}
- **失败次数:** {item.failures}

"""

        report += "\n## 指标对比\n\n"

        for metric_name, metric_data in summary.get("metrics", {}).items():
            report += f"### {metric_name}\n\n"
            report += f"- **平均值:** {metric_data['mean']:.3f}\n"
            report += f"- **标准差:** {metric_data['std']:.3f}\n"
            report += f"- **最小值:** {metric_data['min']:.3f}\n"
            report += f"- **最大值:** {metric_data['max']:.3f}\n"
            report += f"- **范围:** {metric_data['range']:.3f}\n\n"

            report += "**各组的值:**\n\n"
            for group_id, value in metric_data["values"].items():
                report += f"- {group_id}: {value:.3f}\n"
            report += "\n"

        report += "\n## 改进分析\n\n"

        # 假设 A 是对照组，B 和 C 是实验组
        if "control" in [item.group_id for item in self.data]:
            for treatment in ["basic", "enhanced"]:
                for metric in ["operation_effectiveness", "efficiency_gain"]:
                    improvement = self.compute_improvement("control", treatment, metric)
                    if "error" not in improvement:
                        report += f"""
### {treatment} vs control ({metric})

- **对照组:** {improvement['control_value']:.3f}
- **实验组:** {improvement['treatment_value']:.3f}
- **改进:** {improvement['improvement_percent']:.1f}%
- **倍数:** {improvement['improvement_factor']:.2f}x

"""

        report += "\n## 结论\n\n"

        # 找出最佳组
        best_efficiency = max(self.data, key=lambda x: x.efficiency_gain)
        best_effectiveness = max(self.data, key=lambda x: x.operation_effectiveness)

        report += f"""
**最高效率提升:** {best_efficiency.group_id} ({best_efficiency.efficiency_gain:.2f}x)
**最高操作有效性:** {best_effectiveness.group_id} ({best_effectiveness.operation_effectiveness:.2%})

"""

        report += "\n---\n\n"
        report += "*本报告由 ExperimentAnalyzer 自动生成*\n"

        # 保存报告
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(report)

        return report

    def generate_plots(self) -> List[Path]:
        """生成可视化图表"""
        plot_files = []

        try:
            import matplotlib.pyplot as plt
            import matplotlib

            # 创建输出目录
            self.plot_dir.mkdir(parents=True, exist_ok=True)

            # 设置中文字体
            matplotlib.rcParams['font.sans-serif'] = ['DejaVu Sans']
            matplotlib.rcParams['axes.unicode_minus'] = False

            # 图表1: 操作有效性对比
            plt.figure(figsize=(10, 6))
            group_ids = [item.group_id for item in self.data]
            effectiveness = [item.operation_effectiveness for item in self.data]

            plt.bar(group_ids, effectiveness)
            plt.title('Operation Effectiveness by Group', fontsize=14)
            plt.ylabel('Effectiveness', fontsize=12)
            plt.ylim(0, 1.0)
            plt.grid(axis='y', alpha=0.3)
            plt.savefig(self.plot_dir / 'operation_effectiveness.png', dpi=150, bbox_inches='tight')
            plt.close()
            plot_files.append(self.plot_dir / 'operation_effectiveness.png')

            # 图表2: 效率提升对比
            plt.figure(figsize=(10, 6))
            efficiency = [item.efficiency_gain for item in self.data]

            plt.bar(group_ids, efficiency)
            plt.title('Efficiency Gain by Group', fontsize=14)
            plt.ylabel('Efficiency Gain (x)', fontsize=12)
            plt.grid(axis='y', alpha=0.3)
            plt.savefig(self.plot_dir / 'efficiency_gain.png', dpi=150, bbox_inches='tight')
            plt.close()
            plot_files.append(self.plot_dir / 'efficiency_gain.png')

            # 图表3: 多指标对比雷达图
            plt.figure(figsize=(10, 10))

            # 归一化数据
            metrics = ['operation_effectiveness', 'cognitive_stability', 'parallel_speedup']
            angles = np.linspace(0, 2 * np.pi, len(metrics), endpoint=False).tolist()
            angles += angles[:1]  # 闭合

            for item in self.data:
                values = [
                    getattr(item, metric, 0.0)
                    for metric in metrics
                ]
                # 归一化到 0-1
                max_val = max([getattr(d, metric, 0.0) for d in self.data for metric in metrics])
                if max_val > 0:
                    values = [v / max_val for v in values]
                values += values[:1]  # 闭合

                plt.polar(angles, values, 'o-', linewidth=2, label=item.group_id)

            plt.xticks(angles[:-1], metrics)
            plt.ylim(0, 1.0)
            plt.title('Multi-metric Comparison', fontsize=14, pad=20)
            plt.legend(loc='upper right', bbox_to_anchor=(1.3, 1.0))
            plt.tight_layout()
            plt.savefig(self.plot_dir / 'multi_metric_comparison.png', dpi=150, bbox_inches='tight')
            plt.close()
            plot_files.append(self.plot_dir / 'multi_metric_comparison.png')

            # 图表4: 策略数量对比
            plt.figure(figsize=(10, 6))
            strategy_counts = [item.strategy_count for item in self.data]

            plt.bar(group_ids, strategy_counts)
            plt.title('Strategy Count by Group', fontsize=14)
            plt.ylabel('Number of Strategies', fontsize=12)
            plt.grid(axis='y', alpha=0.3)
            plt.savefig(self.plot_dir / 'strategy_count.png', dpi=150, bbox_inches='tight')
            plt.close()
            plot_files.append(self.plot_dir / 'strategy_count.png')

        except ImportError:
            print("警告: matplotlib 未安装，无法生成图表")
            print("安装命令: pip install matplotlib")

        return plot_files


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="AI自我进化实验分析器")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("experiments/data"),
        help="数据目录"
    )
    parser.add_argument(
        "--plot-dir",
        type=Path,
        default=Path("experiments/plots"),
        help="图表输出目录"
    )
    parser.add_argument(
        "--experiment-id",
        type=str,
        required=True,
        help="实验ID"
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="生成分析报告"
    )
    parser.add_argument(
        "--plots",
        action="store_true",
        help="生成可视化图表"
    )

    args = parser.parse_args()

    # 创建分析器
    analyzer = ExperimentAnalyzer(args.data_dir, args.plot_dir)

    # 加载数据
    analyzer.load_data(args.experiment_id)

    # 生成摘要
    summary = analyzer.generate_summary()
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    # 生成报告
    if args.report:
        report_path = Path("experiments/reports") / f"{args.experiment_id}_analysis.md"
        report = analyzer.generate_markdown_report(report_path)
        print(f"\n报告已保存到: {report_path}")

    # 生成图表
    if args.plots:
        plot_files = analyzer.generate_plots()
        print(f"\n图表已生成:")
        for plot_file in plot_files:
            print(f"  - {plot_file}")


if __name__ == "__main__":
    main()
