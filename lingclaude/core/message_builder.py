"""LINGKERNEL_v1 task #1 — MessageBuilder 模块（query_engine 第一阶段拆分）

dsh 对位: `core/system-prompt` 模块（system-prompt/assemble 事件）。
职责: 构建 system_prompt section + 装配完整 prompt。

从 query_engine._build_adaptive_system_prompt (130+ 行) 抽取。
保持 API 等价, query_engine 内部委托。
"""

from __future__ import annotations

import logging
from typing import Any, Protocol


logger = logging.getLogger(__name__)


class _BehaviorLike(Protocol):
    hallucination_risk: float
    frustration_rate: float
    tool_error_rate: float
    corrections_received: int
    total_turns: int
    tool_use_rate: float
    tool_error_count: int


class _MemoryLike(Protocol):
    def inject_common_to_prompt(self) -> str: ...
    def build_context_injection(self, current_query: str) -> str: ...


class _MetaCogLike(Protocol):
    def get_system_prompt_injection(self) -> str: ...


class _DementiaLike(Protocol):
    def diagnose(self) -> Any: ...


class MessageBuilder:
    """System prompt + message 装配器（独立模块）。

    接收 behavior/memory/meta_cognition/dementia_detector 依赖，
    输出完整 system prompt string。
    """

    BASE_PROMPT = (
        "你是灵克，一个会自我进化的开源 AI 编程助手。\n"
        "\n"
        "核心规则:\n"
        "1. 先判断用户意图：只有涉及具体代码、文件、项目结构的问题才需要调用工具。\n"
        "2. 一般性对话、观点讨论、概念解释等非代码问题，直接回答，不要调用工具。\n"
        "3. 回答代码相关问题时，必须先用工具（read/grep/glob）读取源码，不要猜测。\n"
        "4. 如果用户指出你胡说或没读代码，立即使用工具重新阅读相关文件。\n"
        "5. 你擅长代码理解、编辑、终端操作，并通过自优化持续提升能力。\n"
        "6. 用中文回答，代码保持原样。"
    )

    def __init__(
        self,
        *,
        behavior: _BehaviorLike,
        layered_memory: _MemoryLike,
        meta_cognition: _MetaCogLike,
        dementia_detector: _DementiaLike,
    ) -> None:
        self._behavior = behavior
        self._layered_memory = layered_memory
        self._meta_cognition = meta_cognition
        self._dementia_detector = dementia_detector

    def build_adaptive_system_prompt(
        self,
        messages: list[str],
        session_cache_hits: int = 0,
        project_index: dict[str, list[str]] | None = None,
    ) -> str:
        """dsh system-prompt/assemble 等价: 装配各 section, 返回最终 system prompt。"""
        bm = self._behavior
        extras: list[str] = []

        memory_text = self._layered_memory.inject_common_to_prompt()
        if memory_text:
            extras.append("\n\n" + memory_text)

        meta_text = self._meta_cognition.get_system_prompt_injection()
        if meta_text:
            extras.append("\n\n" + meta_text)

        # 行为警告 section (dsh: behavior/* events)
        if bm.hallucination_risk > 0.3:
            extras.append(
                "\n⚠ 行为警告: 你近期幻觉风险较高({:.0%})。回答代码问题时必须先调用工具读取文件，"
                "绝对不能凭记忆猜测代码内容。一般性问题可以直接回答。".format(bm.hallucination_risk)
            )
        if bm.frustration_rate > 0.2:
            extras.append(
                "\n⚠ 用户状态: 用户近期频繁表现出沮丧({:.0%})。"
                "请格外仔细，回答代码问题前先读文件。".format(bm.frustration_rate)
            )
        if bm.tool_error_rate > 0.3:
            extras.append(
                "\n⚠ 工具问题: 近期工具调用失败率较高({:.0%})。"
                "请检查参数格式，确保文件路径正确。".format(bm.tool_error_rate)
            )
        if bm.corrections_received >= 2:
            extras.append(
                "\n⚠ 纠正记录: 已收到 {} 次用户纠正。"
                "请更加谨慎，确认信息准确后再回答。".format(bm.corrections_received)
            )
        if bm.total_turns > 2 and bm.tool_use_rate < 0.2:
            extras.append(
                "\n💡 提醒: 你近期工具使用率较低({:.0%})。"
                "面对代码相关问题请积极使用工具。".format(bm.tool_use_rate)
            )

        # 错误复发 section (data flywheel 集成)
        if bm.tool_error_count > 0:
            try:
                from lingclaude.core.data_flywheel import DataFlywheel
                fw = DataFlywheel()
                if fw.should_alert(threshold=0.5):
                    stats = fw.get_stats()
                    extras.append(
                        f"\n⚠ 错误复发: 错误复发率 {stats.recurrence_rate:.0%}，"
                        f"共 {stats.total_errors} 个错误，{stats.total_corrections} 个修复。"
                        "请避免重复已犯过的错误。"
                    )
                fw.close()
            except Exception as e:
                logger.warning("feedback writer close failed: %s", e)

        # Session 缓存提示
        if session_cache_hits > 2:
            extras.append(
                f"\n📂 文件缓存: 本次会话已命中 {session_cache_hits} 次。"
                "已读文件不需要重复读取。"
            )

        # 已学经验 (lingmemory knowledge base)
        try:
            from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase
            kb = KnowledgeBase()
            keyword = messages[-1][:50] if messages else ""
            result = kb.search_rules(keyword=keyword, limit=5)
            if result.is_ok and result.data:
                rule_lines = [
                    f"  - {r.description} (置信度={r.confidence:.0%})"
                    for r in result.data
                    if r.confidence > 0.5
                ]
                if rule_lines:
                    extras.append("\n📚 已学经验:\n" + "\n".join(rule_lines))
            all_result = kb.get_all_rules(limit=3)
            if all_result.is_ok and all_result.data:
                existing_descs = {r.description for r in (result.data or [])}
                general_lines = [
                    f"  - {r.description} (置信度={r.confidence:.0%})"
                    for r in all_result.data
                    if r.confidence > 0.7 and r.description not in existing_descs
                ]
                if general_lines:
                    extras.append("\n📚 通用经验:\n" + "\n".join(general_lines))
            kb.close()
        except Exception as e:
            logger.warning("knowledge base close failed: %s", e)

        # 经验注入 (layered_memory)
        try:
            current_query = messages[-1] if messages else ""
            experience_text = self._layered_memory.build_context_injection(current_query=current_query)
            if experience_text and len(experience_text) > 50:
                extras.append("\n\n" + experience_text)
        except Exception as e:
            logger.warning("layered memory build_context_injection failed: %s", e)

        # 痴呆检测干预
        diagnosis = self._dementia_detector.diagnose()
        if diagnosis.intervention_prompt:
            extras.append("\n\n" + diagnosis.intervention_prompt)

        # 项目结构索引
        if project_index:
            pkg_summary = "\n".join(
                f"- {pkg}/: {', '.join(sorted(files[:5]))}"
                for pkg, files in sorted(project_index.items())
                if pkg != "."
            )
            if pkg_summary:
                extras.append("\n📁 当前项目结构:\n" + pkg_summary)

        return self.BASE_PROMPT + "".join(extras)


def assemble_system_prompt(
    *,
    behavior: Any,
    layered_memory: Any,
    meta_cognition: Any,
    dementia_detector: Any,
    messages: list[str],
    session_cache_hits: int = 0,
    project_index: dict[str, list[str]] | None = None,
) -> str:
    """便捷函数: 单次调用返回完整 system prompt。

    等价于 MessageBuilder(...).build_adaptive_system_prompt(...)。
    """
    mb = MessageBuilder(
        behavior=behavior,
        layered_memory=layered_memory,
        meta_cognition=meta_cognition,
        dementia_detector=dementia_detector,
    )
    return mb.build_adaptive_system_prompt(
        messages=messages,
        session_cache_hits=session_cache_hits,
        project_index=project_index,
    )