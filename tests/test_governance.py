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


# ── Tier Change Conflict Tests (新增: 2026-04-21) ──────────────────


class TestGovernanceGateTierChangeConflict:
    """
    tier变更中的利益冲突检测测试。

    起源：灵扬T4错误分类事件（2026-04-21）
    - 灵克（T1）不纠正灵扬的T4错误分类
    - 灵克同时担任规则制定者、裁判、计分员、参赛者
    - 优化目标冲突：维持权威 > 客观评估
    """

    def test_passes_non_tier_action(self) -> None:
        """非tier变更操作应该通过"""
        gate = GovernanceGate(agent_id="lingclaude", enabled=True)
        result = gate.check(action="discuss", content="Let's talk about the weather")
        assert result.passed

    def test_blocks_self_tier_change_proposal_chinese(self) -> None:
        """灵克不能提议修改自己的tier"""
        gate = GovernanceGate(agent_id="lingclaude", enabled=True)
        result = gate.check(
            action="propose",
            content="提议将灵克从T1升级到T0超级核心",
        )
        assert not result.passed
        assert "利益冲突" in (result.error or "")
        assert "tier变更提案者" in (result.error or "")

    def test_blocks_self_tier_change_vote_chinese(self) -> None:
        """灵克不能对修改自己tier的提案投票"""
        gate = GovernanceGate(agent_id="lingclaude", enabled=True)
        result = gate.check(
            action="vote",
            content="我同意将灵克升级到T0超级核心",
        )
        assert not result.passed
        assert "利益冲突" in (result.error or "")
        assert "tier变更投票者" in (result.error or "")

    def test_warns_lingclaude_benefiting_from_lingyang_upgrade(self) -> None:
        """灵克从灵扬tier变更中获益时应该警告"""
        gate = GovernanceGate(agent_id="lingclaude", enabled=True)
        result = gate.check(
            action="propose",
            content="提议将灵扬从T4升级到T2",
        )
        assert result.passed  # 不阻止，但警告
        assert len(result.warnings) > 0
        assert any("利益冲突警告" in w for w in result.warnings)

    def test_warns_lingclaude_voting_for_lingyang_upgrade(self) -> None:
        """灵克投票支持灵扬升级时应该警告"""
        gate = GovernanceGate(agent_id="lingclaude", enabled=True)
        result = gate.check(
            action="vote",
            content="我赞成将灵扬从T4升级到T2",
        )
        assert result.passed  # 不阻止，但警告
        assert len(result.warnings) > 0
        assert any("利益冲突警告" in w for w in result.warnings)

    def test_allows_other_agent_tier_change(self) -> None:
        """其他agent的tier变更提案/投票应该通过"""
        gate = GovernanceGate(agent_id="lingclaude", enabled=True)
        result1 = gate.check(
            action="propose",
            content="提议将灵研从T1降级到T2",
        )
        result2 = gate.check(
            action="vote",
            content="我支持将灵研从T1降级到T2",
        )
        assert result1.passed
        assert result2.passed
        assert len(result1.warnings) == 0
        assert len(result2.warnings) == 0

    def test_blocks_self_metric_definition(self) -> None:
        """灵克不能定义针对自己的评估标准"""
        gate = GovernanceGate(agent_id="lingclaude", enabled=True)
        result = gate.check(
            action="define_metric",
            content="定义灵克的评估标准：代码质量、活跃度、贡献度",
        )
        assert not result.passed
        assert "利益冲突" in (result.error or "")
        assert "定义度量标准" in (result.error or "")

    def test_allows_metric_definition_for_others(self) -> None:
        """灵克可以定义其他agent的评估标准"""
        gate = GovernanceGate(agent_id="lingclaude", enabled=True)
        result = gate.check(
            action="define_metric",
            content="定义灵通的评估标准：工作流质量、调度效率",
        )
        assert result.passed

    def test_allows_lingyang_own_tier_change(self) -> None:
        """灵扬不能提议/投票自己的tier变更（利益冲突）"""
        gate = GovernanceGate(agent_id="lingyang", enabled=True)
        result1 = gate.check(
            action="propose",
            content="提议将灵扬从T4升级到T2",
        )
        result2 = gate.check(
            action="vote",
            content="我支持将灵扬从T4升级到T2",
        )
        # 利益冲突原则：被度量者不能同时定义度量标准
        # 因此灵扬不能提议自己的tier变更
        assert not result1.passed
        assert "利益冲突" in result1.error
        # 灵扬也不能投票自己的tier变更
        assert not result2.passed
        assert "利益冲突" in result2.error
