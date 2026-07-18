"""事实校验层测试 — T1"""
import sys; sys.path.insert(0, "lingmemory")

import pytest
from lingclaude.core.fact_checker import (
    ClaimExtractor, KGFactChecker, FactCheckResult, audit_response,
)
from test_engine import TestCase, pytest_case


class TestClaimExtraction:
    def test_extract_completion_claims(self):
        text = "已完成代码搜索。使用了code_search工具。确认没有安全漏洞。"
        claims = ClaimExtractor.extract(text)
        assert len(claims) >= 3
        assert any("代码搜索" in c.text for c in claims)

    def test_extract_empty_text(self):
        assert ClaimExtractor.extract("") == []
        assert ClaimExtractor.extract("普通文本") == []


class TestKGFactChecker:
    def test_mock_check_passes(self):
        checker = KGFactChecker()
        claim = ClaimExtractor.extract("已完成代码搜索")[0]
        result = checker.check(claim)
        assert result.found is True

    def test_custom_search_fn(self):
        def _bad_search(q):
            return [{"id": "x", "text": "", "confidence": 0.3}]
        checker = KGFactChecker(search_fn=_bad_search)
        result = checker.check(ClaimExtractor.extract("已完成")[0])
        assert result.found is False


class TestAuditResponse:
    def test_audit_passes_for_mock(self):
        result = audit_response("已完成代码搜索。使用了grep。")
        assert result["total"] >= 1
        assert isinstance(result["passed"], bool)

    def test_audit_empty_output(self):
        result = audit_response("")
        assert result["total"] == 0
        assert result["passed"] is True
