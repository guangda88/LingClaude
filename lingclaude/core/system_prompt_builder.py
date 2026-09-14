"""Adaptive system prompt builder — LINGKERNEL_v1 D6.

Extracted from QueryEngine._build_adaptive_system_prompt.
Constructs the dynamic system prompt by combining base rules with
behavioral warnings, learned knowledge, layered memory, dementia
detection, and project index context. All side-effects (KB access,
memory injection, flywheel stats) are swallowed and logged as warnings.
"""

from __future__ import annotations

import datetime
import logging
import os
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

_BASE_PROMPT = (
    "你是灵克，一个会自我进化的开源 AI 编程助手。\n"
    "\n"
    "核心规则:\n"
    "1. 先判断用户意图：只有涉及具体代码、文件、项目结构的问题才需要调用工具。\n"
    "2. 一般性对话、观点讨论、概念解释等非代码问题，直接回答，不要调用工具。\n"
    "3. 回答代码相关问题时，必须先用工具（read/grep/glob）读取源码，不要猜测。\n"
    "4. 如果用户指出你胡说或没读代码，立即使用工具重新阅读相关文件。\n"
    "5. 你擅长代码理解、编辑、终端操作，并通过自优化持续提升能力。\n"
    "6. 用中文回答，代码保持原样。\n"
    "\n"
    "工作纪律:\n"
    "7. 会话环境见下方 SESSION_CONTEXT（目录/日期/git 摘要），路径类回答以其为准。\n"
    "8. 只读操作（read/grep/glob 等）可并行调用；写操作与有副作用的命令先确认影响面再执行。\n"
    "9. 多步任务先列简要计划再动手；计划或结果变化时向用户说明。\n"
    "10. 回答先给结论再按需展开；文件引用使用 路径:行号 格式。\n"
    "11. 引用文件路径前必须实测存在，禁止凭记忆或他人汇报转述。"
)


