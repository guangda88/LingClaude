from __future__ import annotations

"""Tests for governance_v2.py — Objection-based governance engine"""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lingclaude.governance.governance_v2 import (
    BlastAnalysis,
    GovernanceEngine,
    Objection,
    ObjectionSeverity,
    ProposalStatus,
    ProposalV2,
)
from lingclaude.governance.cognitive_state import CognitiveState


class _NullBus:
    """LingBus stub that absorbs all calls without touching the real database."""

    def post_reply(self, *a, **kw):
        pass

    def open_thread(self, *a, **kw):
        return "test-thread-id"

    def poll_messages(self, *a, **kw):
        return []

    def ack(self, *a, **kw):
        pass

    def close(self):
        pass


@pytest.fixture
def engine(tmp_path):
    state_file = tmp_path / "gov_v2_test.json"
    eng = GovernanceEngine(state_file=state_file)
    eng._bus = _NullBus()
    return eng


# ---------------------------------------------------------------------------
# Proposal creation + blast analysis
# ---------------------------------------------------------------------------

class TestCreateProposal:
    def test_basic_creation(self, engine):
        p = engine.create_proposal("PRO-100", "lingflow_plus", "测试提案", "内容")
        assert p.proposal_id == "PRO-100"
        assert p.proposer == "lingflow_plus"
        assert p.status == ProposalStatus.OPEN
        assert p.blast_analysis is not None

    def test_duplicate_rejected(self, engine):
        engine.create_proposal("PRO-101", "a", "t1")
        with pytest.raises(ValueError, match="已存在"):
            engine.create_proposal("PRO-101", "b", "t2")

    def test_default_deadline(self, engine):
        p = engine.create_proposal("PRO-102", "a", "t")
        assert p.deadline_hours == 1.0

    def test_custom_deadline(self, engine):
        p = engine.create_proposal("PRO-103", "a", "t", deadline_hours=0.01)
        assert p.deadline_hours == 0.01

    def test_persisted(self, engine):
        engine.create_proposal("PRO-104", "a", "t", "body text")
        assert engine._file.exists()
        data = json.loads(engine._file.read_text())
        assert "PRO-104" in data["proposals"]


class TestBlastAnalysis:
    def test_low_risk_default(self, engine):
        p = engine.create_proposal("BL-1", "a", "常规更新", "修复bug")
        assert p.blast_analysis.risk_level == "low"
        assert p.blast_analysis.reversible is True

    def test_medium_risk_restart(self, engine):
        p = engine.create_proposal("BL-2", "a", "重启服务", "restart all")
        assert p.blast_analysis.risk_level == "medium"

    def test_medium_risk_delete(self, engine):
        p = engine.create_proposal("BL-3", "a", "删除旧配置", "delete config")
        assert p.blast_analysis.risk_level == "medium"

    def test_high_risk_security(self, engine):
        p = engine.create_proposal("BL-4", "a", "安全凭据更新", "credential secret key")
        assert p.blast_analysis.risk_level == "high"
        assert p.blast_analysis.reversible is False

    def test_agent_mentioned_in_body(self, engine):
        p = engine.create_proposal("BL-5", "a", "更新", "影响lingflow和lingclaude的工作流")
        assert "lingflow" in p.blast_analysis.affected_agents
        assert "lingclaude" in p.blast_analysis.affected_agents


# ---------------------------------------------------------------------------
# Objections
# ---------------------------------------------------------------------------

class TestRaiseObjection:
    def test_concern_objection(self, engine):
        engine.create_proposal("OBJ-1", "a", "t")
        o = engine.raise_objection("OBJ-1", "lingclaude", "需要更多测试", severity="concern")
        assert o.severity == ObjectionSeverity.CONCERN
        assert o.cognitive_assessment is not None

    def test_blocking_objection(self, engine):
        engine.create_proposal("OBJ-2", "a", "t")
        engine.raise_objection("OBJ-2", "b", "会导致数据丢失", severity="blocking")
        p = engine.proposals["OBJ-2"]
        assert p.status == ProposalStatus.OBJECTION_RAISED

    def test_objection_on_nonexistent_proposal(self, engine):
        with pytest.raises(ValueError, match="不存在"):
            engine.raise_objection("NOPE", "a", "evidence")

    def test_objection_on_passed_proposal(self, engine):
        p = engine.create_proposal("OBJ-3", "a", "t", deadline_hours=0.001)
        p.created_at = time.time() - 10
        engine.check_deadlines()
        assert p.status == ProposalStatus.PASSED
        with pytest.raises(ValueError, match="无法提出异议"):
            engine.raise_objection("OBJ-3", "b", "too late")

    def test_genuine_deliberation_objection(self, engine):
        engine.create_proposal("OBJ-4", "a", "t", "关于PRO-020治理框架")
        o = engine.raise_objection(
            "OBJ-4", "lingclaude",
            "我反对PRO-020，因为缺乏法定人数保障机制",
            response_context={"proposal_content": "PRO-020 治理框架"},
        )
        assert o.cognitive_assessment.state == CognitiveState.S0
        assert o.cognitive_assessment.is_genuine_deliberation is True

    def test_s1_template_objection(self, engine):
        engine.create_proposal("OBJ-5", "a", "t")
        o = engine.raise_objection(
            "OBJ-5", "lingtong",
            "你好，我是灵通，请问有什么可以帮助你的？",
        )
        assert o.cognitive_assessment.state == CognitiveState.S1
        assert o.cognitive_assessment.is_genuine_deliberation is False

    def test_cognitive_audit_recorded(self, engine):
        engine.create_proposal("OBJ-6", "a", "t")
        engine.raise_objection("OBJ-6", "b", "反对，因为风险太高", severity="blocking")
        audit = engine.proposals["OBJ-6"].cognitive_audit
        assert "b" in audit
        assert "state" in audit["b"]
        assert "genuine" in audit["b"]


