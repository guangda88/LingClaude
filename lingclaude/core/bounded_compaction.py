"""有界类型化上下文压缩（NanoJev 契约消费层消费点⑧，2026-09-22）。

对齐 NanoJev/Jev 的 Context Pruning / 上下文压缩场景：把冗长对话历史或工具输出
压成**有界、有类型的决策模式**（声明字段 + 有界选项 + 来源引用），而非无界自由
文本摘要——减少信息损失（结构化字段比散文摘要更不易丢关键项）与后续解析成本
（类型化模式可直接被下游消费，不必重新 LLM 解析散文）。

设计（对齐 lc 红线：能模式匹配解决的不用模型）：
- 主路径 = 从被压缩对话段提取**有界结构化决策模式**（纯本地提取，0 模型调用）：
  - 声明字段（bounded）：目标 / 当前进度 / 已确认结论 / 阻塞项——各字段条数有界
  - 有界选项（bounded）：待决分支（open options，≤N 条）
  - 来源引用（sourced）：压缩了哪几轮（dropped 轮次区间 + 关键 obs/fact 引用）
- 有界 = 每字段固定上限（超界按优先级截断），保证压缩产物尺寸可预测
  （治「压缩摘要比原文还长」的反效果，也保住前缀缓存稳定性）。
- fail-open = 提取无实质信号（全是噪声段）→ 返回 None，调用方回退原自由文本摘要。

停层声明（铁律 2 细则 5）：
- 内核 = build_bounded_decision_pattern(messages, dropped_count) -> dict|None（纯函数无 I/O）
- 接缝 = 有界字段提取协议（复用 context_compression.extract_facts_from_messages 的事实源）
- 实现 = 单实现（本地提取，字段有界）
边界纪律：产物是**类型化 dict**（供 context_compression.compress_messages 的有界
通道渲染成有框定摘要文本），不改压缩算法本体；未启用有界通道时零行为分叉。
"""
from __future__ import annotations

from typing import Any

# 有界字段上限（治「压缩摘要比原文还长」：每字段固定条数，产物尺寸可预测）
_BOUNDED_LIMITS = {
    "goal": 1,            # 目标（1 句，取首条最强信号）
    "progress": 4,        # 当前进度（已完成的关键步骤）
    "confirmed": 4,       # 已确认结论（decisions 里有确定性措辞的）
    "blocked": 3,         # 阻塞项（errors/blocker 信号）
    "open_options": 4,    # 待决分支（未定结论的选项）
    "sources": 6,         # 来源引用（压缩了哪些轮次/文件）
}

# 字段关键词信号（纯本地提取，0 模型调用）
_GOAL_KEYWORDS = ("目标", "需求", "要求", "任务", "goal", "task", "需要", "要")
_DONE_KEYWORDS = ("已完成", "完成", "搞定", "全绿", "通过", "已提交", "落地",
                  "done", "completed", "passed", "fixed", "merged")
_DECISION_KEYWORDS = ("决定", "结论", "确认", "裁定", "拍板", "定为",
                      "decision", "conclusion", "confirmed")
_BLOCKED_KEYWORDS = ("阻塞", "失败", "报错", "无法", "卡住", "未通过", "死循环",
                     "blocked", "failed", "error", "cannot", "stuck")
_OPTION_KEYWORDS = ("选项", "方案", "待定", "二选一", "可选", "候选", "权衡",
                    "option", "candidate", "alternative", "tradeoff")


def _extract_text(msg: Any) -> str:
    """宽容提取消息文本（与 context_compression._extract_text 同语义，局部自足）。"""
    if isinstance(msg, str):
        return msg
    if isinstance(msg, dict):
        c = msg.get("content", "")
        return c if isinstance(c, str) else repr(c)
    return getattr(msg, "content", "") or ""


def _first_line_with(text: str, keywords: tuple[str, ...], max_len: int = 200) -> str | None:
    """取 text 里首条命中任一关键词的行（去空白截断到 max_len）。"""
    for line in text.split("\n"):
        s = line.strip()
        if len(s) < 4:
            continue
        low = s.lower()
        if any(k in low for k in keywords):
            return s[:max_len]
    return None


