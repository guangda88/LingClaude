"""S5 契约测试：Cross-reference claims（灵元幻觉治理 P0-1，借鉴 CC）。

- ClaimExtractor 提取 claim 时同步捕获显式依据引用（"依据是X"/"因为X"）
- audit_response 对带依据引用的 claim 追加证据存在性检查
- 不改 fail-closed 语义、不改既有返回结构（新增字段零破坏）
"""
from __future__ import annotations

from lingclaude.core.fact_checker import ClaimExtractor, Claim, KGFactChecker, audit_response


def test_extract_captures_source_reference():
    """claim 后的"依据是X"被捕获为 source_text + source_span。"""
    text = "已确认修复完成，依据是测试报告 2026-09-14"
    claims = ClaimExtractor.extract(text)
    assert claims, "应提取到 claim"
    c = claims[0]
    assert c.source_text == "测试报告 2026-09-14"
    assert c.source_span != (0, 0)


def test_extract_captures_because_clause():
    """"因为X"也被捕获为依据。"""
    text = "已调用验证接口，因为需要检查数据一致性"
    claims = ClaimExtractor.extract(text)
    assert claims
    c = claims[0]
    assert "检查数据一致性" in c.source_text


def test_extract_no_source_keeps_empty():
    """无显式依据引用 → source 为空（零破坏）。"""
    text = "已完成任务"
    claims = ClaimExtractor.extract(text)
    assert claims
    c = claims[0]
    assert c.source_text == ""
    assert c.source_span == (0, 0)


class _FakeChecker(KGFactChecker):
    """假 checker：返回固定 found=True + 可控 evidence。"""

    def __init__(self, evidence: str):
        self._evidence = evidence

    def check(self, claim: Claim):
        return type(
            "R",
            (),
            {
                "claim": claim,
                "found": True,
                "confidence": 0.9,
                "source": "fake-doc",
                "evidence": self._evidence,
                "completeness": 1.0,
                "error": "",
            },
        )()


def test_cross_reference_evidence_exists():
    """依据文本出现在证据中 → 通过（found 保持 True）。"""
    checker = _FakeChecker(evidence="测试报告 2026-09-14 显示全部用例通过")
    out = "已确认修复完成，依据是测试报告 2026-09-14"
    result = audit_response(out, checker=checker)
    assert result["passed"] is True
    assert all(c["found"] for c in result["claims"])


def test_cross_reference_evidence_missing_flags_unverifiable():
    """依据文本大部分不在证据中 → 视同不可信（found=False）。"""
    checker = _FakeChecker(evidence="与修复无关的其他内容")
    out = "已确认修复完成，依据是测试报告 2026-09-14"
    result = audit_response(out, checker=checker)
    # 依据关键词 "测试报告"、"2026"、"09"、"14" 均不在证据中 → 标记不可信
    assert result["failed"] >= 1
    failing = [c for c in result["claims"] if not c["found"]]
    assert failing and failing[0]["source_text"] == "测试报告 2026-09-14"


def test_audit_response_includes_source_text_field():
    """返回结构新增 source_text 字段（零破坏，既有字段保留）。"""
    checker = _FakeChecker(evidence="x")
    result = audit_response("已完成任务，依据是文档A", checker=checker)
    assert "claims" in result and "passed" in result and "warning" in result
    for c in result["claims"]:
        assert "source_text" in c  # S5 新增字段
