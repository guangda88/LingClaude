# lingclaude/core/context_engine.py
"""上下文引擎抽象（方案C v4 P1#2，对齐 hermes native_compaction 语义）。

hermes 实测锚点（native_compaction.py:226）：
    "A summary is never byte/character-sliced: Hermes summaries carry
     structural framing (handoff prefix, end marker) ... dropped instead."

语义迁移到 lc：
- **摘要永不切片**：压缩摘要是结构化整体（handoff 前缀 + 正文 + 结束标记），
  截断会破坏「遗嘱式」自支撑性——要么整条保留，要么整条丢弃。
- **摘要是可识别的一等公民**：引擎提供 is_summary_entry 判定，压缩层
  不再靠「开头是否 ## 压缩摘要」的隐式字符串约定各自实现。
- **框定单一来源**：handoff 前缀格式收敛到 SUMMARY_FRAMING 一处，
  context_compression 与 TUI 回放层共用，不再漂移。

边界：本模块只做框定与判定，不做压缩本身——压缩算法仍是
context_compression.py 的职责（摘要生成与压缩切片管线）。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

# lc 压缩摘要的既有锚点（与 context_compression.generate_chinese_summary 对齐）
SUMMARY_HEADLINE = "## 压缩摘要"
_SUMMARY_HEADLINE_RE_PREFIX = "## 压缩摘要（前"


@dataclass(frozen=True)
class SummaryFraming:
    """压缩摘要的结构化框定（单一来源，TUI/压缩层共用）。

    handoff 前缀带 dropped_count（溯源：这份摘要是替代哪段历史的）；
    end_marker 供回放层判断摘要在何处结束、正文从何处开始。
    """

    dropped_count: int
    handoff_prefix: str                    # 首行（含轮数）
    body: str                              # 摘要正文（不含前缀/结束标记）
    end_marker: str = "<!-- summary-end -->"

    def render(self) -> str:
        """渲染完整摘要消息文本（前缀 + 正文 + 结束标记）。"""
        parts = [self.handoff_prefix.rstrip(), "", self.body.rstrip(), "",
                 self.end_marker]
        return "\n".join(parts)

    @property
    def approx_chars(self) -> int:
        return len(self.handoff_prefix) + len(self.body) + len(self.end_marker)


def make_framing(dropped_count: int, body: str) -> SummaryFraming:
    """从摘要正文构建框定（handoff 前缀格式此处唯一）。"""
    prefix = f"{SUMMARY_HEADLINE}（前 {dropped_count} 轮对话）"
    return SummaryFraming(dropped_count=dropped_count,
                          handoff_prefix=prefix, body=body or "")


class ContextEngine(ABC):
    """上下文引擎抽象：摘要的框定、判定与保留策略。

    实现方约束（E7 门禁）：不得在此层做 LLM 调用——引擎是纯结构层，
    有副作用的摘要生成留在 context_compression。
    """

    @abstractmethod
    def frame(self, dropped_count: int, body: str) -> SummaryFraming:
        """构建摘要框定。"""

    @abstractmethod
    def is_summary_entry(self, item: Any) -> bool:
        """判定条目是否为压缩摘要（压缩层跳过它、回放层特殊渲染）。"""

    @abstractmethod
    def retain_whole(self, item: Any, budget_chars: int) -> bool:
        """never-slice 语义：摘要要么整条保留（True）要么整条丢弃（False），
        绝不允许返回「切一半」。默认实现：装得下就保留。"""


class DefaultContextEngine(ContextEngine):
    """默认实现：对齐 lc 既有压缩摘要格式，零行为变化。"""

    def frame(self, dropped_count: int, body: str) -> SummaryFraming:
        return make_framing(dropped_count, body)

    def is_summary_entry(self, item: Any) -> bool:
        text = _extract_text(item)
        return text.lstrip().startswith(SUMMARY_HEADLINE)

    def retain_whole(self, item: Any, budget_chars: int) -> bool:
        text = _extract_text(item)
        return len(text) <= budget_chars


def extract_summary_framing(item: Any) -> Optional[SummaryFraming]:
    """从历史消息中反向解析摘要框定（回放层/审计用）。

    解析失败（非摘要/格式漂移）返回 None——调用方按普通消息处理。
    """
    text = _extract_text(item)
    stripped = text.lstrip()
    if not stripped.startswith(_SUMMARY_HEADLINE_RE_PREFIX):
        return None
    try:
        first_line, _, rest = stripped.partition("\n")
        dropped = int(first_line.split("（前 ")[1].split(" 轮对话")[0])
    except (IndexError, ValueError):
        logger.debug("context_engine: 摘要首行格式漂移，按未知框定处理")
        return None
    body, _, _tail = rest.rpartition("<!-- summary-end -->")
    return SummaryFraming(dropped_count=dropped,
                          handoff_prefix=first_line, body=body.strip("\n"))


def _extract_text(item: Any) -> str:
    """宽容提取消息文本：str 直返；dict 取 content；其他 repr。"""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        content = item.get("content", "")
        if isinstance(content, str):
            return content
        return repr(content)
    return getattr(item, "content", "") or ""


# 模块级单例（进程内唯一引擎；测试用 _reset）
_default_engine: Optional[ContextEngine] = None


def get_context_engine() -> ContextEngine:
    global _default_engine
    if _default_engine is None:
        _default_engine = DefaultContextEngine()
    return _default_engine


def set_context_engine(engine: ContextEngine) -> None:
    """注入自定义引擎（插片式替换；禁 None——恢复默认用 _reset）。"""
    if engine is None:
        raise ValueError("context engine must not be None")
    global _default_engine
    _default_engine = engine


def _reset_context_engine() -> None:
    """仅测试用。"""
    global _default_engine
    _default_engine = None
