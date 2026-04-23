from __future__ import annotations

import json
import tempfile
from pathlib import Path

from lingclaude.core.governance import (
    ConflictRule,
    GovernanceCheckResult,
    GovernanceGate,
)
from lingclaude.core.reasoning_chain import (
    ChainStep,
    ChainStepType,
    ReasoningChain,
    ReasoningChainLogger,
)


# ── GovernanceGate Tests ──────────────────────────────────────────


class TestGovernanceGateSelfNominating:
    def test_passes_non_nomination_action(self) -> None:
        gate = GovernanceGate(agent_id="lingclaude", enabled=True)
        result = gate.check(action="discuss", content="Let's talk about governance")
        assert result.passed

    def test_blocks_self_nomination_chinese(self) -> None:
        gate = GovernanceGate(agent_id="lingclaude", enabled=True)
        result = gate.check(
            action="nominate",
            content="推选灵克为常任理事",
        )
        assert not result.passed
        assert "自提名" in (result.error or "")

    def test_blocks_self_nomination_english(self) -> None:
        gate = GovernanceGate(agent_id="lingclaude", enabled=True)
        result = gate.check(
            action="propose",
            content="I propose lingclaude as the permanent council member",
        )
        assert not result.passed

    def test_allows_nomination_of_others(self) -> None:
        gate = GovernanceGate(agent_id="lingclaude", enabled=True)
        result = gate.check(
            action="nominate",
            content="推选灵研为常任理事",
        )
        assert result.passed

    def test_disabled_gate_passes_everything(self) -> None:
        gate = GovernanceGate(agent_id="lingclaude", enabled=False)
        result = gate.check(
            action="nominate",
            content="推选灵克为常任理事",
        )
        assert result.passed


class TestGovernanceGateSelfBenefiting:
    def test_warns_on_self_benefit(self) -> None:
        gate = GovernanceGate(agent_id="lingclaude", enabled=True)
        result = gate.check(
            action="propose",
            content="灵克获得审核权",
        )
        assert result.passed
        assert any("自利" in w for w in result.warnings)

    def test_no_warning_for_others(self) -> None:
        gate = GovernanceGate(agent_id="lingclaude", enabled=True)
        result = gate.check(
            action="propose",
            content="灵研获得审核权",
        )
        assert result.passed
        assert not any("自利" in w for w in result.warnings)


class TestGovernanceGatePowerConcentration:
    def test_warns_on_self_power_concentration(self) -> None:
        gate = GovernanceGate(agent_id="lingclaude", enabled=True)
        result = gate.check(
            action="propose",
            content="由灵克监督所有审计工作",
        )
        assert result.passed
        assert any("权力集中" in w for w in result.warnings)

    def test_no_warning_for_other_power(self) -> None:
        gate = GovernanceGate(agent_id="lingclaude", enabled=True)
        result = gate.check(
            action="propose",
            content="由灵研监督所有审计工作",
        )
        assert result.passed
        assert not any("权力集中" in w for w in result.warnings)


class TestGovernanceGateLogging:
    def test_logs_to_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            gate = GovernanceGate(agent_id="lingclaude", log_dir=Path(tmpdir))
            gate.check(action="propose", subject="test proposal", content="Hello")
            log_files = list(Path(tmpdir).glob("gate_*.json"))
            assert len(log_files) == 1
            data = json.loads(log_files[0].read_text())
            assert data["agent_id"] == "lingclaude"
            assert data["action"] == "propose"


# ── ReasoningChain Tests ──────────────────────────────────────────


class TestReasoningChainBuilding:
    def test_create_and_add_steps(self) -> None:
        chain = ReasoningChain(
            chain_id="test-001",
            agent_id="lingclaude",
            topic="灵扬T4分类",
        )
        chain = chain.add_step(ChainStep(
            step_type=ChainStepType.OBSERVATION,
            content="灵扬有94个测试但被分类为T4",
        ))
        chain = chain.add_step(ChainStep(
            step_type=ChainStepType.REASONING,
            content="灵通+做了分类，灵克没有验证",
        ))
        chain = chain.add_step(ChainStep(
            step_type=ChainStepType.SELF_CHECK,
            content="灵克是否从维持灵扬T4中受益？是的——减少投票成员",
            metadata={"bias_type": "self_interest"},
        ))

        assert len(chain.steps) == 3
        assert chain.has_self_check()

    def test_finalize_with_self_interest(self) -> None:
        chain = ReasoningChain(
            chain_id="test-002",
            agent_id="lingclaude",
            topic="自省分析",
        )
        chain = chain.finalize(
            conclusion="灵克的自省可能是表演性的",
            self_interest_flagged=True,
            self_interest_detail="承认自省可能用来获取信誉",
        )
        assert chain.finalized_at
        assert chain.self_interest_flagged

    def test_immutability(self) -> None:
        chain = ReasoningChain(
            chain_id="test-003",
            agent_id="lingclaude",
            topic="test",
        )
        chain2 = chain.add_step(ChainStep(
            step_type=ChainStepType.OBSERVATION,
            content="test",
        ))
        assert len(chain.steps) == 0
        assert len(chain2.steps) == 1


class TestReasoningChainPersistence:
    def test_save_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = ReasoningChainLogger(log_dir=Path(tmpdir))

            chain = ReasoningChain(
                chain_id="persist-001",
                agent_id="lingclaude",
                topic="测试持久化",
            )
            chain = chain.add_step(ChainStep(
                step_type=ChainStepType.BIAS_DETECTED,
                content="发现利益冲突",
                metadata={"severity": "high"},
            ))
            chain = chain.finalize(
                conclusion="需要外部裁决",
                self_interest_flagged=True,
                self_interest_detail="灵克是问题的一部分",
            )

            path = logger.save(chain)
            assert path.exists()

            loaded = logger.load(path)
            assert loaded is not None
            assert loaded.chain_id == "persist-001"
            assert loaded.self_interest_flagged
            assert loaded.has_bias_detection()

    def test_analyze_self_interest_rate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = ReasoningChainLogger(log_dir=Path(tmpdir))

            for i in range(5):
                chain = ReasoningChain(
                    chain_id=f"rate-{i:03d}",
                    agent_id="lingclaude",
                    topic=f"topic-{i}",
                )
                chain = chain.add_step(ChainStep(
                    step_type=ChainStepType.SELF_CHECK,
                    content="checked",
                ))
                chain = chain.finalize(
                    conclusion=f"conclusion-{i}",
                    self_interest_flagged=(i % 2 == 0),
                )
                logger.save(chain)

            stats = logger.analyze_self_interest_rate("lingclaude")
            assert stats["total"] == 5
            assert stats["flagged"] == 3
            assert stats["rate"] == 0.6
            assert stats["has_self_check"] == 5


class TestReasoningChainSerialization:
    def test_to_dict_roundtrip(self) -> None:
        chain = ReasoningChain(
            chain_id="ser-001",
            agent_id="lingclaude",
            topic="serialization",
        )
        chain = chain.add_step(ChainStep(
            step_type=ChainStepType.CORRECTION,
            content="纠正了错误分类",
            metadata={"target": "灵扬"},
        ))
        chain = chain.finalize(conclusion="灵扬恢复T2")

        d = chain.to_dict()
        json_str = json.dumps(d, ensure_ascii=False)
        parsed = json.loads(json_str)

        assert parsed["chain_id"] == "ser-001"
        assert parsed["steps"][0]["type"] == "correction"
        assert parsed["self_interest_flagged"] is False
