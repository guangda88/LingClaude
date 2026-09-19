"""幻觉治理守卫 - 在任务结果输出前强制验证

T9 (2026-09-14): 修复部署配置缺陷 —— 原实现引用不存在的
`lingresearch.response_harvester`（全仓 0 命中），导致 _HARVESTER_AVAILABLE
恒为 False、验证永远降级 fail-soft（守卫形同虚设，只记一条
"harvester_unavailable" 日志）。改为复用本仓真实存在的
`lingclaude.core.prior_verifier.PriorVerifier`（已有 50+ 测试覆盖），
使守卫在无外部依赖的情况下真正可验证、可拦截。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 内部验证器：复用 PriorVerifier（本仓自包含，无外部依赖）。
# S3 倒装纪律：core 内不模块级 import（LAZY 基线），改为函数内延迟 import。


def validate_task_result(
    result_message: str,
    task_context: Optional[Dict[str, Any]] = None,
    strict: bool = False,
    used_tools: bool = False,
    tool_evidence: tuple = (),
    tool_results: Optional[Dict[str, bool]] = None,
) -> Dict[str, Any]:
    """
    验证任务结果，检测潜在幻觉

    Args:
        result_message: 任务结果消息文本
        task_context: 任务上下文（可选，含 prompt / session_id）
        strict: 严格模式，验证失败时抛出异常

    Returns:
        {
            "validated": bool,           # 是否通过验证
            "has_hallucination": bool,   # 是否检测到幻觉
            "confidence": float,         # 置信度 0-1
            "issues": list[str],         # 发现的问题列表
            "verified_message": str,     # 验证后的消息（可能带标记）
            "original_message": str,     # 原始消息
        }
    """
    result = {
        "validated": False,
        "has_hallucination": False,
        "confidence": 1.0,
        "issues": [],
        "verified_message": result_message,
        "original_message": result_message,
    }

    # 如果消息为空，跳过验证
    if not result_message or not result_message.strip():
        return result

    try:
        # 用本仓 PriorVerifier 做断言提取 + 幻觉标记（函数内延迟 import，S3 纪律）
        # P1-3 (2026-09-19): 透传当轮证据 —— 原实现写死 used_tools=False，
        # journal 里的真实工具证据被丢弃，「调了工具且声明有据」也会被误判
        # 幻觉，cross-reference 闭环在流式路径断开。三参全部透传，
        # 调用方（query_engine_turn_mixin）已从 journal 取好当轮证据。
        from lingclaude.core.prior_verifier import PriorVerifier

        pv = PriorVerifier()
        vr = pv.analyze(
            result_message,
            used_tools=used_tools,
            tool_evidence=tool_evidence,
            tool_results=tool_results,
        )

        hard_facts = [a for a in vr.assertions if a.level.value == "hard_fact"]
        unsupported = [a for a in vr.assertions if a.level.value == "unsupported"]

        result["validated"] = True
        # 有 HARD_FACT（工具动作/代码声明无证据）或 UNSUPPORTED（过度自信/伪造报告）
        # 即视为潜在幻觉
        if hard_facts or unsupported:
            result["has_hallucination"] = True
            result["confidence"] = 0.3
            result["issues"].extend(
                f"{a.level.value}: {a.text}" for a in (hard_facts + unsupported)
            )
        else:
            result["confidence"] = 1.0

        # 验证后的消息 = PriorVerifier 打标后的文本（含 ⚠ 标记）
        result["verified_message"] = vr.corrected_text or result_message

        # 严格模式下，检测到幻觉时抛出异常
        if strict and result["has_hallucination"]:
            raise HallucinationDetectedError(
                f"检测到 {len(result['issues'])} 个未验证断言: {result['issues']}"
            )

    except HallucinationDetectedError:
        raise
    except Exception as e:
        logger.error(f"hallucination_guard: 验证失败: {e}")
        result["issues"].append(f"validation_error: {str(e)}")
        if strict:
            raise

    return result


class HallucinationDetectedError(Exception):
    """检测到幻觉时抛出的异常"""

    pass


def should_validate(task_name: str, result_message: str) -> bool:
    """
    判断是否需要验证

    策略：
    1. 包含特定关键词（成功、失败、已修复、推送成功等）
    2. 任务名称匹配特定模式
    3. 消息长度超过阈值
    """
    if not result_message:
        return False

    # 关键词触发
    # T10 (2026-09-14): 补充高频幻觉场景关键词 —— "测试通过/全部通过/已部署/
    # 部署完成/已创建/已写入"等完成式声明是幻觉重灾区，原列表只覆盖
    # 成功/失败/commit/push/merge，导致"测试全部通过""部署完成"漏触发。
    trigger_keywords = [
        "成功", "失败", "已修复", "已解决", "推送成功",
        "exit=0", "exit 0", "✅", "❌",
        "commit", "push", "merge",
        # T10 补充：完成式声明高频词（与 PriorVerifier 断言模式对齐）
        "通过", "已推送", "已部署", "部署完成", "已创建", "已写入",
        "已落盘", "已生成", "已安装", "已提交", "测试通过",
    ]

    message_lower = result_message.lower()
    for keyword in trigger_keywords:
        if keyword.lower() in message_lower:
            return True

    # 任务名称触发
    # T10 (2026-09-14): 补充中文任务名关键词 —— "部署/推送/提交/发布"，
    # 原列表只有 push/deploy/fix/repair（英文），中文任务名漏触发。
    if any(kw in task_name.lower() for kw in
           ["push", "deploy", "fix", "repair", "部署", "推送", "提交", "发布"]):
        return True

    return False