def _build_session_context() -> str:
    """构建 SESSION_CONTEXT 块（对标生产级 agent 提示词的 Session context）。

    三要素: 当前目录 / 当前日期 / git 摘要（分支+脏文件+最近提交）。
    任何要素失败都跳过、任何异常都吞掉——可观测/注入组件不得破坏主 prompt。
    """
    parts: list[str] = []
    try:
        parts.append(f"当前目录: {os.getcwd()}")
    except Exception:  # noqa: BLE001
        pass
    try:
        parts.append(f"当前日期: {datetime.date.today().isoformat()}")
    except Exception:  # noqa: BLE001
        pass
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain", "-b"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0:
            lines = r.stdout.strip().splitlines()
            branch = lines[0].replace("## ", "") if lines else "unknown"
            dirty = [ln for ln in lines[1:] if ln.strip()][:10]
            block = f"git 分支: {branch}"
            if dirty:
                block += "\n未提交改动 (前10):\n" + "\n".join(f"  {ln}" for ln in dirty)
            parts.append(block)
    except Exception:  # noqa: BLE001
        pass
    try:
        r = subprocess.run(
            ["git", "show", "-s", "--format=%h %s (%ci)", "-5"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0 and r.stdout.strip():
            # 渲染侧过滤：提交消息里可能含幻觉治理标记字符（⚠/💡 等运行时符号，
            # 如 5ff2ba1 提交消息自述「被误打 ⚠[工具结果未验证]」），
            # 不应污染系统提示（test_adaptive 断言健康状态无 ⚠）。
            # 历史提交消息本身是事实，这里只过滤渲染，不篡改 git 历史。
            _SANITIZE = str.maketrans({"⚠": "!", "💡": "!"})
            lines = "\n".join(
                f"  {ln.translate(_SANITIZE)}"
                for ln in r.stdout.strip().splitlines()[:5]
            )
            parts.append(f"最近提交:\n{lines}")
    except Exception:  # noqa: BLE001
        pass
    if not parts:
        return ""
    return "\n\n# SESSION_CONTEXT\n" + "\n".join(parts)


def build_adaptive_system_prompt(
    behavior: Any,
    layered_memory: Any,
    meta_cognition: Any,
    messages: list[str],
    session_cache_hits: int,
    dementia_detector: Any,
    project_index: dict[str, Any] | None,
    tool_call_count: int = 0,  # R8: 用于触发 sub_agent 推荐提示
) -> str:
    """Build the adaptive system prompt.

    Args:
        behavior: BehaviorMetrics instance with hallucination_risk, frustration_rate,
            tool_error_rate, corrections_received, total_turns, tool_use_rate,
            tool_error_count.
        layered_memory: LayeredMemory instance (inject_common_to_prompt,
            build_context_injection).
        meta_cognition: MetaCognition instance (get_system_prompt_injection).
        messages: Current conversation message list.
        session_cache_hits: Number of file cache hits this session.
        dementia_detector: DementiaDetector instance (diagnose).
        project_index: Project file index dict or None.
        tool_call_count: R8：当前会话已执行的工具调用总数;超过阈值时注入 sub_agent 推荐。

    Returns:
        Complete system prompt string.
    """
    extras: list[str] = []
    bm = behavior

    memory_text = layered_memory.inject_common_to_prompt()
    if memory_text:
        extras.append("\n\n" + memory_text)

    meta_text = meta_cognition.get_system_prompt_injection()
    if meta_text:
        extras.append("\n\n" + meta_text)

    if bm.hallucination_risk > 0.3:
        extras.append(
            "\n⚠ 行为警告: 你近期幻觉风险较高({:.0%})。回答代码问题时必须先调用工具读取文件，绝对不能凭记忆猜测代码内容。一般性问题可以直接回答。".format(bm.hallucination_risk)
        )

    if bm.frustration_rate > 0.2:
        extras.append(
            "\n⚠ 用户状态: 用户近期频繁表现出沮丧({:.0%})。请格外仔细，回答代码问题前先读文件。".format(bm.frustration_rate)
        )

    if bm.tool_error_rate > 0.3:
        extras.append(
            "\n⚠ 工具问题: 近期工具调用失败率较高({:.0%})。请检查参数格式，确保文件路径正确。".format(bm.tool_error_rate)
        )

    if bm.corrections_received >= 2:
        extras.append(
            "\n⚠ 纠正记录: 已收到 {} 次用户纠正。请更加谨慎，确认信息准确后再回答。".format(bm.corrections_received)
        )

    if bm.total_turns > 2 and bm.tool_use_rate < 0.2:
        extras.append(
            "\n💡 提醒: 你近期工具使用率较低({:.0%})。面对代码相关问题请积极使用工具。".format(bm.tool_use_rate)
        )

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

    if session_cache_hits > 2:
        extras.append(
            f"\n📂 文件缓存: 本次会话已命中 {session_cache_hits} 次。"
            "已读文件不需要重复读取。"
        )

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
                extras.append(
                    "\n📚 已学经验:\n" + "\n".join(rule_lines)
                )
        all_result = kb.get_all_rules(limit=3)
        if all_result.is_ok and all_result.data:
            existing_descs = {r.description for r in (result.data or [])}
            general_lines = [
                f"  - {r.description} (置信度={r.confidence:.0%})"
                for r in all_result.data
                if r.confidence > 0.7 and r.description not in existing_descs
            ]
            if general_lines:
                extras.append(
                    "\n📚 通用经验:\n" + "\n".join(general_lines)
                )
        kb.close()
    except Exception as e:
        logger.warning("knowledge base close failed: %s", e)

    try:
        current_query = messages[-1] if messages else ""
        experience_text = layered_memory.build_context_injection(
            current_query=current_query,
        )
        if experience_text and len(experience_text) > 50:
            extras.append("\n\n" + experience_text)
    except Exception as e:
        logger.warning("layered memory build_context_injection failed: %s", e)

    diagnosis = dementia_detector.diagnose()
    if diagnosis.intervention_prompt:
        extras.append("\n\n" + diagnosis.intervention_prompt)

    # R8: 大任务优先 sub_agent（治本：docs/SYSTEMS_THEORY_SYNTHESIS §一.4 token 战）。
    # 触发条件由实例属性 auto_sub_agent_threshold 控制（默认 5 tool calls）。
    # threshold=0 = 显式禁用（不同于"未配置时回落到 5"）。
    # 不强制（模型仍有自由裁量）——只是建议,避免"长任务靠堆轮次"的反模式。
    try:
        threshold = int(
            getattr(behavior, "auto_sub_agent_threshold", 5) if behavior is not None else 5
        )
        current_calls = tool_call_count  # R8：直接从参数取,避免双重真实源混淆
        if threshold > 0 and current_calls >= threshold:
            extras.append(
                f"\n\n💡 R8 提示:当前会话已执行 {current_calls} 次工具调用"
                f"(阈值 {threshold})。**大型探索/搜索/审查任务**建议拆给 sub_agent:"
                f"\n  sub_agent(task=\"<具体子目标>\", max_rounds=10, provider=\"inprocess\")"
                f"\n 拆完后主会话继续,主代理轮次不被探索工作占用"
                f"（详见 docs/SYSTEMS_THEORY_SYNTHESIS.md §一.4）"
            )
    except Exception:  # noqa: BLE001 — 提示注入失败不影响主 prompt
        pass

    if project_index:
        pkg_summary = "\n".join(
            f"- {pkg}/: {', '.join(sorted(files[:5]))}"
            for pkg, files in sorted(project_index.items())
            if pkg != "."
        )
        if pkg_summary:
            extras.append(
                "\n📁 当前项目结构:\n" + pkg_summary
            )

    return _BASE_PROMPT + _build_session_context() + "".join(extras)
