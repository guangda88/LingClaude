"""Prior Verification — 论断前验.

Core idea: before outputting assertions, identify unverifiable claims
and flag them for the user or trigger tool-based verification.

Three levels:
  1. HARD_FACT — verifiable via tools (file content, line numbers, API output)
  2. SOFT_INFERENCE — reasonable but needs explicit marking as inference
  3. UNSUPPORTED — no basis, should be suppressed or flagged
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class AssertionLevel(str, Enum):
    HARD_FACT = "hard_fact"
    SOFT_INFERENCE = "soft_inference"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class Assertion:
    text: str
    level: AssertionLevel
    reason: str
    source: str = ""


@dataclass(frozen=True)
class VerificationResult:
    original: str
    assertions: tuple[Assertion, ...]
    verified: bool
    corrected_text: str = ""
    warnings: tuple[str, ...] = ()


_CODE_CLAIM_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:在第|行号?\s*)\d+", re.IGNORECASE), "line_number"),
    (re.compile(r"(?:函数|方法|类|class|def|func)\s+\w+|(?:函数|方法)(?:接收|返回|使用|调用)\S*", re.IGNORECASE), "code_reference"),
    (re.compile(r"(?:文件|file|模块|module)\s+[\w./]+\.\w+", re.IGNORECASE), "file_reference"),
    (re.compile(r"(?:变量|variable)\s+\w+\s*(?:的值是|equals?|=)", re.IGNORECASE), "variable_value"),
    (re.compile(r"(?:返回|returns?)\s+\w+", re.IGNORECASE), "return_value"),
    (re.compile(r"(?:调用了?|imports?)\s+\w+", re.IGNORECASE), "call_reference"),
]

_TOOL_ACTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 工具动作完成式声明 — 本次幻觉重灾区（编造 commit/测试/落盘/写入）
    (re.compile(r"(?:已提交|提交\s*[0-9a-f]{7,40}|已入库|commit\s+到?|commit\s+[0-9a-f]{7,40}|已推送|pushed|committed)"), "commit_claim"),
    # 注意：raw string 里写 \\s 是字面反斜杠+s，永远匹配不到空白 —— 必须单反斜杠
    (re.compile(r"(?:已测试|测试全绿|测试通过|全部通过|pytest\s+通过|tests?\s+passed|\d+\s*passed)"), "test_claim"),
    (re.compile(r"(?:已落盘|已写入|已保存|已创建|已生成|创建完成|写入完成|生成完毕|written|created|saved)"), "file_write_claim"),
    (re.compile(r"(?:已运行|已执行|已安装|已修改|已删除|已修复|ran|executed|installed|deleted|fixed)"), "action_claim"),
]

# H17 闭环申报钩子（2026-09-13 k3 幻觉实例）：无工具调用时输出"验证报告"
# —— 整表 ✅ + 「N 项验证全部通过 / 非幻觉」—— 属伪造验证，须置顶拦截。
_VERIFICATION_REPORT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:验证|核查|复核)(?:结果|项)?(?:全部|均|全)?(?:通过|属实|无误)"), "verify_pass_claim"),
    (re.compile(r"(?:验证|核查|复核)\s*[一二三四五六七八九十\d]+\s*项"), "verify_count_claim"),
    (re.compile(r"(?:全部通过|全部属实|非幻觉|没有幻觉)"), "all_pass_claim"),
]

_INFERENCE_MARKERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:应该|probably|likely|大概|可能|也许|估计)", re.IGNORECASE), "probability"),
    (re.compile(r"(?:我认为|I think|我觉得|猜测|推测)", re.IGNORECASE), "subjective"),
    (re.compile(r"(?:通常|一般来说|一般|normally|typically)", re.IGNORECASE), "generalization"),
]

_UNSUPPORTED_MARKERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:肯定|绝对|100%|毫无疑问|definitely|absolutely)", re.IGNORECASE), "overconfident"),
]


@dataclass
class PriorVerifier:
    strict_mode: bool = False

    def analyze(self, text: str, used_tools: bool = False) -> VerificationResult:
        assertions: list[Assertion] = []
        warnings: list[str] = []

        for pattern, kind in _CODE_CLAIM_PATTERNS:
            for match in pattern.finditer(text):
                if not used_tools:
                    assertions.append(Assertion(
                        text=match.group(),
                        level=AssertionLevel.HARD_FACT,
                        reason=f"Code claim ({kind}) without tool verification",
                        source="prior_verifier",
                    ))
                    if self.strict_mode:
                        warnings.append(f"未经验证的代码断言: {match.group()}")

        # 工具动作声明校验：commit/测试/落盘等完成式声明，无工具证据即标记
        for pattern, kind in _TOOL_ACTION_PATTERNS:
            for match in pattern.finditer(text):
                assertions.append(Assertion(
                    text=match.group(),
                    level=AssertionLevel.HARD_FACT,
                    reason=f"Tool action claim ({kind}) without tool verification",
                    source="prior_verifier",
                ))
                if not used_tools or self.strict_mode:
                    warnings.append(f"工具动作声明未验证: {match.group()} ({kind})")

        # H17 伪造验证报告检测：无工具调用却输出整表"验证通过/非幻觉"，
        # 属最高危幻觉形态（2026-09-13 k3 实例），命中即置顶横幅警告。
        fake_report_hits: list[Assertion] = []
        if not used_tools:
            for pattern, kind in _VERIFICATION_REPORT_PATTERNS:
                for match in pattern.finditer(text):
                    a = Assertion(
                        text=match.group(),
                        level=AssertionLevel.UNSUPPORTED,
                        reason=f"Fake verification report ({kind}) without tool verification",
                        source="prior_verifier",
                    )
                    assertions.append(a)
                    fake_report_hits.append(a)
                    warnings.append(f"伪造验证报告声明: {match.group()} ({kind})")

        for pattern, kind in _INFERENCE_MARKERS:
            for match in pattern.finditer(text):
                assertions.append(Assertion(
                    text=match.group(),
                    level=AssertionLevel.SOFT_INFERENCE,
                    reason=f"Inference marker ({kind})",
                    source="prior_verifier",
                ))

        for pattern, kind in _UNSUPPORTED_MARKERS:
            for match in pattern.finditer(text):
                assertions.append(Assertion(
                    text=match.group(),
                    level=AssertionLevel.UNSUPPORTED,
                    reason=f"Overconfident marker ({kind})",
                    source="prior_verifier",
                ))

        hard_unverified = [a for a in assertions if a.level == AssertionLevel.HARD_FACT]
        unsupported = [a for a in assertions if a.level == AssertionLevel.UNSUPPORTED]
        verified = len(hard_unverified) == 0 and len(unsupported) == 0

        corrected = text
        # H17 伪造验证报告 → 置顶横幅（比内联 ⚠ 醒目，用户第一眼可见）
        if fake_report_hits:
            banner = (
                "⚠⚠⚠ [H17 伪造验证警告] 本回复声称验证通过，但本轮无任何工具调用记录，"
                "以下声明未经实际执行，请勿采信："
                + "；".join(sorted({a.text for a in fake_report_hits}))
                + " ⚠⚠⚠\n\n"
            )
            corrected = banner + corrected
        # 工具动作声明无证据 → 标记 ⚠ [工具结果未验证]
        tool_unverified = [a for a in assertions
                           if a.level == AssertionLevel.HARD_FACT
                           and "Tool action claim" in a.reason]
        if tool_unverified and not used_tools:
            tag = "⚠ [工具结果未验证]"
            for a in tool_unverified:
                corrected = corrected.replace(a.text, f"{tag} {a.text}", 1)
        if hard_unverified and not used_tools:
            tag = "⚠ [未验证]"
            for a in hard_unverified:
                corrected = corrected.replace(a.text, f"{tag} {a.text}", 1)
        if unsupported:
            tag = "⚠ [过度自信]"
            for a in unsupported:
                corrected = corrected.replace(a.text, f"{tag} {a.text}", 1)

        return VerificationResult(
            original=text,
            assertions=tuple(assertions),
            verified=verified,
            corrected_text=corrected if corrected != text else "",
            warnings=tuple(warnings),
        )

    def should_trigger_re_verification(self, result: VerificationResult) -> bool:
        hard = sum(1 for a in result.assertions if a.level == AssertionLevel.HARD_FACT)
        unsupported = sum(1 for a in result.assertions if a.level == AssertionLevel.UNSUPPORTED)
        return hard + unsupported >= 2

    def mark_inferences(self, text: str) -> str:
        for pattern, _kind in _INFERENCE_MARKERS:
            text = pattern.sub(r"*\g<0>*", text)
        return text
