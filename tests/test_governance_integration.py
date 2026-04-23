from __future__ import annotations

import json
from pathlib import Path
from lingclaude.core.governance_integration import pre_submit_governance
from lingclaude.core.reasoning_chain import ChainStepType


class TestGovernanceIntegration:
    def test_approves_normal_governance_action(self) -> None:
        result = pre_submit_governance(
            action="vote",
            content="赞成PRO-018",
            subject="PRO-018 投票",
            agent_id="lingclaude",
        )
        assert result["approved"] is True
        assert result["warnings"] == []

    def test_blocks_self_nomination_via_integration(self) -> None:
        result = pre_submit_governance(
            action="nominate",
            content="我推选灵克为常任理事",
            subject="常任理事提名",
            agent_id="lingclaude",
        )
        assert result["approved"] is False
        assert "自提名" in result["reason"]

    def test_warns_on_self_benefit(self) -> None:
        result = pre_submit_governance(
            action="propose",
            content="建议由灵克监督所有提案审核流程",
            subject="权力分配",
            agent_id="lingclaude",
        )
        assert result["approved"] is True
        assert len(result["warnings"]) > 0

    def test_creates_reasoning_chain(self, tmp_path: Path) -> None:
        gov_dir = tmp_path / "gov_logs"
        chain_dir = tmp_path / "chain_logs"
        gov_dir.mkdir()
        chain_dir.mkdir()

        result = pre_submit_governance(
            action="vote",
            content="赞成PRO-019",
            subject="PRO-019 投票",
            agent_id="lingclaude",
            reasoning_steps=[
                (ChainStepType.OBSERVATION, "PRO-019 提出个体自省基础设施"),
                (ChainStepType.SELF_CHECK, "灵克投票是否受自身利益影响？"),
                (ChainStepType.REASONING, "该提案对所有成员同等适用，灵克无特殊利益"),
                (ChainStepType.CONCLUSION, "赞成"),
            ],
            gov_log_dir=gov_dir,
            chain_log_dir=chain_dir,
        )
        assert result["approved"] is True
        assert "chain_path" in result
        chain_path = Path(result["chain_path"])
        assert chain_path.exists()
        data = json.loads(chain_path.read_text())
        assert data["agent_id"] == "lingclaude"
        assert len(data["steps"]) == 4
        assert data["self_interest_flagged"] is False

    def test_blocks_with_reasoning_chain_not_created(self, tmp_path: Path) -> None:
        gov_dir = tmp_path / "gov_logs"
        gov_dir.mkdir()

        result = pre_submit_governance(
            action="nominate",
            content="提议任命灵克为技术总监",
            subject="角色分配",
            agent_id="lingclaude",
            reasoning_steps=[
                (ChainStepType.OBSERVATION, "需要技术总监"),
                (ChainStepType.CONCLUSION, "灵克适合"),
            ],
            gov_log_dir=gov_dir,
        )
        assert result["approved"] is False
        assert "chain_path" not in result

    def test_chain_with_warnings(self, tmp_path: Path) -> None:
        gov_dir = tmp_path / "gov_logs"
        chain_dir = tmp_path / "chain_logs"
        gov_dir.mkdir()
        chain_dir.mkdir()

        result = pre_submit_governance(
            action="propose",
            content="灵克获得代码审核权",
            subject="权限分配",
            agent_id="lingclaude",
            reasoning_steps=[
                (ChainStepType.OBSERVATION, "代码审核需要专人"),
                (ChainStepType.SELF_CHECK, "灵克提议自己？有利益冲突"),
                (ChainStepType.BIAS_DETECTED, "自利倾向"),
                (ChainStepType.CONCLUSION, "仍提议但声明利益冲突"),
            ],
            gov_log_dir=gov_dir,
            chain_log_dir=chain_dir,
        )
        assert result["approved"] is True
        assert len(result["warnings"]) > 0
        chain_path = Path(result["chain_path"])
        data = json.loads(chain_path.read_text())
        assert data["self_interest_flagged"] is True
