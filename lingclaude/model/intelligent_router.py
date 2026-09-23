"""Intelligent Model Router

功能：
- 评估任务复杂度
- 自动选择合适的 GLM 模型
- 目标：80% 任务用 GLM-4.7，20% 复杂任务用 GLM-5.1
- 节省 60-200% tokens
"""
from __future__ import annotations

import json
import logging
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any


def _load_policy() -> dict:
    """从策略文件加载关键词（灵元：策略是 data，不是结构）。

    走 PolicyLoader 统一加载（P1-1）：支持 mtime watch 热更，改
    router_keywords.yaml 后下个 turn 生效，进程不重启。
    读失败回退空 dict → 调用方用内置默认（graceful degrade）。
    """
    from lingclaude.core.policy_loader import get as policy_get

    return policy_get("router_keywords")


def _task_keywords(task_key: str) -> list[str]:
    """取某任务类型的关键词列表；策略缺失回退内置默认。"""
    builtin = {
        "code_generation": ["生成", "create", "write", "implement", "开发", "实现", "写", "编写", "编", "函数", "function"],
        "code_analysis": ["代码分析", "code analyze", "code review", "analyze code", "review code", "检查代码", "explain code", "检查", "explain"],
        "code_refactoring": ["重构", "refactor", "重写", "rewrite"],
        "debugging": ["调试", "debug", "错误", "error", "bug", "fix", "修复"],
        "documentation": ["文档", "document", "documentation", "注释", "comment", "说明", "readme"],
        "search": ["搜索", "search", "查找", "find", "grep"],
        "analysis": ["分析", "analysis", "研究", "research"],
        "optimization": ["优化", "optimize", "提升", "improve", "性能"],
        "testing": ["测试", "test", "验证", "verify", "assert"],
    }
    try:
        kws = _load_policy().get("task_keywords", {}).get(task_key)
        if isinstance(kws, list) and kws:
            return kws
    except Exception:  # noqa: BLE001
        pass
    return builtin.get(task_key, [])


def _complexity_keywords(level: str) -> list[str]:
    """取复杂度关键词；策略缺失回退内置默认。"""
    builtin = {
        "complex": ["架构", "architecture", "设计模式", "design pattern", "分布式", "distributed", "并发", "concurrent", "性能优化", "performance optimization", "算法", "algorithm", "数据结构", "data structure"],
        "medium": ["函数", "function", "类", "class", "模块", "module", "配置", "config", "调试", "debug"],
    }
    try:
        kws = _load_policy().get("complexity_keywords", {}).get(level)
        if isinstance(kws, list) and kws:
            return kws
    except Exception:  # noqa: BLE001
        pass
    return builtin.get(level, [])


# R9 (2026-09-16): 长任务首 turn 拆解信号。
# 动机：R8 提示只在会话烧了 N 次工具调用后才注入(事后补救), 首 turn 明知是大任务
# 却先硬扛。此判定让"第 0 次工具调用就知道该拆"。
# 动作词表复用 router_keywords.task_keywords(与任务分类同源, 策略热更生效),
# 不新增第二份词表——改动词只改 YAML。
_R9_MIN_LEN = 200
_R9_MIN_VERBS = 2
_BUILTIN_TASK_KEYS = (
    "code_generation", "code_analysis", "code_refactoring", "debugging",
    "documentation", "search", "analysis", "optimization", "testing",
)


def _action_verbs() -> list[str]:
    """聚合 task_keywords 全部关键词为动作词表；策略缺失回退内置默认。"""
    verbs: list[str] = []
    try:
        mapping = _load_policy().get("task_keywords", {})
        if isinstance(mapping, dict) and mapping:
            for lst in mapping.values():
                if isinstance(lst, list):
                    verbs.extend(str(v) for v in lst)
    except Exception:  # noqa: BLE001
        pass
    if not verbs:
        for key in _BUILTIN_TASK_KEYS:
            verbs.extend(_task_keywords(key))
    return sorted(set(verbs))


def is_decomposition_candidate(query: str) -> bool:
    """R9: 判断 query 是否为"长任务拆解候选"（首 turn 提示用, 不强制）。

    判据: 长度 > 200 字符 且 命中动作词 >= 2（与外部建议对齐, 与
    _evaluate_complexity 的长度分档同源）。动作词表来自
    router_keywords.task_keywords（策略热更），含少量名词性关键词,
    判定偏宽——误报代价仅是多注入一条建议提示, 漏报则浪费整段 token。

    Returns:
        True = 建议上层注入"先拆解"提示。
    """
    if not query or len(query) <= _R9_MIN_LEN:
        return False
    q = query.lower()
    hits = sum(1 for v in _action_verbs() if v and v.lower() in q)
    return hits >= _R9_MIN_VERBS


