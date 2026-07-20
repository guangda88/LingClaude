"""事实校验层测试 — T1"""
import sys; sys.path.insert(0, "lingmemory")

import pytest
from lingclaude.core.fact_checker import (
    ClaimExtractor, KGFactChecker, FactCheckResult, audit_response,
    get_db_pool, close_db_pool, reset_db_pool,
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
        assert result.completeness >= 0.7

    def test_low_completeness_detected(self):
        checker = KGFactChecker()
        # 模拟 search_fn 返回部分匹配证据
        def _partial(q):
            return [{"id": "x", "text": "代码相关", "confidence": 0.9}]
        checker._search_fn = _partial
        claim = ClaimExtractor.extract("已完成代码搜索")[0]
        result = checker.check(claim)
        assert result.found is True
        assert result.completeness < 0.7  # "代码搜索" 未完全覆盖

    def test_custom_search_fn(self):
        def _bad_search(q):
            return [{"id": "x", "text": "", "confidence": 0.3}]
        checker = KGFactChecker(search_fn=_bad_search)
        result = checker.check(ClaimExtractor.extract("已完成")[0])
        assert result.found is False


class TestDBPoolInjection:
    """T1.1: DB pool 注入路径 (production 部署核心)

    三种注入方式:
    1. 显式 db_pool 参数 (推荐 — 复用调用方连接池)
    2. db_url 参数 (fallback — 用传入的 DSN 创建)
    3. DATABASE_URL 环境变量 (最终 fallback)

    失败模式:
    - 无 pool/URL 配置 → RuntimeError → search 回退 mock
    """

    def setup_method(self):
        reset_db_pool()

    def teardown_method(self):
        reset_db_pool()

    def test_explicit_db_pool_priority(self):
        """显式 db_pool 优先级最高, 不读 env"""
        class FakePool:
            pass
        fake = FakePool()
        checker = KGFactChecker(db_pool=fake)
        assert checker._explicit_pool is fake

    def test_no_pool_no_url_falls_back_to_mock(self):
        """无 pool 无 URL → RuntimeError → mock 兜底"""
        import os
        old = os.environ.pop("DATABASE_URL", None)
        try:
            checker = KGFactChecker()
            claim = ClaimExtractor.extract("已完成测试")[0]
            result = checker.check(claim)
            # 失败回退 mock (found=True)
            assert result.found is True
        finally:
            if old is not None:
                os.environ["DATABASE_URL"] = old

    def test_db_url_passed_through(self):
        """db_url 参数被记录, 后续 pool 创建用"""
        checker = KGFactChecker(db_url="postgresql://test:test@localhost:5432/test")
        assert checker._db_url == "postgresql://test:test@localhost:5432/test"

    def test_pool_helpers_are_async_or_noop(self):
        """pool 帮助函数接口契约"""
        # get_db_pool 是 async
        import inspect
        assert inspect.iscoroutinefunction(get_db_pool)
        # close_db_pool 是 async
        assert inspect.iscoroutinefunction(close_db_pool)
        # reset_db_pool 是 sync (测试专用)
        assert not inspect.iscoroutinefunction(reset_db_pool)


class TestAuditResponse:
    def test_audit_passes_for_mock(self):
        result = audit_response("已完成代码搜索。使用了grep。")
        assert result["total"] >= 1
        assert isinstance(result["passed"], bool)

    def test_audit_empty_output(self):
        result = audit_response("")
        assert result["total"] == 0
        assert result["passed"] is True

    def test_audit_warns_on_unverified(self):
        """低置信度 search_fn 应触发 warning"""
        def _low_conf(q):
            return [{"id": "x", "text": "", "confidence": 0.2}]
        result = audit_response(
            "已完成代码搜索。使用了grep工具。",
            checker=KGFactChecker(search_fn=_low_conf),
        )
        assert result["passed"] is False or result["warning"] != ""
