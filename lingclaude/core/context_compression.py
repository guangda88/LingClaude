from __future__ import annotations

"""Context Compression — 遗嘱式压缩 + 中文优化。

核心设计原则:
1. 压缩不是"遗忘"，是"归档" — 关键信息存入 LayeredMemory
2. 中文优化摘要 — 代码/技术术语英文，决策/推理中文
3. 强制包含认知锚点：已读文件、已排除方案、当前决策、未解决问题
4. 与 Crush 的摘要模板对标，但加入灵族独有的结构化记忆回写

三个压缩层级:
- TRUNCATE: 最旧消息截断（当前行为，保留为兜底）
- SUMMARY: 中文优化摘要 + 归档到 LayeredMemory
- AGGRESSIVE: 双语混合摘要，最大化 token 节省
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from lingclaude.core.types import Result

logger = logging.getLogger(__name__)


class CompressionLevel(str, Enum):
    TRUNCATE = "truncate"
    SUMMARY = "summary"
    AGGRESSIVE = "aggressive"


@dataclass(frozen=True)
class CompressionResult:
    compressed_messages: list[Any]
    dropped_count: int
    summary_text: str
    archived_facts: int
    tokens_estimated_saved: int
    level: CompressionLevel


@dataclass
class CompressionConfig:
    max_messages: int = 24
    summary_max_chars: int = 4000
    archive_to_memory: bool = True
    level: CompressionLevel = CompressionLevel.SUMMARY


_FILE_PATH_RE = re.compile(
    r'(?:^|\s|`)([\w./\-]+\.(?:py|js|ts|tsx|jsx|go|rs|java|rb|md|yaml|yml|json|toml|cfg|ini|sh|sql))(?:\s|`|$|,|:|;|\))'
)

_DECISION_KEYWORDS = (
    "决定", "选择", "采用", "方案", "decided", "chose", "selected",
    "approach", "strategy", "plan to",
)

_EXCLUSION_KEYWORDS = (
    "排除", "不用", "放弃", "excluded", "ruled out", "rejected",
    "not using", "skipped",
)

_ERROR_KEYWORDS = (
    "错误", "失败", "报错", "error", "failed", "bug", "issue",
    "不对", "不对", "问题",
)

_BLOCK_DELIMITER = "---BLOCK---"


def extract_facts_from_messages(messages: list[Any]) -> dict[str, list[str]]:
    """从消息中提取结构化事实。

    Returns:
        {
            "files_read": [...],
            "decisions": [...],
            "exclusions": [...],
            "errors": [...],
        }
    """
    files_seen: set[str] = set()
    decisions: list[str] = []
    exclusions: list[str] = []
    errors: list[str] = []

    for msg in messages:
        text = _extract_text(msg)
        if not text:
            continue

        for match in _FILE_PATH_RE.finditer(text):
            fp = match.group(1)
            if len(fp) > 3 and not fp.startswith("http"):
                files_seen.add(fp)

        for line in text.split("\n"):
            stripped = line.strip()
            if not stripped or len(stripped) < 10:
                continue
            lower = stripped.lower()

            if any(k in lower for k in _EXCLUSION_KEYWORDS):
                if len(exclusions) < 10:
                    exclusions.append(stripped[:200])
            elif any(k in lower for k in _DECISION_KEYWORDS):
                if len(decisions) < 15:
                    decisions.append(stripped[:200])

            if any(k in lower for k in _ERROR_KEYWORDS):
                if len(errors) < 10:
                    errors.append(stripped[:200])

    return {
        "files_read": sorted(files_seen),
        "decisions": decisions,
        "exclusions": exclusions,
        "errors": errors,
    }


def generate_chinese_summary(
    facts: dict[str, list[str]],
    dropped_count: int,
    recent_context: str = "",
) -> str:
    """生成中文优化的压缩摘要（遗嘱式）。

    设计原则：
    - 假设所有历史消息将丢失，摘要必须独立支撑续接
    - 代码/路径/技术术语保持英文（省 token）
    - 决策/推理保持中文（保语义）
    - 强制包含认知锚点
    """
    sections: list[str] = []
    sections.append(f"## 压缩摘要（前 {dropped_count} 轮对话）\n")

    if facts["files_read"]:
        files = facts["files_read"][:30]
        sections.append("### 已读文件")
        per_dir: dict[str, list[str]] = {}
        for fp in files:
            parts = fp.rsplit("/", 1)
            if len(parts) == 2:
                per_dir.setdefault(parts[0], []).append(parts[1])
            else:
                per_dir.setdefault(".", []).append(parts[0])
        for d, fs in sorted(per_dir.items()):
            sections.append(f"- {d}/: {', '.join(fs)}")
        sections.append("")

    if facts["decisions"]:
        sections.append("### 已做决策")
        for d in facts["decisions"]:
            sections.append(f"- {d}")
        sections.append("")

    if facts["exclusions"]:
        sections.append("### 已排除方案")
        for e in facts["exclusions"]:
            sections.append(f"- {e}")
        sections.append("")

    if facts["errors"]:
        sections.append("### 已遇错误")
        for e in facts["errors"]:
            sections.append(f"- {e}")
        sections.append("")

    if recent_context:
        sections.append("### 最近上下文片段")
        snippet = recent_context[:500]
        sections.append(f"```\n{snippet}\n```\n")

    return "\n".join(sections)


def compress_messages(
    messages: list[Any],
    config: CompressionConfig | None = None,
) -> CompressionResult:
    """压缩消息列表，返回压缩结果。

    策略：
    1. 保留最后 config.max_messages 条消息
    2. 从被丢弃的消息中提取事实
    3. 生成中文优化摘要
    4. 如果 archive_to_memory=True，事实已准备好供外部归档
    """
    config = config or CompressionConfig()
    total = len(messages)

    if total <= config.max_messages:
        return CompressionResult(
            compressed_messages=messages,
            dropped_count=0,
            summary_text="",
            archived_facts=0,
            tokens_estimated_saved=0,
            level=config.level,
        )

    keep_count = config.max_messages
    dropped = messages[:total - keep_count]
    kept = messages[total - keep_count:]

    facts = extract_facts_from_messages(dropped)
    dropped_count = (total - keep_count)

    if config.level == CompressionLevel.TRUNCATE:
        summary = f"[前 {dropped_count} 轮对话已压缩]"
        return CompressionResult(
            compressed_messages=[summary] + kept,
            dropped_count=dropped_count,
            summary_text=summary,
            archived_facts=0,
            tokens_estimated_saved=_estimate_tokens_saved(dropped),
            level=config.level,
        )

    recent_text = _extract_text(kept[0]) if kept else ""
    summary = generate_chinese_summary(facts, dropped_count, recent_context=recent_text)

    if len(summary) > config.summary_max_chars:
        summary = summary[:config.summary_max_chars] + "\n... (摘要已截断)"

    archived_count = sum(len(v) for v in facts.values())

    return CompressionResult(
        compressed_messages=[summary] + kept,
        dropped_count=dropped_count,
        summary_text=summary,
        archived_facts=archived_count,
        tokens_estimated_saved=_estimate_tokens_saved(dropped) - len(summary),
        level=config.level,
    )


def _extract_text(msg: Any) -> str:
    if isinstance(msg, str):
        return msg
    if isinstance(msg, dict):
        return msg.get("content", "") or msg.get("text", "") or ""
    if hasattr(msg, "content"):
        return msg.content or ""
    return str(msg) if msg else ""


def _estimate_tokens_saved(messages: list[Any]) -> int:
    total_chars = sum(len(_extract_text(m)) for m in messages)
    return total_chars // 4