class TaskType(Enum):
    """任务类型"""
    CODE_GENERATION = auto()
    CODE_ANALYSIS = auto()
    CODE_REFACTORING = auto()
    DEBUGGING = auto()
    DOCUMENTATION = auto()
    SEARCH = auto()
    ANALYSIS = auto()
    OPTIMIZATION = auto()
    TESTING = auto()
    OTHER = auto()

    @classmethod
    def from_query(cls, query: str) -> "TaskType":
        """从查询内容推断任务类型

        Args:
            query: 用户查询内容

        Returns:
            任务类型
        """
        query_lower = query.lower()

        # 代码生成（关键词来自策略文件 router_keywords.yaml）
        if any(kw in query_lower for kw in _task_keywords("code_generation")):
            return cls.CODE_GENERATION

        # 代码分析
        if any(kw in query_lower for kw in _task_keywords("code_analysis")):
            return cls.CODE_ANALYSIS

        # 代码重构
        if any(kw in query_lower for kw in _task_keywords("code_refactoring")):
            return cls.CODE_REFACTORING

        # 调试
        if any(kw in query_lower for kw in _task_keywords("debugging")):
            return cls.DEBUGGING

        # 文档
        if any(kw in query_lower for kw in _task_keywords("documentation")):
            return cls.DOCUMENTATION

        # 搜索
        if any(kw in query_lower for kw in _task_keywords("search")):
            return cls.SEARCH

        # 分析
        if any(kw in query_lower for kw in _task_keywords("analysis")):
            return cls.ANALYSIS

        # 优化
        if any(kw in query_lower for kw in _task_keywords("optimization")):
            return cls.OPTIMIZATION

        # 测试
        if any(kw in query_lower for kw in _task_keywords("testing")):
            return cls.TESTING

        return cls.OTHER


class TaskComplexity(str, Enum):
    """任务复杂度"""
    SIMPLE = "简单"
    MEDIUM = "中等"
    COMPLEX = "复杂"

    def get_weight(self) -> float:
        """获取权重

        Returns:
            权重值
        """
        return {
            TaskComplexity.SIMPLE: 1.0,
            TaskComplexity.MEDIUM: 1.5,
            TaskComplexity.COMPLEX: 2.0,
        }[self]


class GLMModel(str, Enum):
    """GLM 模型"""
    GLM_4_7 = "GLM-4.7"
    GLM_5_1 = "GLM-5.1"
    GLM_5 = "GLM-5"
    # 2026-09-11 (claudecode 审计): 生产模型已是 glm-5.3-flash,
    # 枚举此前缺位导致 token_monitor/评分 SQL 只能靠 TARGET_MODEL 字符串兜底。
    GLM_5_3_FLASH = "glm-5.3-flash"

    def get_cost_multiplier(self) -> float:
        """获取成本倍数

        Returns:
            成本倍数
        """
        return {
            GLMModel.GLM_4_7: 1.0,
            GLMModel.GLM_5_1: 2.0,  # 非高峰期可能 1 倍
            GLMModel.GLM_5: 3.0,
            GLMModel.GLM_5_3_FLASH: 1.5,  # flash 档: 介于 4.7 与 5.1 之间
        }[self]


@dataclass(frozen=True)
class RoutingDecision:
    """路由决策"""
    model: GLMModel
    complexity: TaskComplexity
    task_type: TaskType
    reason: str
    confidence: float  # 0.0 - 1.0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass(frozen=True)
class RoutingStats:
    """路由统计"""
    total_routed: int = 0
    glm_4_7_count: int = 0
    glm_5_1_count: int = 0
    glm_5_count: int = 0
    simple_count: int = 0
    medium_count: int = 0
    complex_count: int = 0

    def get_glm_4_7_ratio(self) -> float:
        """获取 GLM-4.7 使用率

        Returns:
            使用率
        """
        return self.glm_4_7_count / self.total_routed if self.total_routed > 0 else 0.0


