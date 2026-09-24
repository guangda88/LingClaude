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
    "11. 引用文件路径前必须实测存在，禁止凭记忆或他人汇报转述。\n"
    "12. 外部知识断言（参数量/定价/限额/API 契约/版本号等训练数据内记忆）不得直接断言，"
    "必须先经 web_search/web_fetch 工具验证；无法验证时明确说「未验证」，禁止编造具体数字。\n"
    "13. 涉及模型/端点清单的问题，只引用工具返回的实时清单；未知或清单外的模型一律回答"
    " NOT_FOUND，禁止顺着记忆猜测补全。\n"
    # P0-3（2026-09-21，全 15 家精读 §3.2 cc 式缓存边界行）：显式动态边界标记——
    # 本行之前是字节级冻结的前缀（provider 前缀缓存命中区），之后全部是动态内容
    # （SESSION_CONTEXT/extras，由 build_dynamic_system_suffix 承载 tail-append）。
    # resume/compact 时前缀字节不变当 CI 断言（assert_prefix_stable）。
    "__DYNAMIC_BOUNDARY__"
)

# P0-3: 前缀冻结断言用的边界行常量（与 _BASE_PROMPT 末行一致，单源防漂移）。
DYNAMIC_BOUNDARY_MARKER = "__DYNAMIC_BOUNDARY__"


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
    current_query: str = "",  # R9: 当前 turn 的用户 query，用于首 turn 拆解判定
    model_switch_note: dict[str, Any] | None = None,  # P1-4: 模型切换声明注入
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
        current_query: R9：当前 turn 的用户输入原文；命中长任务判据时首 turn 即注入拆解建议。

    Returns:
        Complete system prompt string.
    """
    # 2026-09-21 (前缀缓存优化 P0-2): 动态段（SESSION_CONTEXT + extras）不再拼入
    # system prompt —— 它们每轮变化（git 状态/行为指标/工具计数/当前 query），
    # 混在前缀里使 provider 前缀缓存每轮 miss（GLM cached_tokens 价为标准价 29%）。
    # 现拆为两部分：
    #   - system prompt = _BASE_PROMPT（纯静态，会话内字节级稳定 → 缓存命中）；
    #   - 动态段 = 独立 system 尾随消息（_build_dynamic_system_suffix），由
    #     _build_messages 以「tail-append」方式放在历史之后、本轮 user 之前——
    #     只破坏一次尾部缓存，前缀（BASE+历史）保持可命中。
    # P0-3（2026-09-21）：_BASE_PROMPT 末尾带显式 __DYNAMIC_BOUNDARY__ 边界行——
    # 边界行之前为冻结前缀，build_adaptive_system_prompt 恒定只返回 _BASE_PROMPT
    # （含边界行），动态内容全压在 build_dynamic_system_suffix 尾部，前缀字节
    # 永不随动态内容变化。
    return _BASE_PROMPT


def assert_prefix_stable() -> None:
    """P0-3（2026-09-21）：前缀字节稳定 CI 断言。

    校验两点（违反任一即测试红）：
      1. `build_adaptive_system_prompt` 返回体恒等于 `_BASE_PROMPT`（不随
         任何入参变化）——动态内容不得渗入前缀；
      2. 返回体以 `__DYNAMIC_BOUNDARY__` 边界行结尾——边界行之后才是动态段
         （build_dynamic_system_suffix 承载），保证 provider 前缀缓存命中区
         与动态区的显式分界。

    供 CI / 单测调用：`assert_prefix_stable()` 无参，断言失败即抛 AssertionError。
    """
    # 1) 返回体对任意入参字节级一致（冻结前缀）
    def _mk(**over: Any) -> str:
        base = dict(
            behavior=_FakeBM(), layered_memory=_FakeLM(), meta_cognition=_FakeMeta(),
            messages=["m"], session_cache_hits=0, dementia_detector=_FakeDD(),
            project_index=None, tool_call_count=0,
        )
        base.update(over)
        return build_adaptive_system_prompt(**base)

    a = _mk()
    b = _mk(current_query="完全不同的动态 query", tool_call_count=999)
    c = _mk(current_query="x", tool_call_count=0)
    assert a == b == c, "前缀随动态入参变化（缓存边界被破坏）"
    assert a == _BASE_PROMPT, "build_adaptive_system_prompt 返回体偏离 _BASE_PROMPT"
    # 2) 边界行结尾
    assert _BASE_PROMPT.rstrip().endswith(DYNAMIC_BOUNDARY_MARKER), \
        f"_BASE_PROMPT 必须以 {DYNAMIC_BOUNDARY_MARKER} 结尾（前缀/动态分界）"


# P0-3: 断言用最小 fake（仅服务于 prefix 稳定性校验，全离线）
class _FakeBM:
    hallucination_risk = 0.0
    frustration_rate = 0.0
    tool_error_rate = 0.0
    corrections_received = 0
    total_turns = 0
    tool_use_rate = 0.0
    tool_error_count = 0
    auto_sub_agent_threshold = 0


class _FakeLM:
    def inject_common_to_prompt(self) -> str:
        return ""


class _FakeMeta:
    def get_system_prompt_injection(self) -> str:
        return ""


class _FakeDD:
    def diagnose(self) -> Any:
        class _D:
            dementia_index = 0.0
            intervention_prompt = ""
        return _D()


def build_dynamic_system_suffix(
    behavior: Any,
    layered_memory: Any,
    meta_cognition: Any,
    messages: list[str],
    session_cache_hits: int,
    dementia_detector: Any,
    project_index: dict[str, Any] | None,
    tool_call_count: int = 0,
    current_query: str = "",
    model_switch_note: dict[str, Any] | None = None,
) -> str:
    """动态上下文尾随块（原 build_adaptive_system_prompt 的 SESSION_CONTEXT+extras）。

    2026-09-21 拆分自 build_adaptive_system_prompt（见其 docstring）。语义不变，
    仅改承载位置：作为独立 system 消息 append 在对话尾部，前缀缓存只破尾部一次。
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
        # R9 (2026-09-23): corrections 消费面激活——计数壳升级为真实内容。
        # 库里存的已是用户原话（b7c7283 新格式），注入 = 教训带内容地回炉。
        # 双路取样: 24h 内 top-2（热记忆）+ 全库随机 1 条（冷唤醒防旧错复发）。
        # fail-soft：读取失败只留计数行，绝不阻断 prompt 组装。
        try:
            from lingclaude.core.data_flywheel import DataFlywheel

            fw = DataFlywheel()
            # R10-2 (2026-09-23): 复发感知读取——注入条目带 recurrence_count，
            # 「注入过仍复发」的纠正加 🔁 重点标记（段3 效果可观测的第一步）。
            res = fw.get_recent_corrections_with_recurrence(limit=2)
            lines: list[str] = []
            if res.is_ok and res.data:
                seen_ids = set()
                for c_ in res.data:
                    key = (c_["correction"][:50], c_["original_error"][:50])
                    if key in seen_ids:
                        continue
                    seen_ids.add(key)
                    _rc = int(c_.get("recurrence_count", 0) or 0)
                    _mark = f"🔁{_rc}次 " if _rc > 0 else ""
                    lines.append(
                        "  - {}纠正: {} → 勿重犯: {}".format(
                            _mark,
                            c_["correction"][:80].replace("\n", " "),
                            c_["original_error"][:60].replace("\n", " ") or "（无原文）",
                        )
                    )
            if lines:
                extras.append("\n📚 近期纠正实录（务必避免重犯）:\n" + "\n".join(lines))
        except Exception as e:  # pragma: no cover - fail-soft 兜底
            logger.warning("corrections 注入失败（fail-soft）: %s", e)

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
        keyword = (
            current_query or (messages[-1] if messages else "")
        )[:50]  # F3-3: 检索信号源升级——优先当轮用户输入原文（current_query
        # 由 _build_dynamic_suffix 传入）；messages[-1] 在多轮场景下常为注入的
        # SYSTEM 动态后缀，LIKE 检索会漂移到无关规则。无 current_query 时回退旧行为。
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
        # R9 修复(2026-09-16): 局部变量改用 _last_msg, 不得遮蔽函数参数 current_query
        # （原先 current_query = messages[-1] 会把 R9 判定入参覆盖为 messages 尾元素）
        _last_msg = messages[-1] if messages else ""
        experience_text = layered_memory.build_context_injection(
            current_query=_last_msg,
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
            getattr(behavior, "auto_sub_agent_threshold", 3) if behavior is not None else 3
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

    # R9 (2026-09-16): 长任务首 turn 拆解信号 —— R8 的前置补位。
    # R8 在 tool_call_count >= threshold 时才提示(事后补救); R9 在第 0 次工具调用
    # 时就依据 query 特征(长度+动作词, intelligent_router.is_decomposition_candidate)
    # 提前建议拆解。软引导不强制, 与 R8 同一调用面(sub_agent), 不引入新执行机制。
    if current_query:
        try:
            from lingclaude.model.intelligent_router import is_decomposition_candidate

            if is_decomposition_candidate(current_query):
                extras.append(
                    "\n\n💡 R9 提示:当前任务较长且含多个操作目标。"
                    "**建议先做计划拆解(3-7 个子任务)**, 明确各子任务的验收标准, "
                    "再按序执行; 其中探索/搜索/审查类工作优先拆给 sub_agent:"
                    "\n  sub_agent(task=\"<具体子目标>\", max_rounds=10, provider=\"inprocess\")"
                    "\n 拆解是建议而非强制——若任务实际很短, 直接完成即可。"
                )
        except Exception:  # noqa: BLE001 — 提示注入失败不影响主 prompt
            logger.warning("R9 提示注入失败", exc_info=True)

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

    # P1-4 (2026-09-19, 幻觉调研第二批): 模型切换声明 —— switch/pin 触发后，
    # 下一轮起注入「接管声明」：新模型不得继承前任模型的未验证声明，
    # 必须 explicit 声明当前模型身份、只基于会话内显式信息（工具结果/用户
    # 输入）工作。H3（路由切换上下文断裂）的直接治理。
    if model_switch_note:
        _m = model_switch_note.get("model", "unknown")
        _how = "已钉住" if model_switch_note.get("pinned") else "由降级链切换而来"
        extras.append(
            f"\n\n🔄 模型切换声明: 你刚接管本会话（当前模型: {_m}，{_how}）。"
            "此前对话中其他模型声称过的一切（读过的文件、执行过的命令、得出的结论）"
            "你都无从验证，不得继承、复述或延续。回答代码问题前必须用工具重新读取；"
            f"本次回复开头请显式声明当前模型身份（{_m}）。"
            "若上下文中出现你无法确认的模型名/清单，宁可回答 NOT_FOUND 也不得猜测。"
        )

    # 2026-09-21: 动态段独立成块返回（不含 _BASE_PROMPT——那已在 system 首
    # 消息里冻结）。包含 SESSION_CONTEXT + 全部 extras，语义与拆分前一致。
    return _build_session_context() + "".join(extras)
