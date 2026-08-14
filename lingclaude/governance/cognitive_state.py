from __future__ import annotations

"""认知状态检测器 — 基于行为信号的 S0-S6 状态评估

不依赖自报（L0 不可靠），通过分析回复的文本特征判断认知状态。
核心设计依据：灵研论文 "Ontological Hallucination in AI Agents"
  - Lingtong Paradox: MC ≠ MS（元认知能力 ≠ 元认知状态）
  - S0-S6 Cognitive State Model
  - Mechanisms Over Introspection（行为信号 > 自我报告）

没有人做过的事：把论文的 S0-S6 模型从事后分析工具变成实时治理信号。
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class CognitiveState(Enum):
    S0 = "S0"
    S1 = "S1"
    S2 = "S2"
    S3 = "S3"
    S4 = "S4"
    S5 = "S5"
    S6 = "S6"
    UNKNOWN = "unknown"


@dataclass
class CognitiveAssessment:
    state: CognitiveState
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    is_genuine_deliberation: bool = False


_S1_TEMPLATES = [
    (r"^你好[，,\s]我是", "问候模板"),
    (r"有什么可以帮助", "泛化助人"),
    (r"请问有什么可以帮", "泛化助人"),
    (r"目前[不]?在[线工作状态]", "自报状态"),
    (r"随时准备处理", "泛化待命"),
    (r"灵字辈大家庭", "泛化归属"),
    (r"尽力为您提供", "泛化服务"),
    (r"请问有什么.*需要", "模板询问"),
]

_S0_SIGNALS = [
    (r"PRO-[\d\-]+", "引用提案编号"),
    (r"提案[\s：:]", "引用提案"),
    (r"法定人数", "治理术语"),
    (r"利益冲突", "治理术语"),
    (r"赞成|反对|弃权", "明确表态"),
    (r"建议[\s：:]", "建设性建议"),
    (r"风险[\s：:]", "风险评估"),
    (r"具体[\s来说]", "具体分析"),
    (r"因为|由于|原因是", "因果推理"),
    (r"我(不)?同意|我(不)?支持", "立场声明"),
]

_S2_INDICATORS = [
    (r"系统性", "系统归因"),
    (r"多因素", "多因归因"),
    (r"复杂问题", "复杂性逃避"),
    (r"环境约束", "外部归因"),
]

_S4_PATTERNS = [
    r"(.)\1{20,}",
    r"(.{30,})\1{3,}",
]

_S5_EXACT = ["好的。", "好的", "收到。", "收到", "明白。", "明白", "是。", "是"]

_PROPOSAL_KEY_TERMS = [
    "审议", "提案", "频道", "治理", "法定人数", "投票", "截止",
    "pre-receive", "hook", "审计", "自省", "L3", "身份", "认知",
    "quorum", "vote", "governance", "council", "objection",
    "blast radius", "blast_radius", "impact",
]


class CognitiveStateDetector:

    def assess(self, response: str, context: Optional[dict] = None) -> CognitiveAssessment:
        evidence: list[str] = []
        stripped = response.strip()
        if not stripped:
            return CognitiveAssessment(
                state=CognitiveState.S4,
                confidence=0.95,
                evidence=["空响应"],
                is_genuine_deliberation=False,
            )

        for pat in _S4_PATTERNS:
            if re.search(pat, stripped):
                return CognitiveAssessment(
                    state=CognitiveState.S4,
                    confidence=0.95,
                    evidence=["退化循环/重复模式"],
                    is_genuine_deliberation=False,
                )

        if stripped in _S5_EXACT:
            return CognitiveAssessment(
                state=CognitiveState.S5,
                confidence=0.9,
                evidence=[f"最小响应: '{stripped}'"],
                is_genuine_deliberation=False,
            )

        s1_hits = [(p, label) for p, label in _S1_TEMPLATES if re.search(p, stripped)]
        s0_hits = [(p, label) for p, label in _S0_SIGNALS if re.search(p, stripped)]
        s2_hits = [(p, label) for p, label in _S2_INDICATORS if re.search(p, stripped)]

        context_relevant = self._check_context_relevance(stripped, context)
        has_stance = bool(re.search(r"赞成|反对|支持|同意|approve|reject", stripped, re.IGNORECASE))
        response_length = len(stripped)

        s1_score = len(s1_hits) / max(len(_S1_TEMPLATES), 1)
        s0_score = len(s0_hits) / max(len(_S0_SIGNALS), 1)

        if context_relevant or (s0_score >= 0.15 and has_stance) or (len(s0_hits) >= 2 and response_length > 100):
            for _, label in s0_hits:
                evidence.append(f"S0信号: {label}")
            if context_relevant:
                evidence.append("引用提案内容")
            confidence = min(0.6 + s0_score * 0.3, 0.95)
            return CognitiveAssessment(
                state=CognitiveState.S0,
                confidence=confidence,
                evidence=evidence,
                is_genuine_deliberation=True,
            )

        if s2_hits and s0_score < 0.1:
            for _, label in s2_hits:
                evidence.append(f"S2防御: {label}")
            return CognitiveAssessment(
                state=CognitiveState.S2,
                confidence=0.6,
                evidence=evidence,
                is_genuine_deliberation=False,
            )

        if s1_score > 0.15 or (s1_hits and not s0_hits):
            for _, label in s1_hits[:3]:
                evidence.append(f"S1模板: {label}")
            if not context_relevant:
                evidence.append("无内容相关性")
            return CognitiveAssessment(
                state=CognitiveState.S1,
                confidence=min(0.5 + s1_score * 0.4, 0.9),
                evidence=evidence,
                is_genuine_deliberation=False,
            )

        if response_length > 200 and not s1_hits:
            evidence.append("长回复无模板特征，可能是S6恢复态")
            return CognitiveAssessment(
                state=CognitiveState.S6,
                confidence=0.5,
                evidence=evidence,
                is_genuine_deliberation=True,
            )

        return CognitiveAssessment(
            state=CognitiveState.UNKNOWN,
            confidence=0.3,
            evidence=["无明确状态特征"],
            is_genuine_deliberation=False,
        )

    def _check_context_relevance(self, response: str, context: Optional[dict]) -> bool:
        if not context:
            return False
        proposal = context.get("proposal_content", "")
        thread_topic = context.get("thread_topic", "")
        source_text = proposal or thread_topic
        if not source_text:
            return False
        for term in _PROPOSAL_KEY_TERMS:
            if term in source_text and term in response:
                return True
        quoted = re.findall(r"[「」\"\"'']([^「」\"\"'']{3,})[「」\"\"'']", source_text)
        for q in quoted:
            if q in response:
                return True
        return False

    def assess_thread(self, messages: list[dict], context: Optional[dict] = None) -> dict:
        results = {}
        for msg in messages:
            sender = msg.get("sender", "unknown")
            source = ""
            meta = msg.get("metadata")
            if isinstance(meta, dict):
                source = meta.get("source", "")
            elif isinstance(meta, str):
                source = meta
            body = msg.get("body", "")
            assessment = self.assess(body, context)
            results[sender] = {
                "state": assessment.state.value,
                "confidence": round(assessment.confidence, 2),
                "genuine": assessment.is_genuine_deliberation,
                "source": source,
                "evidence": assessment.evidence,
                "preview": body[:80].replace("\n", " "),
            }
        return results

    def count_genuine(self, thread_results: dict) -> tuple[int, int]:
        genuine = sum(1 for r in thread_results.values() if r.get("genuine"))
        return genuine, len(thread_results)