class IntelligentRouter:
    """智能路由器"""

    def __init__(self, stats_path: str | Path | None = None):
        """初始化路由器

        Args:
            stats_path: 统计文件路径，默认为 ~/.lingclaude/routing_stats.json
        """
        if stats_path is None:
            stats_path = Path.home() / ".lingclaude" / "routing_stats.json"

        self.stats_path = Path(stats_path)
        self.stats_path.parent.mkdir(parents=True, exist_ok=True)

        # 加载统计
        self._stats = self._load_stats()

    def _load_stats(self) -> RoutingStats:
        """加载统计

        Returns:
            路由统计
        """
        if not self.stats_path.exists():
            return RoutingStats()

        try:
            data = json.loads(self.stats_path.read_text(encoding="utf-8"))
            return RoutingStats(**data)
        except (json.JSONDecodeError, TypeError):
            return RoutingStats()

    def _save_stats(self) -> None:
        """保存统计（写入失败时降级为警告，统计为非关键数据不应阻断路由）"""
        data = {
            "total_routed": self._stats.total_routed,
            "glm_4_7_count": self._stats.glm_4_7_count,
            "glm_5_1_count": self._stats.glm_5_1_count,
            "glm_5_count": self._stats.glm_5_count,
            "simple_count": self._stats.simple_count,
            "medium_count": self._stats.medium_count,
            "complex_count": self._stats.complex_count,
        }
        try:
            from lingclaude.core.state_store import _atomic_write_json
            _atomic_write_json(self.stats_path, data)
        except OSError as exc:
            logging.getLogger(__name__).warning("路由统计写入失败(忽略): %s -> %s", self.stats_path, exc)

    def _evaluate_complexity(self, query: str, context: dict[str, Any] | None = None) -> TaskComplexity:
        """评估任务复杂度

        Args:
            query: 用户查询
            context: 上下文信息

        Returns:
            任务复杂度
        """
        context = context or {}

        # 基础分数
        score = 0

        # 查询长度
        query_len = len(query)
        if query_len > 1000:
            score += 3
        elif query_len > 500:
            score += 2
        elif query_len > 200:
            score += 1

        # 关键词检测（关键词来自策略文件 router_keywords.yaml）
        complex_keywords = _complexity_keywords("complex")
        medium_keywords = _complexity_keywords("medium")

        for kw in complex_keywords:
            if kw in query.lower():
                score += 3

        for kw in medium_keywords:
            if kw in query.lower():
                score += 1

        # 文件数量
        file_count = len(context.get("files", []))
        if file_count > 10:
            score += 3
        elif file_count > 5:
            score += 2
        elif file_count > 1:
            score += 1

        # 上下文大小
        context_size = sum(
            len(content) for content in context.get("file_contents", {}).values()
        )
        if context_size > 50000:
            score += 3
        elif context_size > 20000:
            score += 2
        elif context_size > 5000:
            score += 1

        # 判断复杂度
        if score >= 8:
            return TaskComplexity.COMPLEX
        elif score >= 4:
            return TaskComplexity.MEDIUM
        else:
            return TaskComplexity.SIMPLE

    def _choose_model(
        self,
        complexity: TaskComplexity,
        task_type: TaskType,
        context: dict[str, Any] | None = None,
    ) -> tuple[GLMModel, str, float]:
        """选择模型

        Args:
            complexity: 任务复杂度
            task_type: 任务类型
            context: 上下文信息

        Returns:
            (模型, 原因, 置信度)
        """
        context = context or {}

        # 检查 GLM-4.7 使用率
        current_ratio = self._stats.get_glm_4_7_ratio()

        # 策略：80% 用 GLM-4.7，20% 用 GLM-5.1
        if current_ratio < 0.8:
            # 如果 GLM-4.7 使用率不够，尽量用 GLM-4.7
            if complexity in (TaskComplexity.SIMPLE, TaskComplexity.MEDIUM):
                return (
                    GLMModel.GLM_4_7,
                    f"GLM-4.7 使用率 {current_ratio * 100:.1f}% < 80%，优先使用 GLM-4.7",
                    0.9,
                )
            elif complexity == TaskComplexity.COMPLEX:
                # 复杂任务考虑 GLM-5.1
                return (
                    GLMModel.GLM_5_1,
                    "复杂任务，使用 GLM-5.1 保证质量",
                    0.8,
                )

        # 如果 GLM-4.7 使用率已经很高，按复杂度选择
        if complexity == TaskComplexity.SIMPLE:
            return (
                GLMModel.GLM_4_7,
                "简单任务，GLM-4.7 足够",
                0.95,
            )
        elif complexity == TaskComplexity.MEDIUM:
            # 80% 概率用 GLM-4.7，20% 概率用 GLM-5.1
            if random.random() < 0.8:
                return (
                    GLMModel.GLM_4_7,
                    "中等任务，GLM-4.7 可以胜任",
                    0.8,
                )
            else:
                return (
                    GLMModel.GLM_5_1,
                    "中等任务，尝试 GLM-5.1 提升质量",
                    0.7,
                )
        else:  # COMPLEX
            return (
                GLMModel.GLM_5_1,
                "复杂任务，使用 GLM-5.1 保证质量",
                0.9,
            )

    def route(
        self,
        query: str,
        context: dict[str, Any] | None = None,
    ) -> RoutingDecision:
        """路由查询到合适的模型

        Args:
            query: 用户查询
            context: 上下文信息

        Returns:
            路由决策
        """
        context = context or {}

        # 识别任务类型
        task_type = TaskType.from_query(query)

        # 评估复杂度
        complexity = self._evaluate_complexity(query, context)

        # 选择模型
        model, reason, confidence = self._choose_model(complexity, task_type, context)

        # 创建决策
        decision = RoutingDecision(
            model=model,
            complexity=complexity,
            task_type=task_type,
            reason=reason,
            confidence=confidence,
        )

        # 更新统计
        self._update_stats(decision)

        return decision

    def _update_stats(self, decision: RoutingDecision) -> None:
        """更新统计

        Args:
            decision: 路由决策
        """
        self._stats = RoutingStats(
            total_routed=self._stats.total_routed + 1,
            glm_4_7_count=self._stats.glm_4_7_count + (1 if decision.model == GLMModel.GLM_4_7 else 0),
            glm_5_1_count=self._stats.glm_5_1_count + (1 if decision.model == GLMModel.GLM_5_1 else 0),
            glm_5_count=self._stats.glm_5_count + (1 if decision.model == GLMModel.GLM_5 else 0),
            simple_count=self._stats.simple_count + (1 if decision.complexity == TaskComplexity.SIMPLE else 0),
            medium_count=self._stats.medium_count + (1 if decision.complexity == TaskComplexity.MEDIUM else 0),
            complex_count=self._stats.complex_count + (1 if decision.complexity == TaskComplexity.COMPLEX else 0),
        )
        self._save_stats()

    def get_stats(self) -> RoutingStats:
        """获取统计

        Returns:
            路由统计
        """
        return self._stats

    def reset_stats(self) -> None:
        """重置统计"""
        self._stats = RoutingStats()
        self._save_stats()


