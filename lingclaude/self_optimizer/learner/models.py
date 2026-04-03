from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class FeedbackSeverity(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FeedbackCategory(Enum):
    SECURITY = "security"
    PERFORMANCE = "performance"
    CODE_QUALITY = "code_quality"
    MAINTAINABILITY = "maintainability"
    BEST_PRACTICE = "best_practice"
    BUG_RISK = "bug_risk"
    ARCHITECTURE = "architecture"


class ToolType(Enum):
    STATIC_ANALYZER = "static_analyzer"
    CODE_REVIEW = "code_review"
    SECURITY_SCANNER = "security_scanner"
    LINTING = "linting"


class PatternType(Enum):
    ANTI_PATTERN = "anti_pattern"
    BEST_PRACTICE = "best_practice"


@dataclass
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


@dataclass
class Pattern:
    file_patterns: list[str] = field(default_factory=list)
    code_patterns: list[str] = field(default_factory=list)
    context_keywords: list[str] = field(default_factory=list)
    severity_distribution: dict[str, int] = field(default_factory=dict)
    tool_support: list[str] = field(default_factory=list)


@dataclass
class LearnedRule:
    id: str
    name: str
    description: str
    category: FeedbackCategory
    pattern: Pattern
    tools: list[str]
    frequency: int
    confidence: float
    quality_score: float = 0.0
    status: str = "draft"
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.created_at is None:
            self.created_at = datetime.now()
