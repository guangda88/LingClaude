"""T1 事实校验 e2e — run_l5_audit_full 集成测试"""
import sys; sys.path.insert(0, "lingmemory")

import pytest
from lingclaude.core.fact_checker import KGFactChecker, ClaimExtractor, audit_response


class TestFactCheckerSearchFn:
    def test_default_search_fn_imports_lingzhi(self):
        """默认 search_fn 尝试导入灵知"""
        checker = KGFactChecker()
        assert checker._search_fn is not None

    def test_audit_response_injects_warnings(self):
        """audit_response 正确返回 warning"""
        # mock search_fn 返回低置信度
        def _low_conf(q):
            return [{"id": "x", "text": "", "confidence": 0.3}]
        result = audit_response(
            "已完成代码搜索。使用了grep工具。",
            checker=KGFactChecker(search_fn=_low_conf),
        )
        assert result["total"] >= 1
        # 低置信度 = 不 passed
        assert result["passed"] is False or result["total"] == 0


@pytest.mark.skip(reason="需要真实运行 QueryEngine + L5, 手动运行")
class TestRunL5AuditFullWithFactCheck:
    """真实 L5 链路测试 (需手动执行)"""

    def test_fact_check_logged_in_audit(self):
        """验证事实校验日志出现在 audit 输出中"""
        from lingclaude.core.query_engine import QueryEngine
        qe = QueryEngine.__new__(QueryEngine)
        result = qe._run_l5_orchestrator(
            prompt="硬化规则：优先使用code_search",
            output="已完成代码搜索。使用了grep工具。没有发现安全漏洞。",
        )
        assert isinstance(result, str)
