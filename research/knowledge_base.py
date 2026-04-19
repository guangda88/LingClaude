"""
共享知识库管理工具

功能：
- 策略共享和验证
- 策略评分和过滤
- 协作日志记录
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Strategy:
    """策略定义"""
    strategy_id: str
    author: str  # "lingclaude" or "lingyan"
    name: str
    description: str
    category: str  # "workflow", "optimization", "debugging"
    success_rate: float
    usage_count: int
    avg_duration_ms: float
    efficiency_gain: float
    stability_score: float
    shared: bool = False
    shared_at: Optional[float] = None
    verified_by: Optional[str] = None
    verified_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Strategy":
        return cls(**data)


class KnowledgeBase:
    """共享知识库"""

    def __init__(self, base_path: Path):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

        # 路径
        self.strategies_path = self.base_path / "strategies"
        self.patterns_path = self.base_path / "patterns"
        self.metrics_path = self.base_path / "metrics"
        self.experiments_path = self.base_path / "experiments"
        self.logs_path = self.experiments_path / "logs"

        # 创建目录
        for path in [self.strategies_path, self.patterns_path,
                     self.metrics_path, self.experiments_path, self.logs_path]:
            path.mkdir(exist_ok=True)

        # 策略文件
        self.lingclaude_strategies_file = self.strategies_path / "lingclaude_strategies.json"
        self.lingyan_strategies_file = self.strategies_path / "lingyan_strategies.json"
        self.shared_strategies_file = self.strategies_path / "shared_strategies.json"
        self.strategy_shares_log = self.logs_path / "strategy_shares.json"
        self.conflict_resolves_log = self.logs_path / "conflict_resolves.json"

    def add_strategy(self, author: str, strategy: Strategy) -> None:
        """添加策略"""
        # 选择文件
        if author == "lingclaude":
            file_path = self.lingclaude_strategies_file
        elif author == "lingyan":
            file_path = self.lingyan_strategies_file
        else:
            raise ValueError(f"Unknown author: {author}")

        # 加载现有策略
        strategies = self._load_strategies(file_path)

        # 添加新策略
        strategies[strategy.strategy_id] = strategy.to_dict()

        # 保存
        self._save_strategies(file_path, strategies)

        # 检查是否可以共享
        if self._should_share(strategy):
            self.share_strategy(strategy)

    def _should_share(self, strategy: Strategy) -> bool:
        """判断是否应该共享"""
        # 评分过滤
        if strategy.success_rate < 0.9:
            return False

        if strategy.usage_count < 5:
            return False

        # 效率提升和稳定性（推荐）
        if strategy.efficiency_gain < 1.5:
            return False

        if strategy.stability_score < 0.8:
            return False

        return True

    def share_strategy(self, strategy: Strategy) -> bool:
        """分享策略"""
        # 加载共享策略
        shared = self._load_strategies(self.shared_strategies_file)

        # 检查是否已存在
        if strategy.strategy_id in shared:
            return False

        # 标记为已共享
        strategy.shared = True
        strategy.shared_at = datetime.now().timestamp()

        # 添加到共享库
        shared[strategy.strategy_id] = strategy.to_dict()
        self._save_strategies(self.shared_strategies_file, shared)

        # 记录日志
        self._log_strategy_share(strategy)

        return True

    def _log_strategy_share(self, strategy: Strategy) -> None:
        """记录策略共享日志"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "strategy_id": strategy.strategy_id,
            "author": strategy.author,
            "name": strategy.name,
            "success_rate": strategy.success_rate,
            "efficiency_gain": strategy.efficiency_gain
        }

        logs = self._load_logs(self.strategy_shares_log)
        logs.append(log_entry)
        self._save_logs(self.strategy_shares_log, logs)

    def verify_strategy(self, verifier: str, strategy_id: str, approved: bool) -> None:
        """验证策略（人工审核）"""
        # 加载共享策略
        shared = self._load_strategies(self.shared_strategies_file)

        if strategy_id not in shared:
            raise ValueError(f"Strategy not found: {strategy_id}")

        # 更新验证信息
        shared[strategy_id]["verified_by"] = verifier
        shared[strategy_id]["verified_at"] = datetime.now().timestamp()

        # 如果未通过，移除
        if not approved:
            del shared[strategy_id]

        # 保存
        self._save_strategies(self.shared_strategies_file, shared)

    def load_shared_strategies(self) -> List[Strategy]:
        """加载共享策略"""
        shared = self._load_strategies(self.shared_strategies_file)
        return [Strategy.from_dict(data) for data in shared.values()]

    def load_author_strategies(self, author: str) -> List[Strategy]:
        """加载特定作者的策略"""
        if author == "lingclaude":
            file_path = self.lingclaude_strategies_file
        elif author == "lingyan":
            file_path = self.lingyan_strategies_file
        else:
            raise ValueError(f"Unknown author: {author}")

        strategies = self._load_strategies(file_path)
        return [Strategy.from_dict(data) for data in strategies.values()]

    def _load_strategies(self, file_path: Path) -> Dict[str, Any]:
        """加载策略文件"""
        if not file_path.exists():
            return {}

        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _save_strategies(self, file_path: Path, strategies: Dict[str, Any]) -> None:
        """保存策略文件"""
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(strategies, f, indent=2, ensure_ascii=False)

    def _load_logs(self, file_path: Path) -> List[Dict[str, Any]]:
        """加载日志文件"""
        if not file_path.exists():
            return []

        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _save_logs(self, file_path: Path, logs: List[Dict[str, Any]]) -> None:
        """保存日志文件"""
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(logs, f, indent=2, ensure_ascii=False)

    def get_statistics(self) -> Dict[str, Any]:
        """获取知识库统计信息"""
        lingclaude_strategies = self._load_strategies(self.lingclaude_strategies_file)
        lingyan_strategies = self._load_strategies(self.lingyan_strategies_file)
        shared_strategies = self._load_strategies(self.shared_strategies_file)

        # 计算平均成功率
        def avg_success_rate(strategies_dict):
            if not strategies_dict:
                return 0.0
            rates = [s.get("success_rate", 0) for s in strategies_dict.values()]
            return sum(rates) / len(rates)

        # 计算共享率
        lingclaude_count = len(lingclaude_strategies)
        lingyan_count = len(lingyan_strategies)
        shared_count = len(shared_strategies)
        total_count = lingclaude_count + lingyan_count
        share_rate = shared_count / total_count if total_count > 0 else 0.0

        return {
            "lingclaude_strategies": lingclaude_count,
            "lingyan_strategies": lingyan_count,
            "shared_strategies": shared_count,
            "share_rate": share_rate,
            "lingclaude_avg_success": avg_success_rate(lingclaude_strategies),
            "lingyan_avg_success": avg_success_rate(lingyan_strategies),
            "shared_avg_success": avg_success_rate(shared_strategies)
        }


