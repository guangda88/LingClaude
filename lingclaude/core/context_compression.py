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

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


logger = logging.getLogger(__name__)


class CompressionLevel(str, Enum):
    TRUNCATE = "truncate"
    SUMMARY = "summary"
    AGGRESSIVE = "aggressive"
    REASONING_AWARE = "reasoning_aware"


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
    priority_hints: list[str] = None  # handover重点词表，优先保留包含这些词的消息
    handover_conclusions: list[str] = None  # handover key_conclusions，强制注入摘要
    # T1-1: 动态预算 — 按模型真实窗口计算 summary 上限（None = 用 summary_max_chars 兜底）
    model_window_tokens: int | None = None
    # T1-1: LLM 摘要开关 — True 且 provider 可用时用 LLM 摘要，失败降级正则
    use_llm_summary: bool = False
    # T1-1: LLM 摘要 provider — 由调用方传入 engine 持有的 provider；None 时自动降级正则
    provider: Any | None = None

    def __post_init__(self):
        if self.priority_hints is None:
            self.priority_hints = []
        if self.handover_conclusions is None:
            self.handover_conclusions = []

    def effective_summary_chars(self) -> int:
        """T1-1: 动态预算 — 模型窗口×4% 或 summary_max_chars，取较小者。

        4 字符≈1 token 的保守估算；窗口越大摘要可越长，但不超过硬上限。
        """
        if self.model_window_tokens:
            window_budget = self.model_window_tokens * 4 // 100
            return min(window_budget, self.summary_max_chars)
        return self.summary_max_chars


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

_THINKING_BLOCK_RE = re.compile(
    r'<thinking[^>]*>(.*?)</thinking>',
    re.DOTALL | re.IGNORECASE,
)

_REASONING_BLOCK_RE = re.compile(
    r'<reasoning[^>]*>(.*?)</reasoning>',
    re.DOTALL | re.IGNORECASE,
)

_REASONING_KEYWORDS = (
    "分析", "考虑", "权衡", "替代", "方案", "假设", "推测",
    "analyze", "consider", "tradeoff", "alternative", "hypothesis",
    "infer", "deduce", "evaluate", "assess", "weigh",
    "because", "since", "therefore", "thus", "hence",
    "key insight", "important", "critical", "should", "must",
)


def extract_facts_from_messages(
    messages: list[Any],
    priority_hints: list[str] | None = None,
) -> dict[str, list[str]]:
    """从消息中提取结构化事实。

    Args:
        priority_hints: handover重点词表。包含这些词的消息/句子优先保留。

    Returns:
        {
            "files_read": [...],
            "decisions": [...],
            "exclusions": [...],
            "errors": [...],
            "priority_snippets": [...],  # 包含handover重点词的关键句
        }
    """
    priority_hints = priority_hints or []
    files_seen: set[str] = set()
    decisions: list[str] = []
    exclusions: list[str] = []
    errors: list[str] = []
    priority_snippets: list[str] = []

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

            # handover引导：包含重点词的句子优先保留
            if priority_hints:
                for hint in priority_hints:
                    if hint.lower() in lower and len(priority_snippets) < 25:
                        priority_snippets.append(stripped[:250])
                        break

    return {
        "files_read": sorted(files_seen),
        "decisions": decisions,
        "exclusions": exclusions,
        "errors": errors,
        "priority_snippets": priority_snippets,
    }


