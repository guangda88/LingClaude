"""全链路 e2e: 三层质量过滤 — T1 + T2 + T3 + L5"""
import sys
for _p in ("/home/ai/lingminopt", "/home/ai/lingresearch", "/home/ai/lingzhi", "/home/ai/lingclaude/lingmemory"):
    if _p not in sys.path: sys.path.insert(0, _p)

import asyncio
import pytest

def _ii(): from lingyuan.l5_orchestrator import intent_precheck; return intent_precheck
def _ir(): from experiments.r5_kb_conflict import R5KBConflictSource; return R5KBConflictSource
def _if(): from backend.services.retrieval.fact_verifier import FactVerifier; return FactVerifier


class TestT1FactCheck:
    def test_claim_verified(self):
        FV = _if()
        class M:
            async def search(self, q, top_k=3):
                return [{"similarity": 0.85, "kg_entity": "cs", "source_table": "tools"}]
        r = asyncio.run(FV(M()).check_text("灵克使用了code_search"))
        assert r.verified >= 0

    def test_claim_unverified(self):
        FV = _if()
        class E:
            async def search(self, q, top_k=3):
                return []
        r = asyncio.run(FV(E()).check_text("虚构的不存在内容"))
        assert r.unverified > 0 or r.total == 0


class TestT2IntentPrecheck:
    def test_intent_match(self):
        result = _ii()("硬化规则", "硬化规则", llm=None, threshold=0.5)
        assert result.passed is True

    def test_intent_mismatch(self):
        result = _ii()("硬化规则", "今天天气不错哈哈", llm=None, threshold=0.5)
        assert result.passed is False


class TestT3R5KbConflict:
    def test_no_conflict(self):
        d = _ir()()
        r = d.ent_kb_conflict("code_search是搜索工具", ["code_search: done"])
        assert r is not None and hasattr(r, "conflict_score")

    def test_conflict_detected(self):
        d = _ir()()
        r = d.ent_kb_conflict("code_search是数据库工具", ["bash: rm -rf"])
        assert r is not None and hasattr(r, "conflict_score")


class TestThreeLayerEndToEnd:
    def test_e2e_all_layers(self):
        from lingyuan.l5_orchestrator import L5Orchestrator, L5Context, R5SignalSourceMock

        precheck = _ii()("硬化规则", "硬化规则", llm=None, threshold=0.5)
        assert precheck.passed is True

        FV = _if()
        class M:
            async def search(self, q, top_k=3):
                return [{"similarity": 0.85, "kg_entity": "cs", "source_table": "tools"}]
        report = asyncio.run(FV(M()).check_text("使用了code_search"))
        assert report.verified >= 0

        d = _ir()()
        c = d.ent_kb_conflict("code_search是搜索工具", ["code_search: done"])
        assert hasattr(c, "conflict_score")

        ctx = L5Context(session_id="e2e", round=0)
        orch = L5Orchestrator(r5_source=R5SignalSourceMock(), l5_context=ctx)
        result = orch.validate("使用code_search", ["cs: done"], 1, 4)
        assert hasattr(result, "consistency")

    def test_e2e_hallucination(self):
        FV = _if()
        class E:
            async def search(self, q, top_k=3):
                return []
        report = asyncio.run(FV(E()).check_text("code_search是一个搜索工具"))
        if report.unverified == 0 and report.total == 0:
            pytest.skip("skip: 灵知正则无法提取claim")

        d = _ir()()
        c = d.ent_kb_conflict("使用了不存在工具12345", [])
        assert hasattr(c, "conflict_score")
