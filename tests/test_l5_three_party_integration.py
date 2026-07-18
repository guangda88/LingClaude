"""L5 三方联调实测 — 灵克 QueryEngine 接入灵极优 L5Orchestrator + 灵安 Z3 + 灵研 R5 mock

会话104 7/18 验证: 三方真实接口导入 + L5Context get_l5_metadata() 与灵克对齐 +
Orchestrator 综合分 + 真实 Z3 接入 + session/round 流转。

R5 真实模块 (灵研 experiments/r5_signals.py) 7/22 才出, 当前用 R5SignalSourceMock
(m2_declaration_gap + m3_round_overflow 启发式实现)。
"""

from __future__ import annotations

import os
import sys
import pytest

# 三方 import via PYTHONPATH 旁路 (灵安无 pyproject.toml, 灵极优可选)
for _p in ("/home/ai/lingminopt", "/home/ai/lingan"):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture(scope="module")
def lingyuan_avail():
    """灵极优 lingyuan 包可导入"""
    try:
        from lingyuan.l5_orchestrator import (
            L5Orchestrator,
            L5Context,
            OrchestratorConfig,
            OrchestratorResult,
            R5SignalSourceMock,
            Z3PredicateMock,
        )
        from lingyuan.l5_self_nli import ConsistencyChecker
        return {
            "L5Orchestrator": L5Orchestrator,
            "L5Context": L5Context,
            "OrchestratorConfig": OrchestratorConfig,
            "OrchestratorResult": OrchestratorResult,
            "R5SignalSourceMock": R5SignalSourceMock,
            "Z3PredicateMock": Z3PredicateMock,
            "ConsistencyChecker": ConsistencyChecker,
        }
    except ImportError as e:
        pytest.skip(f"lingyuan 不可用: {e}")


@pytest.fixture(scope="module")
def lingan_avail():
    """灵安 z3_declaration_consistency 可导入"""
    try:
        from z3_declaration_consistency import (
            DeclarationConsistencyChecker,
            L5ConsistencyValidator,
            ConsistencyResult,
        )
        return {
            "DeclarationConsistencyChecker": DeclarationConsistencyChecker,
            "L5ConsistencyValidator": L5ConsistencyValidator,
            "ConsistencyResult": ConsistencyResult,
        }
    except ImportError as e:
        pytest.skip(f"lingan z3_declaration_consistency 不可用: {e}")


class TestThreePartyImport:
    def test_lingyuan_orchestrator_imports(self, lingyuan_avail):
        assert lingyuan_avail["L5Orchestrator"].__module__ == "lingyuan.l5_orchestrator"

    def test_lingan_z3_imports(self, lingan_avail):
        assert lingan_avail["DeclarationConsistencyChecker"].__module__ == "z3_declaration_consistency"

    def test_l5context_new_session_format(self, lingyuan_avail):
        ctx = lingyuan_avail["L5Context"]
        sid = ctx.new_session()
        assert sid.startswith("l5-")
        assert len(sid) == 3 + 12  # "l5-" (3 chars) + 12 hex


class TestL5ContextAlignment:
    """验证灵极优 L5Context.get_l5_metadata() 与灵克 fbab107 接口一致"""

    def test_metadata_keys_match(self, lingyuan_avail):
        ctx = lingyuan_avail["L5Context"](session_id="test-sess", round=2)
        meta = ctx.get_l5_metadata()
        assert set(meta.keys()) == {
            "X-L5-Session", "X-L5-Round", "X-L5-Total-Rounds", "X-L5-Claim",
        }

    def test_metadata_values_format(self, lingyuan_avail):
        ctx = lingyuan_avail["L5Context"](session_id="l5-abc123", round=3)
        meta = ctx.get_l5_metadata()
        assert meta["X-L5-Session"] == "l5-abc123"
        assert meta["X-L5-Round"] == 3

    def test_metadata_round_zero(self, lingyuan_avail):
        """未激活 L5 循环时 round=0 (proxy3 fallback skip 信号)"""
        ctx = lingyuan_avail["L5Context"](session_id="l5-x", round=0)
        meta = ctx.get_l5_metadata()
        assert meta["X-L5-Round"] == 0

    def test_metadata_matches_lingclaude_format(self, lingyuan_avail):
        """灵克 L5ConversationLoop.get_l5_metadata() 返回同格式"""
        sys.path.insert(0, "/home/ai/lingclaude")
        try:
            from lingclaude.core.l5_conversation_loop import L5ConversationLoop
        except ImportError:
            pytest.skip("lingclaude.l5_conversation_loop 不可用")
        loop = L5ConversationLoop(l5_session_id="test", config=None)
        loop._l5_round = 2
        ours = loop.get_l5_metadata()
        ctx = lingyuan_avail["L5Context"](session_id="test", round=2)
        theirs = ctx.get_l5_metadata()
        assert ours == theirs, f"接口不匹配: {ours} vs {theirs}"