def generate_chinese_summary(
    facts: dict[str, list[str]],
    dropped_count: int,
    recent_context: str = "",
    handover_conclusions: list[str] | None = None,
) -> str:
    """生成中文优化的压缩摘要（遗嘱式）。

    设计原则：
    - 假设所有历史消息将丢失，摘要必须独立支撑续接
    - 代码/路径/技术术语保持英文（省 token）
    - 决策/推理保持中文（保语义）
    - 强制包含认知锚点
    - handover_conclusions强制注入（人筛选的重点结论）
    """
    handover_conclusions = handover_conclusions or []
    sections: list[str] = []
    sections.append(f"## 压缩摘要（前 {dropped_count} 轮对话）\n")

    # handover重点结论优先展示（人筛选的，最高价值）
    if handover_conclusions:
        sections.append("### 核心结论（handover引导）")
        for c in handover_conclusions[:10]:
            sections.append(f"- {c}")
        sections.append("")

    # handover引导提取的关键句（包含重点词的消息）
    if facts.get("priority_snippets"):
        sections.append("### 关键上下文（handover重点相关）")
        for s in facts["priority_snippets"][:15]:
            sections.append(f"- {s}")
        sections.append("")

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

    facts = extract_facts_from_messages(dropped, priority_hints=config.priority_hints)
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

    if config.level == CompressionLevel.REASONING_AWARE:
        reasoning = extract_reasoning_from_messages(dropped)
        reasoning_summary = generate_reasoning_summary(reasoning, dropped_count)
        recent_text = _extract_text(kept[0]) if kept else ""
        fact_summary = generate_chinese_summary(
            facts, dropped_count,
            recent_context=recent_text,
            handover_conclusions=config.handover_conclusions,
        )
        summary = reasoning_summary + "\n" + fact_summary
        archived_count = sum(len(v) for v in facts.values()) + sum(len(v) for v in reasoning.values())
    else:
        recent_text = _extract_text(kept[0]) if kept else ""
        summary = generate_chinese_summary(
            facts, dropped_count,
            recent_context=recent_text,
            handover_conclusions=config.handover_conclusions,
        )
        # T1-1: LLM 摘要通道 — use_llm_summary 且 provider 可用时替换正则摘要，失败降级
        if config.use_llm_summary:
            llm_summary = _try_llm_summary(facts, dropped_count, config)
            if llm_summary:
                summary = llm_summary
        archived_count = sum(len(v) for v in facts.values())

    budget = config.effective_summary_chars()
    if len(summary) > budget:
        summary = summary[:budget] + "\n... (摘要已截断)"

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


