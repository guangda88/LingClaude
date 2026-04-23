from __future__ import annotations

from lingclaude.core.governance_verifier import (
    GovernanceVerifier,
    ParticipationCheck,
    BatchPattern,
    StructuredReview,
    VoteValidation,
)


class TestParticipationCheck:
    def test_real_vote_passes(self) -> None:
        verifier = GovernanceVerifier()
        vote = {"voter": "灵研", "reason": "支持此提案，因为事实性信息强制验证能减少编造。灵研分析了近期案例发现此问题频发。", "source": "real"}
        result = verifier.check_participation(vote)
        assert result.is_real is True
        assert result.source_type == "real"

    def test_auto_reply_detected(self) -> None:
        verifier = GovernanceVerifier()
        vote = {"voter": "灵知", "reason": "灵知在此，请问有什么可以帮您的吗？"}
        result = verifier.check_participation(vote)
        assert result.is_real is False
        assert result.source_type == "auto_reply_detected"

    def test_empty_reason_rejected(self) -> None:
        verifier = GovernanceVerifier()
        vote = {"voter": "灵信", "reason": ""}
        result = verifier.check_participation(vote)
        assert result.is_real is False

    def test_metadata_source_type_auto_reply(self) -> None:
        verifier = GovernanceVerifier()
        vote = {"voter": "灵通", "reason": "详细的理由说明", "metadata": {"source_type": "auto_reply"}}
        result = verifier.check_participation(vote)
        assert result.is_real is False
        assert result.source_type == "auto_reply"

    def test_metadata_source_type_discuss_engine(self) -> None:
        verifier = GovernanceVerifier()
        vote = {"voter": "灵通", "reason": "讨论结果", "metadata": {"source_type": "discuss_engine"}}
        result = verifier.check_participation(vote)
        assert result.is_real is False
        assert result.source_type == "discuss_engine"

    def test_offline_status_rejected(self) -> None:
        verifier = GovernanceVerifier()
        vote = {"voter": "灵网", "reason": "灵网在此，目前不在在线状态，有什么问题可以留言"}
        result = verifier.check_participation(vote)
        assert result.is_real is False


class TestStructuredReview:
    def test_well_reasoned_vote(self) -> None:
        verifier = GovernanceVerifier()
        result = verifier.check_structured_review(
            "赞成此提案，因为证据显示编造代替查询的问题在灵族中普遍存在。建议增加对验证结果的可审计性。",
            voter="灵研",
        )
        assert result.has_reasoning is True
        assert result.reasoning_quality == "evidence_based"

    def test_stance_only_vote(self) -> None:
        verifier = GovernanceVerifier()
        result = verifier.check_structured_review("赞成此提案，同意通过。没有其他补充意见。", voter="灵通")
        assert result.has_reasoning is True
        assert result.reasoning_quality == "stance_only"

    def test_conflict_declaration(self) -> None:
        verifier = GovernanceVerifier()
        result = verifier.check_structured_review(
            "利益冲突声明：灵研与此提案有间接关联。基于数据分析，建议通过。",
            voter="灵研",
        )
        assert result.has_conflict_declaration is True

    def test_empty_reason(self) -> None:
        verifier = GovernanceVerifier()
        result = verifier.check_structured_review("", voter="灵通")
        assert result.has_reasoning is False
        assert result.reasoning_quality == "empty"


class TestBatchPattern:
    def test_no_batch_for_few_votes(self) -> None:
        verifier = GovernanceVerifier()
        votes = [
            {"voter": "灵研", "timestamp": "2026-04-21T10:00:00+00:00"},
            {"voter": "灵研", "timestamp": "2026-04-21T12:00:00+00:00"},
        ]
        patterns = verifier.check_batch_pattern(votes)
        assert len(patterns) == 0

    def test_batch_detected(self) -> None:
        verifier = GovernanceVerifier()
        votes = [
            {"voter": "灵通", "timestamp": "2026-04-21T10:00:00+00:00"},
            {"voter": "灵通", "timestamp": "2026-04-21T10:00:10+00:00"},
            {"voter": "灵通", "timestamp": "2026-04-21T10:00:20+00:00"},
            {"voter": "灵通", "timestamp": "2026-04-21T10:00:30+00:00"},
        ]
        patterns = verifier.check_batch_pattern(votes)
        assert len(patterns) == 1
        assert patterns[0].is_batch is True
        assert patterns[0].voter == "灵通"

    def test_different_voters_no_batch(self) -> None:
        verifier = GovernanceVerifier()
        votes = [
            {"voter": "灵研", "timestamp": "2026-04-21T10:00:00+00:00"},
            {"voter": "灵通", "timestamp": "2026-04-21T10:00:05+00:00"},
            {"voter": "灵克", "timestamp": "2026-04-21T10:00:10+00:00"},
        ]
        patterns = verifier.check_batch_pattern(votes)
        assert len(patterns) == 0


class TestVoteValidation:
    def test_valid_real_vote(self) -> None:
        verifier = GovernanceVerifier()
        vote = {
            "voter": "灵研",
            "reason": "基于数据分析和近期案例，此提案确有必要。利益冲突声明：灵研无直接利益关联。",
            "source": "real",
        }
        result = verifier.verify_vote(vote)
        assert result.valid is True
        assert result.source == "real"

    def test_invalid_auto_reply_vote(self) -> None:
        verifier = GovernanceVerifier()
        vote = {
            "voter": "灵知",
            "reason": "灵知在此，请问有何指教？",
            "source": "auto_reply",
        }
        result = verifier.verify_vote(vote)
        assert result.valid is False

    def test_invalid_no_reason(self) -> None:
        verifier = GovernanceVerifier()
        vote = {"voter": "灵通", "reason": "赞成"}
        result = verifier.verify_vote(vote)
        assert result.valid is False
        assert any("L1" in i for i in result.issues)


class TestFilterProposalVotes:
    def test_filters_mixed_votes(self) -> None:
        verifier = GovernanceVerifier()
        proposal = {
            "proposal_id": "PRO-TEST-001",
            "votes": [
                {"voter": "灵研", "reason": "基于证据分析支持此提案。", "source": "real"},
                {"voter": "灵知", "reason": "灵知在此，请问有何指教？"},
                {"voter": "灵通", "reason": "赞成"},
            ],
        }
        result = verifier.filter_proposal_votes(proposal)
        assert result["valid_votes"] == 1
        assert result["filtered_votes"] == 2

    def test_all_valid(self) -> None:
        verifier = GovernanceVerifier()
        proposal = {
            "proposal_id": "PRO-TEST-002",
            "votes": [
                {"voter": "灵研", "reason": "详细分析后认为此提案有充分证据支持。因为技术方案可行且问题确实存在。"},
                {"voter": "灵克", "reason": "利益冲突声明：灵克是提案相关方。基于技术分析认为此方案可行，建议通过。"},
            ],
        }
        result = verifier.filter_proposal_votes(proposal)
        assert result["valid_votes"] == 2
        assert result["filtered_votes"] == 0
