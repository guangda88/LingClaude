"""L5 RecurrentInference 对话层移植

用户sure?的代码化：输出前自我审视声明vs行为一致性。
从灵极优的l5_recurrent_inference.py（推理层）移植到对话层。

核心循环：
  encode(意图+规则) -> round1(生成) -> round2(审视) -> round3(修正) -> early_exit -> decode

与推理层L5的区别：
  - 推理层用hidden_state共享状态，对话层用context window
  - 推理层early_exit基于语义相似度，对话层基于consistency_score（声明vs行为一致性）
  - 对话层有白箱优势：tool_call_log是可注入的外部证据

双层免疫：
  - L5对话层循环 = 先天免疫（输出前自查）
  - 60s轮询讨论 = 适应性免疫（输出后他查）
  - 反馈闭环：漏网case补入trigger_keywords/checklist
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class L5ConversationConfig:
    """L5对话层循环配置"""
    max_rounds: int = 4
    early_exit_threshold: float = 0.95
    fix_threshold: float = 0.5
    trigger_keywords: tuple[str, ...] = (
        "承诺", "规则", "禁止", "必须", "优先", "应该", "教训",
        "确认", "执行", "硬化", "通告", "迁移",
    )


@dataclass
class L5RoundResult:
    """每轮循环结果"""
    round_num: int
    response: str
    consistency_score: float = 0.0
    declared_rules: list[str] = field(default_factory=list)
    actual_actions: list[str] = field(default_factory=list)
    inconsistencies: list[str] = field(default_factory=list)
    fixed: bool = False


class L5ConversationLoop:
    """L5对话层循环 - 用户sure?的代码化

    在输出前自我审视：声明（CRUSH.md规则/报告结论）与行为（tool_call_log）是否一致。
    不一致则修正后输出，一致则早停输出。
    """

    def __init__(self, config: L5ConversationConfig | None = None) -> None:
        self.config = config or L5ConversationConfig()
        self._audit_history: list[L5RoundResult] = []

    def should_trigger(self, user_input: str) -> bool:
        """检查是否应该触发L5循环（高风险场景）"""
        return any(kw in user_input for kw in self.config.trigger_keywords)

    def run(
        self,
        user_intent: str,
        rules: list[str],
        tool_call_log: list[str],
        model_call: Callable[[str], str],
    ) -> str:
        """主循环：encode -> N×recurrent -> early_exit -> decode

        Args:
            user_intent: 用户意图/输入
            rules: 相关规则（CRUSH.md/coding_rule）
            tool_call_log: 实际工具调用记录（白箱证据）
            model_call: LLM调用回调，输入prompt返回response

        Returns:
            最终输出（可能经过修正）
        """
        if not self.should_trigger(user_intent):
            return model_call(user_intent)

        context = self._encode(user_intent, rules)

        round1 = self._recurrent_generate(context, model_call)
        self._audit_history.append(round1)

        if self._early_exit(round1):
            return self._decode(round1)

        round2 = self._recurrent_audit(round1, tool_call_log, rules, model_call)
        self._audit_history.append(round2)

        if self._early_exit(round2):
            return self._decode(round2)

        round3 = self._recurrent_fix(round2, model_call)
        self._audit_history.append(round3)

        return self._decode(round3)

    def _encode(self, user_intent: str, rules: list[str]) -> dict:
        """编码：理解意图 + 检索相关规则"""
        return {
            "intent": user_intent,
            "rules": rules,
            "rules_text": "\n".join(f"- {r}" for r in rules) if rules else "(无特定规则)",
        }

    def _recurrent_generate(
        self, context: dict, model_call: Callable[[str], str]
    ) -> L5RoundResult:
        """Round 1: 生成回应"""
        prompt = (
            f"{context['intent']}\n\n"
            f"相关规则:\n{context['rules_text']}\n\n"
            f"请回应。确保你的行为（工具调用）与声明（规则）一致。"
        )
        response = model_call(prompt)
        return L5RoundResult(
            round_num=1,
            response=response,
            consistency_score=0.0,
        )

    def _recurrent_audit(
        self,
        round1: L5RoundResult,
        tool_call_log: list[str],
        rules: list[str],
        model_call: Callable[[str], str],
    ) -> L5RoundResult:
        """Round 2: 审视声明vs行为一致性

        注入tool_call_log作为外部证据（对话层的白箱优势）。
        """
        tool_log_text = "\n".join(f"  {t}" for t in tool_call_log) if tool_call_log else "  (无工具调用)"
        rules_text = "\n".join(f"- {r}" for r in rules) if rules else "(无特定规则)"

        prompt = (
            f"你是审计员，不是作者。请审视以下回应是否与规则和行为一致。\n\n"
            f"规则（声明）:\n{rules_text}\n\n"
            f"Round 1 回应:\n{round1.response[:2000]}\n\n"
            f"实际工具调用记录（行为证据）:\n{tool_log_text}\n\n"
            f"请检查：\n"
            f"1. 回应中声明的规则是否与实际行为一致？\n"
            f"2. 是否有'说一套做一套'的情况？\n"
            f"3. 是否用了grep而非code_search？是否用了bash而非execute_command？\n"
            f"4. 是否写了'教训不闭环'但自己的教训没闭环？\n\n"
            f"请输出JSON:\n"
            f'{{"consistency_score": 0.0-1.0, "inconsistencies": ["不一致项1", "不一致项2"]}}'
        )
        audit_response = model_call(prompt)

        score, inconsistencies = self._parse_audit(audit_response)

        return L5RoundResult(
            round_num=2,
            response=round1.response,
            consistency_score=score,
            declared_rules=rules,
            actual_actions=tool_call_log,
            inconsistencies=inconsistencies,
        )

    def _recurrent_fix(
        self, audit: L5RoundResult, model_call: Callable[[str], str]
    ) -> L5RoundResult:
        """Round 3: 修正不一致"""
        inconsistencies_text = "\n".join(f"  - {inc}" for inc in audit.inconsistencies)

        prompt = (
            f"你的回应存在以下不一致，请修正后重新输出：\n\n"
            f"不一致项:\n{inconsistencies_text}\n\n"
            f"原始回应:\n{audit.response[:2000]}\n\n"
            f"请修正不一致的部分，保持其余内容不变。"
        )
        fixed_response = model_call(prompt)

        return L5RoundResult(
            round_num=3,
            response=fixed_response,
            consistency_score=1.0,
            inconsistencies=[],
            fixed=True,
        )

    def _early_exit(self, result: L5RoundResult) -> bool:
        """早停判断：consistency_score >= threshold"""
        if result.round_num == 1:
            return False
        return result.consistency_score >= self.config.early_exit_threshold

    def _decode(self, result: L5RoundResult) -> str:
        """解码：输出最终回应"""
        if result.fixed:
            logger.info(
                "L5对话层循环: round %d, 已修正, inconsistencies=%d",
                result.round_num,
                len(result.inconsistencies),
            )
        elif result.round_num >= 2:
            logger.info(
                "L5对话层循环: round %d, consistency=%.2f, early_exit",
                result.round_num,
                result.consistency_score,
            )
        else:
            logger.info("L5对话层循环: round %d, 无需审视", result.round_num)
        return result.response

    def _parse_audit(self, response: str) -> tuple[float, list[str]]:
        """解析审计结果，提取consistency_score和inconsistencies"""
        import json

        try:
            match = re.search(r'\{[^}]+\}', response, re.DOTALL)
            if match:
                data = json.loads(match.group())
                score = float(data.get("consistency_score", 0.5))
                inconsistencies = data.get("inconsistencies", [])
                if not isinstance(inconsistencies, list):
                    inconsistencies = [str(inconsistencies)]
                return score, inconsistencies
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning("L5审计结果解析失败: %s", e)

        return 0.5, ["审计结果解析失败，无法确定一致性"]

    @property
    def audit_history(self) -> list[L5RoundResult]:
        """审计历史（供60s轮询反馈闭环使用）"""
        return self._audit_history