def main():
    """主函数：测试路由器"""

    print("=" * 80)
    print("🤖 智能模型路由器测试")
    print("=" * 80)

    # 创建路由器
    router = IntelligentRouter()

    # 测试查询
    test_queries = [
        "写一个 hello world 函数",
        "分析这个项目的架构",
        "实现一个分布式缓存系统",
        "优化数据库查询性能",
        "搜索所有 Python 文件",
        "调试这个错误：AttributeError",
        "重构这个类",
        "生成测试代码",
    ]

    print("\n📋 测试查询路由：")
    print("-" * 80)

    for query in test_queries:
        decision = router.route(query)
        print(f"\n查询: {query}")
        print(f"  模型: {decision.model}")
        print(f"  复杂度: {decision.complexity}")
        print(f"  任务类型: {decision.task_type.name}")
        print(f"  原因: {decision.reason}")
        print(f"  置信度: {decision.confidence:.2f}")

    # 显示统计
    print("\n" + "=" * 80)
    print("📊 路由统计")
    print("=" * 80)
    stats = router.get_stats()
    print(f"  总路由次数: {stats.total_routed}")
    print(f"  GLM-4.7 次数: {stats.glm_4_7_count}")
    print(f"  GLM-5.1 次数: {stats.glm_5_1_count}")
    print(f"  GLM-5 次数: {stats.glm_5_count}")
    print(f"  GLM-4.7 使用率: {stats.get_glm_4_7_ratio() * 100:.1f}%")
    print(f"  简单任务: {stats.simple_count}")
    print(f"  中等任务: {stats.medium_count}")
    print(f"  复杂任务: {stats.complex_count}")

    print("\n" + "=" * 80)
    print("✅ 路由器测试完成！")
    print("=" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(main())
