"""L7 认知层叶子类型模块 —— 枚举与纯数据结构，零内部依赖。

2026-09-24 循环 import 清偿（core-l7-classifier-circular-import）：
  原 MessageCategory / OKFType / MemoryTier / CognitiveMemory 定义在
  l7_cognitive.py，而 l7_cognitive 顶层反向 re-export l7_classifier.MessageClassifier，
  l7_classifier 顶层又 import l7_cognitive 的类型 → 直接
  ``import lingclaude.core.l7_classifier`` 触发循环（ImportError，
  仅经 l7_cognitive 入口因模块序侥幸可用）。

拆解方案：类型与纯数据结构下沉本叶子模块（仅依赖 stdlib），
l7_classifier 与 l7_cognitive 均改为从叶子导入；l7_cognitive 保留
re-export 兼容既有消费方（``from l7_cognitive import CognitiveMemory``）。
语义零变更——这些名字的定义逐字搬迁，不做任何行为调整。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


# ── 分层记忆 tier ──

class MemoryTier(str, Enum):
    ALWAYS = "always"        # 重要度 >= 8, 每次会话自动加载, ~2K tokens, 最多 5 条
    ONDEMAND = "ondemand"    # 重要度 3-7, 语义检索触发, 最多 10 条
    TRIGGERED = "triggered"  # 重要度 1-2, 会话钩子触发, 最多 20 条


def importance_to_tier(importance: int) -> MemoryTier:
    if importance >= 8:
        return MemoryTier.ALWAYS
    if importance >= 3:
        return MemoryTier.ONDEMAND
    return MemoryTier.TRIGGERED


# ── OKF type (知识格式) ──

class OKFType(str, Enum):
    DECISION = "decision"      # 架构/设计决策
    PREFERENCE = "preference"  # 用户/成员偏好
    HARDWARE = "hardware"      # 硬件配置
    BLOCKER = "blocker"        # 阻塞/问题
    PROJECT = "project"        # 项目信息
    CONCEPT = "concept"        # 核心概念
    MEMBER = "member"          # 成员信息
    TOOL = "tool"              # 工具/技术
    GLOSSARY = "glossary"      # 术语共识


# ── 消息分类 ──

class MessageCategory(str, Enum):
    DECISION = "decision"
    INCIDENT = "incident"
    ACHIEVEMENT = "achievement"
    PROJECT = "project"
    PREFERENCE = "preference"
    BLOCKER = "blocker"
    GENERAL = "general"


# ── 数据结构 ──

@dataclass
class CognitiveMemory:
    """L7 认知记忆条目 - 在 L7 MemoryEntry 之上加 tier/type/importance"""
    id: str = ""
    key: str = ""
    value: Any = None
    source: str = ""           # 来源成员
    session_id: str = ""
    importance: int = 5        # 1-10
    tier: MemoryTier = MemoryTier.TRIGGERED
    okf_type: OKFType = OKFType.CONCEPT
    tags: list[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    access_count: int = 0

    def __post_init__(self):
        if not self.id:
            self.id = uuid4().hex[:12]
        if not self.created_at:
            self.created_at = time.time()
        self.updated_at = self.created_at
        self.tier = importance_to_tier(self.importance)


__all__ = [
    "MemoryTier",
    "importance_to_tier",
    "OKFType",
    "MessageCategory",
    "CognitiveMemory",
]