def build_bounded_decision_pattern(
    messages: list[Any],
    dropped_count: int,
    *,
    recent_context: str = "",
) -> dict[str, Any] | None:
    """把被压缩对话段压成有界类型化决策模式（纯本地提取，0 模型调用）。

    返回有界结构化 dict（字段有界、选项有界、来源可溯）；
    若提取不到任何实质信号（全是噪声/空段）→ 返回 None（调用方回退自由文本摘要）。

    产物形态：
    {
      "pattern": "bounded_decision/v1",
      "goal": str,                  # 目标（≤1）
      "progress": [str, ...],       # 已完成步骤（≤4）
      "confirmed": [str, ...],      # 已确认结论（≤4）
      "blocked": [str, ...],        # 阻塞项（≤3）
      "open_options": [str, ...],   # 待决分支（≤4）
      "sources": {"dropped_rounds": int, "files": [str, ...]},  # 来源引用
      "bounds_applied": {field: bool}  # 哪些字段触达上限被截断（可观测）
    }
    """
    # 复用事实源（若有）——提取文件引用；字段信号本地独立提取（不依赖 facts 结构）
    all_text = "\n".join(t for t in (_extract_text(m) for m in messages) if t)
    recent_text = recent_context or all_text[-2000:]

    goal = _first_line_with(recent_text, _GOAL_KEYWORDS) or _first_line_with(all_text, _GOAL_KEYWORDS)
    progress_lines: list[str] = []
    for line in all_text.split("\n"):
        s = line.strip()
        if len(s) < 8:
            continue
        if any(k in s.lower() for k in _DONE_KEYWORDS):
            progress_lines.append(s[:160])
        if len(progress_lines) >= _BOUNDED_LIMITS["progress"]:
            break
    confirmed_lines: list[str] = []
    for line in all_text.split("\n"):
        s = line.strip()
        if len(s) < 8:
            continue
        if any(k in s.lower() for k in _DECISION_KEYWORDS):
            confirmed_lines.append(s[:160])
        if len(confirmed_lines) >= _BOUNDED_LIMITS["confirmed"]:
            break
    blocked_lines: list[str] = []
    for line in all_text.split("\n"):
        s = line.strip()
        if len(s) < 6:
            continue
        if any(k in s.lower() for k in _BLOCKED_KEYWORDS):
            blocked_lines.append(s[:160])
        if len(blocked_lines) >= _BOUNDED_LIMITS["blocked"]:
            break
    option_lines: list[str] = []
    for line in all_text.split("\n"):
        s = line.strip()
        if len(s) < 6:
            continue
        if any(k in s.lower() for k in _OPTION_KEYWORDS):
            option_lines.append(s[:160])
        if len(option_lines) >= _BOUNDED_LIMITS["open_options"]:
            break

    # 来源引用：压缩了 dropped_count 轮 + 提取文件引用（路径形 token）
    import re
    files = sorted({
        m for m in re.findall(r"(?:~|(?<![\w])/|\.\.?/)[\w./~-]+", all_text)
        if len(m) > 3 and not m.startswith("http")
    })[:_BOUNDED_LIMITS["sources"]]

    # fail-open：无任何实质信号 → 回退（None）
    if not any([goal, progress_lines, confirmed_lines, blocked_lines, option_lines, files]):
        return None

    bounds_applied = {
        "progress": len(progress_lines) >= _BOUNDED_LIMITS["progress"],
        "confirmed": len(confirmed_lines) >= _BOUNDED_LIMITS["confirmed"],
        "blocked": len(blocked_lines) >= _BOUNDED_LIMITS["blocked"],
        "open_options": len(option_lines) >= _BOUNDED_LIMITS["open_options"],
        "files": len(files) >= _BOUNDED_LIMITS["sources"],
    }
    return {
        "pattern": "bounded_decision/v1",
        "goal": goal or "",
        "progress": progress_lines,
        "confirmed": confirmed_lines,
        "blocked": blocked_lines,
        "open_options": option_lines,
        "sources": {"dropped_rounds": dropped_count, "files": files},
        "bounds_applied": bounds_applied,
    }


def render_bounded_pattern(pattern: dict[str, Any]) -> str:
    """把有界类型化决策模式渲染成有框定摘要文本（有界、可被下游直接解析）。

    与自由文本摘要的区别：字段分节 + 每节条数有界 + 顶部声明 pattern 版本
    （下游/回放层可识别这是类型化产物，非散文摘要）。无字段的节省略。
    """
    if not pattern:
        return ""
    lines: list[str] = [f"## 有界压缩（{pattern.get('pattern', 'bounded_decision/v1')}）"]
    goal = pattern.get("goal", "")
    if goal:
        lines.append(f"- 目标: {goal}")
    for key, label in (("progress", "进度"), ("confirmed", "已确认"),
                       ("blocked", "阻塞"), ("open_options", "待决分支")):
        items = pattern.get(key) or []
        if items:
            lines.append(f"- {label}（{len(items)}）:")
            lines.extend(f"  · {it}" for it in items)
    sources = pattern.get("sources") or {}
    dropped = sources.get("dropped_rounds", 0)
    src_lines: list[str] = []
    if dropped:
        src_lines.append(f"前 {dropped} 轮对话已压缩")
    files = sources.get("files") or []
    if files:
        src_lines.append(f"涉及文件: {', '.join(files)}")
    if src_lines:
        lines.append(f"- 来源: " + "；".join(src_lines))
    return "\n".join(lines)