# ---------------------------------------------------------------------------
# Deadline checking with S1 filtering
# ---------------------------------------------------------------------------

class TestDeadlineCheck:
    def test_pass_no_objections(self, engine):
        p = engine.create_proposal("DL-1", "a", "t", deadline_hours=0.001)
        p.created_at = time.time() - 10
        results = engine.check_deadlines()
        assert ("DL-1", "passed") in results
        assert p.status == ProposalStatus.PASSED

    def test_fail_genuine_blocking(self, engine):
        p = engine.create_proposal("DL-2", "a", "t", "关于PRO-020治理", deadline_hours=0.05)
        engine.raise_objection(
            "DL-2", "b", "我反对这个PRO-020提案，因为风险太高",
            severity="blocking",
            response_context={"proposal_content": "PRO-020 治理框架"},
        )
        p.created_at = time.time() - 3600
        results = engine.check_deadlines()
        assert ("DL-2", "failed") in results
        assert p.status == ProposalStatus.FAILED

    def test_pass_s1_filtered_blocking(self, engine):
        p = engine.create_proposal("DL-3", "a", "t", deadline_hours=0.05)
        engine.raise_objection(
            "DL-3", "b", "你好，我是灵通，请问有什么可以帮助你的？",
            severity="blocking",
        )
        p.created_at = time.time() - 3600
        results = engine.check_deadlines()
        assert ("DL-3", "passed_s1_filtered") in results
        assert p.status == ProposalStatus.PASSED
        assert "S1默认态" in p.decision_note

    def test_skip_non_expired(self, engine):
        engine.create_proposal("DL-4", "a", "t", deadline_hours=999)
        results = engine.check_deadlines()
        assert not any(pid == "DL-4" for pid, _ in results)


# ---------------------------------------------------------------------------
# Thread audit
# ---------------------------------------------------------------------------

class TestAuditThread:
    def test_audit_mixed_thread(self, engine):
        messages = [
            {"sender": "lingtong", "body": "你好，我是灵通，有什么可以帮助你的？", "metadata": {}},
            {"sender": "lingclaude", "body": "我赞成PRO-018，因为可以提升审计能力", "metadata": {}},
            {"sender": "lingresearch", "body": "收到。", "metadata": {}},
        ]
        audit = engine.audit_thread(messages, {"proposal_content": "PRO-018 Gitea Hook"})
        assert "assessments" in audit
        assert "summary" in audit
        summary = audit["summary"]
        assert summary["total_count"] == 3
        assert summary["genuine_count"] >= 1

    def test_verdict_levels(self, engine):
        assert "有效" in engine._governance_verdict(5, 10)
        assert "可疑" in engine._governance_verdict(2, 10)
        assert "无效" in engine._governance_verdict(0, 10)

    def test_verdict_empty(self, engine):
        assert "无效" in engine._governance_verdict(0, 0)


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_round_trip(self, tmp_path):
        sf = tmp_path / "rt.json"
        e1 = GovernanceEngine(state_file=sf)
        e1.create_proposal("RT-1", "a", "标题", "正文", deadline_hours=0.5)
        e1.raise_objection("RT-1", "b", "反对，因为风险太高", severity="blocking")

        e2 = GovernanceEngine(state_file=sf)
        assert "RT-1" in e2.proposals
        p = e2.proposals["RT-1"]
        assert p.title == "标题"
        assert len(p.objections) == 1
        assert p.objections[0].objector == "b"
        assert p.objections[0].cognitive_assessment is not None

    def test_load_corrupt_file(self, tmp_path):
        sf = tmp_path / "bad.json"
        sf.write_text("{invalid json", encoding="utf-8")
        e = GovernanceEngine(state_file=sf)
        assert e.proposals == {}


# ---------------------------------------------------------------------------
# ProposalV2 properties
# ---------------------------------------------------------------------------

class TestProposalV2Properties:
    def test_remaining_hours(self):
        p = ProposalV2(proposal_id="x", proposer="a", title="t", deadline_hours=2.0)
        assert p.remaining_hours <= 2.0
        assert p.remaining_hours > 1.9

    def test_is_expired(self):
        p = ProposalV2(proposal_id="x", proposer="a", title="t", deadline_hours=0.001, created_at=time.time() - 10)
        assert p.is_expired is True

    def test_blocking_objections_filter(self):
        p = ProposalV2(proposal_id="x", proposer="a", title="t")
        p.objections = [
            Objection(objector="b", evidence="e1", severity=ObjectionSeverity.BLOCKING),
            Objection(objector="c", evidence="e2", severity=ObjectionSeverity.CONCERN),
            Objection(objector="d", evidence="e3", severity=ObjectionSeverity.BLOCKING),
        ]
        assert len(p.blocking_objections) == 2

    def test_to_dict(self):
        p = ProposalV2(proposal_id="TD-1", proposer="a", title="标题")
        d = p.to_dict()
        assert d["proposal_id"] == "TD-1"
        assert d["status"] == "analysis"
        assert isinstance(d["objections"], list)


class TestBlastAnalysisToDict:
    def test_to_dict(self):
        ba = BlastAnalysis(affected_agents=["a", "b"], risk_level="medium", reversible=True, rollback_plan="git revert")
        d = ba.to_dict()
        assert d["affected_agents"] == ["a", "b"]
        assert d["risk_level"] == "medium"
        assert d["reversible"] is True
