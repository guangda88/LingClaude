from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class FeedbackSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FeedbackCategory(str, Enum):
    SECURITY = "security"
    PERFORMANCE = "performance"
    CODE_QUALITY = "code_quality"
    MAINTAINABILITY = "maintainability"
    BEST_PRACTICE = "best_practice"
    BUG_RISK = "bug_risk"
    ARCHITECTURE = "architecture"
    # 2026-09-28 断点③修复：失败聚类归因规则的类别。_to_rule 引用 TOOL_ERROR
    # 但枚举缺失 → analyze 每次构造规则抛 AttributeError 被 fail-soft 静默吞掉，
    # 断点③从未真正入库（knowledge.db 无 failure_cluster_* 规则）。补齐使归因闭环成立。
    TOOL_ERROR = "tool_error"
    # 2026-09-22 P0#1：datalog 聚合快照专用类别（telemetry 数据非行为规则，
    # 注入检索按 category 过滤天然隔离）。全仓唯一 isinstance 校验在
    # rule_extractor.py:239，非穷举依赖，加成员安全。
    TELEMETRY = "telemetry"

    # 2026-09-23 F4：返审值守报告专用类别（同 TELEMETRY 理由：
    # 非行为规则，与 best_practice 等行为知识隔离，检索不互扰）。
    AUDIT = "audit"


class ToolType(str, Enum):
    STATIC_ANALYZER = "static_analyzer"
    CODE_REVIEW = "code_review"
    SECURITY_SCANNER = "security_scanner"
    LINTING = "linting"


class PatternType(str, Enum):
    ANTI_PATTERN = "anti_pattern"
    BEST_PRACTICE = "best_practice"


@dataclass(frozen=True)
class FeedbackItem:
    tool_name: str
    tool_type: ToolType
    rule_id: str
    rule_name: str
    category: FeedbackCategory
    severity: FeedbackSeverity
    message: str
    file_path: str
    line: int
    snippet: str | None = None
    suggestion: str | None = None
    confidence: float = 0.8


@dataclass(frozen=True)
class Pattern:
    file_patterns: tuple[str, ...] = ()
    code_patterns: tuple[str, ...] = ()
    context_keywords: tuple[str, ...] = ()
    severity_distribution: dict[str, int] = field(default_factory=dict)
    tool_support: tuple[str, ...] = ()


@dataclass(frozen=True)
class LearnedRule:
    id: str
    name: str
    description: str
    category: FeedbackCategory
    pattern: Pattern
    tools: tuple[str, ...]
    frequency: int
    confidence: float
    quality_score: float = 0.0
    status: str = "draft"
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.created_at is None:
            object.__setattr__(self, "created_at", datetime.now())
