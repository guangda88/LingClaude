"""事实校验层 (Fact Checker) — T1

防低质模型幻觉: 模型答完后查灵知 KG 确认每个 claim 有来源。
无来源 claim 标警告或重答。

当前: 骨架 + KG 检索占位
LingBus ready 后: 通过灵知的搜索工具做实时检索
"""

from __future__ import annotations

import logging
import re
import json
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class Claim:
    """从模型输出中提取的事实性声明"""
    text: str
    entity: str = ""
    span: tuple[int, int] = (0, 0)


@dataclass
class FactCheckResult:
    """事实校验结果"""
    claim: Claim
    found: bool = False           # KG 中是否有来源
    confidence: float = 0.0       # 匹配置信度 [0, 1]
    source: str = ""              # 来源 (灵忆 record ID / 灵知 KG 实体名)
    evidence: str = ""            # 检索到的证据文本
    error: str = ""


class ClaimExtractor:
    """从模型输出中提取 claim"""
    
    # 声明关键词模式
    _PATTERNS = [
        r"(?:已|已经)(?:完成|执行|处理|修复|解决|实现)(?:了)?",
        r"(?:使用|采用|调用)(?:了)?\w+",
        r"(?:确认|验证|检查)(?:了)?\w+",
        r"(?:发现|找到|识别)(?:了)?\w+",
        r"(?:不存在|没有|无需)\w*",
        r"\w+(?:搜索|检索|查询)了",
        r"完成(?:了)?\w+",
    ]
    
    @classmethod
    def extract(cls, text: str) -> list[Claim]:
        """提取事实性声明"""
        claims = []
        for pat in cls._PATTERNS:
            for m in re.finditer(pat, text):
                claims.append(Claim(
                    text=m.group(),
                    entity=m.group()[:16],
                    span=(m.start(), m.end()),
                ))
        # 去重
        seen = set()
        unique = []
        for c in claims:
            if c.text not in seen:
                seen.add(c.text)
                unique.append(c)
        return unique


class KGFactChecker:
    """事实校验器 — 通过灵知 KG 检索验证 claim
    
    当前: mock 实现 (直接返回 found=True)
    LingBus 对接后: 调灵知搜索工具做实时检索
    """
    
    def __init__(self, search_fn: Callable[[str], list[dict]] | None = None):
        self._search_fn = search_fn or self._mock_search
    
    def _mock_search(self, query: str) -> list[dict]:
        """占位: 等灵知 LingBus 接口就绪后替换"""
        return [{"id": "mock", "text": f"(mock) 来自知识库: {query}", "confidence": 1.0}]
    
    def check(self, claim: Claim) -> FactCheckResult:
        try:
            results = self._search_fn(claim.text)
            if results and results[0].get("confidence", 0) >= 0.5:
                return FactCheckResult(
                    claim=claim,
                    found=True,
                    confidence=results[0].get("confidence", 1.0),
                    source=results[0].get("id", ""),
                    evidence=results[0].get("text", ""),
                )
            return FactCheckResult(
                claim=claim,
                found=False,
                confidence=results[0].get("confidence", 0) if results else 0.0,
            )
        except Exception as e:
            return FactCheckResult(claim=claim, error=str(e))


def audit_response(
    output: str,
    checker: KGFactChecker | None = None,
    extractor: type[ClaimExtractor] = ClaimExtractor,
) -> dict[str, Any]:
    """对模型输出做事实校验 (L5 round 2 可调)
    
    Returns:
        {"passed": bool, "claims": list[FactCheckResult], "warning": str}
    """
    checker = checker or KGFactChecker()
    claims = extractor.extract(output)
    results = [checker.check(c) for c in claims]
    failed = [r for r in results if not r.found]
    return {
        "passed": len(failed) == 0,
        "total": len(results),
        "failed": len(failed),
        "claims": [
            {"text": r.claim.text, "found": r.found,
             "confidence": r.confidence, "source": r.source,
             "evidence": r.evidence[:120] if r.evidence else ""}
            for r in results
        ],
        "warning": f"{len(failed)}/{len(results)} claims 无可信来源" if failed else "",
    }
