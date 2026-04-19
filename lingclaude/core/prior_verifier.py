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
    (re.compile(r"(?:函数|方法|类|class|def|func)\s+\w+", re.IGNORECASE), "code_reference"),
    (re.compile(r"(?:文件|file|模块|module)\s+[\w./]+\.\w+", re.IGNORECASE), "file_reference"),
    (re.compile(r"(?:变量|variable)\s+\w+\s*(?:的值是|equals?|=)", re.IGNORECASE), "variable_value"),
    (re.compile(r"(?:返回|returns?)\s+\w+", re.IGNORECASE), "return_value"),
    (re.compile(r"(?:调用了?|imports?)\s+\w+", re.IGNORECASE), "call_reference"),
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