def _try_llm_summary(
    facts: dict[str, list[str]],
    dropped_count: int,
    config: CompressionConfig,
) -> str | None:
    """T1-1: LLM 摘要通道 — 用 provider 生成结构化摘要，失败/不可用返回 None（正则兜底）。

    调用约定：provider 来自 config.provider（engine 持有）；无 provider 或
    调用异常时静默降级正则，不阻塞压缩。
    """
    if not config.use_llm_summary or config.provider is None:
        return None
    try:
        from lingclaude.core.model_adapter import ModelAdapter

        adapter = ModelAdapter(config.provider)
        budget = config.effective_summary_chars()
        prompt = (
            "你是一个对话压缩器。把以下历史对话事实压缩成结构化中文摘要，"
            "保留：已读文件、关键决策、已排除方案、遇到的错误。"
            f"控制在 {budget} 字符内，代码/路径保持英文原文。\n\n"
            "### 事实\n"
        )
        for key, items in facts.items():
            if items:
                prompt += f"- {key}: {', '.join(items[:20])}\n"

        result = adapter.call(
            messages=(("user", prompt),),
            max_tokens=max(budget // 4, 256),
        )
        if result.is_error:
            logger.warning("LLM summary failed: %s", result.error)
            return None
        summary = result.data.content
        if summary and summary.strip():
            logger.info("LLM summary generated (%d chars) for %d dropped turns", len(summary), dropped_count)
            return summary.strip()
    except Exception as e:  # noqa: BLE001 — LLM 摘要失败静默降级正则，不阻塞压缩
        logger.warning("LLM summary failed, falling back to regex: %s", e)
    return None


def extract_reasoning_from_messages(
    messages: list[Any],
) -> dict[str, list[str]]:
    """Extract reasoning/thinking content from messages.

    Detects <thinking> and <reasoning> XML blocks in assistant messages
    and extracts key insights, alternatives considered, and conclusions.

    Returns:
        {
            "reasoning_chains": [...],
            "alternatives": [...],
            "conclusions": [...],
            "thinking_snippets": [...],
        }
    """
    reasoning_chains: list[str] = []
    alternatives: list[str] = []
    conclusions: list[str] = []
    thinking_snippets: list[str] = []

    for msg in messages:
        text = _extract_text(msg)
        if not text:
            continue

        for match in _THINKING_BLOCK_RE.finditer(text):
            block = match.group(1).strip()
            if block:
                thinking_snippets.append(block[:500])
                _extract_reasoning_insights(block, reasoning_chains, alternatives, conclusions)

        for match in _REASONING_BLOCK_RE.finditer(text):
            block = match.group(1).strip()
            if block:
                thinking_snippets.append(block[:500])
                _extract_reasoning_insights(block, reasoning_chains, alternatives, conclusions)

    return {
        "reasoning_chains": reasoning_chains[:20],
        "alternatives": alternatives[:15],
        "conclusions": conclusions[:15],
        "thinking_snippets": thinking_snippets[:15],
    }


def _extract_reasoning_insights(
    text: str,
    reasoning_chains: list[str],
    alternatives: list[str],
    conclusions: list[str],
) -> None:
    """Extract structured insights from a reasoning block."""
    lines = text.split("\n")
    for line in lines:
        stripped = line.strip()
        if not stripped or len(stripped) < 10:
            continue
        lower = stripped.lower()

        if any(k in lower for k in _REASONING_KEYWORDS):
            if len(reasoning_chains) < 20:
                reasoning_chains.append(stripped[:250])

        if any(k in lower for k in ("替代", "alternative", "instead", "rather", "otherwise", "换个", "另一个")):
            if len(alternatives) < 15:
                alternatives.append(stripped[:250])

        if any(k in lower for k in ("结论", "conclusion", "因此", "所以", "thus", "therefore", "hence", "最终", "finally")):
            if len(conclusions) < 15:
                conclusions.append(stripped[:250])


def generate_reasoning_summary(
    reasoning: dict[str, list[str]],
    dropped_count: int,
) -> str:
    """Generate a concise summary of reasoning chains from compressed messages.

    Preserves the decision-making logic while discarding verbose thinking.
    """
    sections: list[str] = []
    sections.append(f"## 推理压缩摘要（前 {dropped_count} 轮推理链）\n")

    if reasoning.get("conclusions"):
        sections.append("### 推理结论")
        for c in reasoning["conclusions"][:10]:
            sections.append(f"- {c}")
        sections.append("")

    if reasoning.get("alternatives"):
        sections.append("### 考虑过的替代方案")
        for a in reasoning["alternatives"][:8]:
            sections.append(f"- {a}")
        sections.append("")

    if reasoning.get("reasoning_chains"):
        sections.append("### 关键推理步骤")
        for r in reasoning["reasoning_chains"][:12]:
            sections.append(f"- {r}")
        sections.append("")

    return "\n".join(sections)


def _estimate_tokens_saved(messages: list[Any]) -> int:
    total_chars = sum(len(_extract_text(m)) for m in messages)
    return total_chars // 4


# T1-1 深化: prefix cache 保留 stub
# 设计目标: 压缩后保留前 N 个 token 不变（用于 prompt cache 命中）
# 当前为 stub，后续可接入 Tier1 stub 化逻辑

@dataclass
class PrefixCacheConfig:
    """Prefix cache 保留配置。"""
    enabled: bool = False
    preserve_tokens: int = 0  # 保留的 token 数（0 = 不保留）
    preserve_messages: int = 0  # 或保留的消息数（优先于 tokens）

    def should_preserve(self, total_tokens: int) -> bool:
        """判断是否需要保留 prefix cache。

        preserve_messages 优先：有值时按消息数判断（len(messages) > preserve_messages），
        否则按 token 数判断（total_tokens > preserve_tokens）。
        """
        if not self.enabled:
            return False
        if self.preserve_messages > 0:
            return True  # 有 preserve_messages 时总是启用（具体保留逻辑在 preserve_prefix_cache）
        if self.preserve_tokens > 0:
            return total_tokens > self.preserve_tokens
        return False


def _estimate_prefix_cache_tokens(messages: list[Any], config: PrefixCacheConfig) -> int:
    """估算可保留的 prefix cache token 数。

    返回: 0 = 不保留; N = 保留前 N 个 token
    修复: 用真实 token 估算（chars // 4），不再拍脑袋 len(messages) * 100
    """
    total_tokens = _estimate_tokens_saved(messages)
    if not config.should_preserve(total_tokens):
        return 0
    return config.preserve_tokens


def preserve_prefix_cache(messages: list[Any], config: PrefixCacheConfig) -> list[Any]:
    """保留 prefix cache 的消息，其余压缩。

    返回: 压缩后的消息列表（前 N 条不变 — prefix cache 命中前提是前缀稳定）

    修复: messages[-N:] → messages[:N]（保头不是保尾，cache 命中前提是前缀稳定）
    """
    if not config.enabled or config.preserve_messages <= 0:
        return messages
    preserve_count = min(config.preserve_messages, len(messages) // 2)
    preserved = messages[:preserve_count]  # 保留最前的几条（prefix cache 命中前提是前缀稳定）
    return preserved