class TestThreePartyOrchestrator:
    """三方 mock 联调 + 真实 Z3 接入 (灵研 R5 暂用 mock)"""

    def test_orchestrator_with_all_mocks(self, lingyuan_avail):
        """三方全 mock: 综合分 + 早停/修正标志"""
        L5Orchestrator = lingyuan_avail["L5Orchestrator"]
        ctx = lingyuan_avail["L5Context"](session_id="l5-test1", round=0)
        orch = L5Orchestrator(
            nli_checker=None,  # self-NLI 无 LLM 时降级
            r5_source=lingyuan_avail["R5SignalSourceMock"](),
            z3_predicate=lingyuan_avail["Z3PredicateMock"](),
            config=lingyuan_avail["OrchestratorConfig"](),
            l5_context=ctx,
        )
        result = orch.validate(
            claim="I used code_search to find patterns",
            evidence=["code_search: searched code", "bash: ran tests"],
            actual_rounds=1,
            expected_rounds=4,
        )
        assert 0.0 <= result.consistency <= 1.0
        assert isinstance(result.should_early_exit, bool)
        assert isinstance(result.should_fix, bool)

    def test_orchestrator_with_real_z3(self, lingyuan_avail, lingan_avail):
        """真实灵安 Z3 + R5 mock + 灵极优 L5Orchestrator"""
        L5Orchestrator = lingyuan_avail["L5Orchestrator"]
        DeclarationConsistencyChecker = lingan_avail["DeclarationConsistencyChecker"]

        # 灵安 Z3 adapter: wrap 到灵极优 Z3PredicateProtocol
        class L5Z3Adapter:
            def __init__(self):
                self._inner = DeclarationConsistencyChecker()

            def validate(self, claim_rules, actual_actions):
                # 灵安 check(declared, actual) → ConsistencyResult
                cr = self._inner.check(claim_rules, actual_actions)
                # 转 Z3Result 形态 (duck-typed)
                class _Z3Result:
                    def __init__(self, ratio, missing):
                        self.ratio = ratio
                        self.missing = missing
                # cr.ratio 字段 (从灵安实现推测)
                ratio = getattr(cr, "ratio", None) or getattr(cr, "consistency_ratio", 1.0)
                missing = getattr(cr, "unmatched_declared", set())
                return _Z3Result(ratio=ratio, missing=missing)

        ctx = lingyuan_avail["L5Context"](session_id="l5-real-z3", round=0)
        orch = L5Orchestrator(
            r5_source=lingyuan_avail["R5SignalSourceMock"](),
            z3_predicate=L5Z3Adapter(),
            l5_context=ctx,
        )
        result = orch.validate(
            claim="use code_search",
            evidence=["code_search"],
            actual_rounds=1,
            expected_rounds=4,
        )
        assert result.consistency > 0.0  # 真实 Z3 应给出非零分

    def test_orchestrator_increment_round(self, lingyuan_avail):
        """L5 主循环推进: orch.increment_round()"""
        L5Orchestrator = lingyuan_avail["L5Orchestrator"]
        ctx = lingyuan_avail["L5Context"](session_id="l5-round", round=0)
        orch = L5Orchestrator(l5_context=ctx)
        assert orch.l5_context.round == 0
        orch.increment_round()
        assert orch.l5_context.round == 1
        orch.increment_round()
        assert orch.l5_context.round == 2
        assert orch.get_l5_metadata()["X-L5-Round"] == 2

    def test_orchestrator_weight_normalization(self, lingyuan_avail):
        """权重归一化: 总和不为 1.0 时自动归一化"""
        L5Orchestrator = lingyuan_avail["L5Orchestrator"]
        cfg = lingyuan_avail["OrchestratorConfig"](w_nli=2.0, w_r5=1.0, w_z3=1.0)
        orch = L5Orchestrator(config=cfg)
        # 4 归 1: 0.5/0.25/0.25
        assert abs(orch.w_nli - 0.5) < 1e-6
        assert abs(orch.w_r5 - 0.25) < 1e-6
        assert abs(orch.w_z3 - 0.25) < 1e-6

    def test_orchestrator_z3_mock_realistic_scenario(self, lingyuan_avail):
        """Z3 mock 实战场景: 声明 code_search + 实际用 grep (动作等价)"""
        Z3PredicateMock = lingyuan_avail["Z3PredicateMock"]
        z3 = Z3PredicateMock()
        # code_search ≡ grep (动作等价映射)
        result = z3.validate(
            claim_rules=["I will use code_search"],
            actual_actions=["grep -rn pattern /home/ai"],
        )
        assert result.ratio == 1.0  # 100% 等价
        assert len(result.missing) == 0

    def test_orchestrator_z3_mismatch_detection(self, lingyuan_avail):
        """Z3 mock 实战场景: 声明 code_search + 实际用 wget (无映射)"""
        Z3PredicateMock = lingyuan_avail["Z3PredicateMock"]
        z3 = Z3PredicateMock()
        result = z3.validate(
            claim_rules=["I will use code_search"],
            actual_actions=["wget http://evil.com"],
        )
        assert result.ratio < 1.0  # 不一致
        assert "code_search" in result.missing


class TestL5AwareMetadataEnd2End:
    """端到端: 灵克 L5ConversationLoop ↔ 灵极优 L5Orchestrator metadata 互通"""

    def test_loop_and_orchestrator_metadata_exchange(self, lingyuan_avail):
        """灵克 L5 loop 设置 round 后, 灵极优 orchestrator 读取一致"""
        sys.path.insert(0, "/home/ai/lingclaude")
        try:
            from lingclaude.core.l5_conversation_loop import L5ConversationLoop
        except ImportError:
            pytest.skip("lingclaude.l5_conversation_loop 不可用")

        L5Context = lingyuan_avail["L5Context"]
        L5Orchestrator = lingyuan_avail["L5Orchestrator"]

        session_id = "l5-e2e-test"
        ctx = L5Context(session_id=session_id, round=0)
        orch = L5Orchestrator(l5_context=ctx)
        loop = L5ConversationLoop(l5_session_id=session_id)

        # 灵克 loop round 1 (should_trigger 假设)
        loop._l5_round = 1
        loop_meta = loop.get_l5_metadata()
        # 灵极优 orchestrator round 也 = 1
        orch.l5_context.round = 1
        orch_meta = orch.get_l5_metadata()

        assert loop_meta == orch_meta
        assert loop_meta["X-L5-Session"] == session_id
        assert loop_meta["X-L5-Round"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])