def create_demo_strategies(kb: KnowledgeBase) -> None:
    """创建演示策略"""
    # 灵克的策略
    lingclaude_strategies = [
        Strategy(
            strategy_id="STR-LC-001",
            author="lingclaude",
            name="view_edit_test_workflow",
            description="标准workflow：读取文件、编辑、测试",
            category="workflow",
            success_rate=0.92,
            usage_count=120,
            avg_duration_ms=150.0,
            efficiency_gain=2.5,
            stability_score=0.95
        ),
        Strategy(
            strategy_id="STR-LC-002",
            author="lingclaude",
            name="parallel_view_files",
            description="并行读取多个文件以提高效率",
            category="optimization",
            success_rate=0.88,
            usage_count=45,
            avg_duration_ms=80.0,
            efficiency_gain=1.8,
            stability_score=0.90
        ),
        Strategy(
            strategy_id="STR-LC-003",
            author="lingclaude",
            name="diagnose_retry",
            description="系统化诊断失败并重试",
            category="debugging",
            success_rate=0.95,
            usage_count=30,
            avg_duration_ms=200.0,
            efficiency_gain=2.0,
            stability_score=0.92
        )
    ]

    # 灵妍的策略
    lingyan_strategies = [
        Strategy(
            strategy_id="STR-LY-001",
            author="lingyan",
            name="analyze_tool_usage",
            description="分析工具使用模式以识别优化点",
            category="analysis",
            success_rate=0.91,
            usage_count=85,
            avg_duration_ms=180.0,
            efficiency_gain=2.2,
            stability_score=0.93
        ),
        Strategy(
            strategy_id="STR-LY-002",
            author="lingyan",
            name="extract_failure_patterns",
            description="提取和分析失败模式",
            category="debugging",
            success_rate=0.94,
            usage_count=60,
            avg_duration_ms=150.0,
            efficiency_gain=2.1,
            stability_score=0.91
        )
    ]

    # 添加到知识库
    for strategy in lingclaude_strategies:
        kb.add_strategy("lingclaude", strategy)

    for strategy in lingyan_strategies:
        kb.add_strategy("lingyan", strategy)


def main():
    """演示使用"""
    # 创建知识库
    kb = KnowledgeBase(Path("knowledge"))

    # 创建演示策略
    create_demo_strategies(kb)

    # 获取统计信息
    stats = kb.get_statistics()
    print("知识库统计:")
    print(f"  灵克的策略: {stats['lingclaude_strategies']}")
    print(f"  灵妍的策略: {stats['lingyan_strategies']}")
    print(f"  共享策略: {stats['shared_strategies']}")
    print(f"  共享率: {stats['share_rate']:.2%}")
    print(f"  灵克平均成功率: {stats['lingclaude_avg_success']:.2%}")
    print(f"  灵妍平均成功率: {stats['lingyan_avg_success']:.2%}")
    print(f"  共享策略平均成功率: {stats['shared_avg_success']:.2%}")

    # 加载共享策略
    shared = kb.load_shared_strategies()
    print(f"\n共享策略列表:")
    for strategy in shared:
        print(f"  - {strategy.name} (成功率: {strategy.success_rate:.2%}, 效率: {strategy.efficiency_gain}x)")


if __name__ == "__main__":
    main()
