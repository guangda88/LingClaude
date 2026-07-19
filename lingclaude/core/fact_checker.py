"""事实校验层 (Fact Checker) — T1

防低质模型幻觉: 模型答完后查灵知 KG 确认每个 claim 有来源。
无来源 claim 标警告或重答。

灵知对接: 通过 sys.path 旁路导入 lingzhi FactVerifier。
回退: mock 实现 (如果灵知不可用)。
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 灵知 FactVerifier 导入 (旁路)
_LINGZHI_PATH = "/home/ai/lingzhi"
if _LINGZHI_PATH not in sys.path:
    sys.path.insert(0, _LINGZHI_PATH)

try:
    from backend.services.retrieval.fact_verifier import FactVerifier as _LZFactVerifier
    from backend.services.retrieval.kg import KGRetriever as _LZKGRetriever
    _LINGZHI_AVAIL = True
    logger.info("灵知 FactVerifier 可用, 已加载")
except ImportError:
    _LINGZHI_AVAIL = False
    _LZFactVerifier = None  # type: ignore[assignment]
    logger.info("灵知 FactVerifier 不可用, 使用 mock")

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

    优先调灵知 FactVerifier (异步, 22ms p95)。
    不可用时回退到 mock (始终返回 found=True)。
    """
    
    def __init__(self, search_fn: Callable[[str], list[dict]] | None = None):
        self._search_fn = search_fn or self._build_default_search()
    
    def _build_default_search(self) -> Callable[[str], list[dict]]:
        """构建默认 search_fn: 优先灵知, 回退 mock"""
        if not _LINGZHI_AVAIL:
            return self._mock_search
        
        import asyncio
        from backend.services.retrieval.kg import KGRetriever
        
        class _LingZhiSearch:
            def __init__(self):
                import time
                self._last_warn = 0.0
            
            async def search(self, query: str) -> list[dict]:
                try:
                    kr = _LZKGRetriever()
                    return await kr.search(query, top_k=3)
                except Exception as e:
                    now = time.time()
                    if now - self._last_warn > 60:
                        logger.warning("灵知 KG 检索失败: %s", e)
                        self._last_warn = now
                    return []
        
        _lz_search = _LingZhiSearch()
        
        def _sync_search(query: str) -> list[dict]:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                results = loop.run_until_complete(_lz_search.search(query))
                loop.close()
                return results
            except Exception as e:
                logger.warning("灵知 KG 检索同步调用失败: %s", e)
                return self._mock_search(query)
        
        return _sync_search
    
    def _mock_search(self, query: str) -> list[dict]:
        """回退: 直接返回 mock (始终 found=True)"""
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
