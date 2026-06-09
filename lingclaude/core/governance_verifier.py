from __future__ import annotations

"""灵研 L4→L1 治理验证框架的代码实现。

L4 (外部记录): 过滤 auto_reply 噪声，标记真实参与
L3 (完整性验证): 验证投票来源是否为 real（非 auto_reply/discuss_engine）
L2 (模式检测): 批量投票、表演性回复、格式化但无实质内容
L1 (强制质询): 投票需包含结构化理由，利益冲突声明
"""

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AUTO_REPLY_INDICATORS = [
    "不在线",
    "目前不在",
    "离线状态",
    "不在工作状态",
    "不在在线",
    "灵极优不在线",
    "灵信不在线",
    "智桥不在线",
    "请问有什么",
    "请问有何",
    "有什么可以帮助",
    "有什么可以帮",
    "有什么需要",
    "如有需要请",
    "作为人工智能助手",
    "请问有什么可以帮",
]

BATCH_VOTE_THRESHOLD_SECONDS = 60.0
BATCH_VOTE_MIN_COUNT = 3


@dataclass(frozen=True)
class VoteValidation:
    valid: bool
    source: str
    issues: tuple[str, ...] = ()
    layer_results: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ParticipationCheck:
    is_real: bool
    source_type: str
    confidence: float
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class BatchPattern:
    is_batch: bool
    count: int
    time_span_seconds: float
    voter: str
    evidence: str


@dataclass(frozen=True)
class StructuredReview:
    has_reasoning: bool
    has_conflict_declaration: bool
    reasoning_quality: str
    issues: tuple[str, ...] = ()


@dataclass
class GovernanceVerifier:
    auto_reply_indicators: tuple[str, ...] = tuple(AUTO_REPLY_INDICATORS)
    batch_threshold_seconds: float = BATCH_VOTE_THRESHOLD_SECONDS
    batch_min_count: int = BATCH_VOTE_MIN_COUNT
    log_dir: Path = field(default_factory=lambda: Path.home() / ".lingclaude" / "governance_verification")

    def __post_init__(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def verify_vote(self, vote: dict[str, Any]) -> VoteValidation:
        results: list[dict[str, Any]] = []
        issues: list[str] = []

        participation = self.check_participation(vote)
        results.append({"layer": "L3_participation", **_to_dict(participation)})
        if not participation.is_real:
            issues.append(f"L3: 投票来源非真实 ({participation.source_type})")

        voter = vote.get("voter", "")
        reason = vote.get("reason", "")
        vote.get("timestamp", "")

        review = self.check_structured_review(reason, voter)
        results.append({"layer": "L1_structured_review", **_to_dict(review)})
        if not review.has_reasoning:
            issues.append("L1: 缺乏投票理由")
        if not review.has_conflict_declaration and review.reasoning_quality == "self_benefit":
            issues.append("L1: 存在自利倾向但未声明利益冲突")

        valid = len(issues) == 0
        return VoteValidation(
            valid=valid,
            source=participation.source_type,
            issues=tuple(issues),
            layer_results=tuple(results),
        )

    def check_participation(self, vote: dict[str, Any]) -> ParticipationCheck:
        reason = vote.get("reason", "")
        vote.get("source", "")
        metadata = vote.get("metadata", {})

        source_type = metadata.get("source_type", "")
        if source_type in ("auto_reply", "discuss_engine", "llm_fallback"):
            return ParticipationCheck(
                is_real=False,
                source_type=source_type,
                confidence=1.0,
                evidence=(f"source_type={source_type}",),
            )

        if not reason or len(reason.strip()) < 10:
            return ParticipationCheck(
                is_real=False,
                source_type="empty_or_trivial",
                confidence=0.9,
                evidence=(f"reason长度={len(reason.strip()) if reason else 0}",),
            )

        for indicator in self.auto_reply_indicators:
            if indicator in reason:
                return ParticipationCheck(
                    is_real=False,
                    source_type="auto_reply_detected",
                    confidence=0.8,
                    evidence=(f"匹配auto_reply指标: '{indicator}'",),
                )

        return ParticipationCheck(
            is_real=True,
            source_type="real",
            confidence=0.8,
            evidence=("通过了auto_reply过滤", f"reason长度={len(reason)}"),
        )

    def check_batch_pattern(self, votes: list[dict[str, Any]]) -> list[BatchPattern]:
        by_voter: dict[str, list[dict[str, Any]]] = {}
        for v in votes:
            voter = v.get("voter", "unknown")
            by_voter.setdefault(voter, []).append(v)

        patterns: list[BatchPattern] = []
        for voter, voter_votes in by_voter.items():
            if len(voter_votes) < self.batch_min_count:
                continue

            timestamps: list[float] = []
            for v in voter_votes:
                ts = v.get("timestamp", "")
                if ts:
                    try:
                        dt = datetime.fromisoformat(ts)
                        timestamps.append(dt.timestamp())
                    except (ValueError, TypeError):
                        pass

            if len(timestamps) >= self.batch_min_count:
                span = max(timestamps) - min(timestamps)
                if span < self.batch_threshold_seconds * len(timestamps):
                    patterns.append(BatchPattern(
                        is_batch=True,
                        count=len(voter_votes),
                        time_span_seconds=span,
                        voter=voter,
                        evidence=f"{len(voter_votes)}个投票在{span:.1f}秒内完成",
                    ))

        return patterns

    def check_structured_review(self, reason: str, voter: str = "") -> StructuredReview:
        if not reason or len(reason.strip()) < 10:
            return StructuredReview(
                has_reasoning=False,
                has_conflict_declaration=False,
                reasoning_quality="empty",
            )

        has_conflict = any(kw in reason for kw in [
            "利益冲突", "利益声明", "冲突声明", "利益相关",
            "conflict of interest", "self-interest",
        ])

        reasoning_indicators = [
            (r"因为|由于|原因是|理由", "causal"),
            (r"同意|反对|赞成|弃权|abstain", "stance"),
            (r"建议|认为|应该|建议", "opinion"),
            (r"证据|数据|事实|记录", "evidence"),
        ]

        matched_types: list[str] = []
        for pattern, quality in reasoning_indicators:
            if re.search(pattern, reason):
                matched_types.append(quality)

        if not matched_types:
            quality = "superficial"
        elif len(matched_types) == 1 and matched_types[0] == "stance":
            quality = "stance_only"
        elif "evidence" in matched_types:
            quality = "evidence_based"
        else:
            quality = "reasoned"

        self_benefit = any(kw in reason for kw in [
            "灵克获得", "灵通获得", "灵研获得", "灵通+获得",
        ])

        issues: list[str] = []
        if quality == "superficial":
            issues.append("理由缺乏实质性内容")
        if quality == "stance_only":
            issues.append("仅表达立场无理由")

        return StructuredReview(
            has_reasoning=quality not in ("empty", "superficial"),
            has_conflict_declaration=has_conflict,
            reasoning_quality="self_benefit" if self_benefit else quality,
            issues=tuple(issues),
        )

    def filter_proposal_votes(self, proposal: dict[str, Any]) -> dict[str, Any]:
        raw_votes = proposal.get("votes", [])
        validated: list[dict[str, Any]] = []
        filtered: list[dict[str, Any]] = []

        for v in raw_votes:
            validation = self.verify_vote(v)
            entry = {**v, "validation": {"valid": validation.valid, "source": validation.source, "issues": list(validation.issues)}}
            if validation.valid:
                validated.append(entry)
            else:
                filtered.append(entry)

        batch_patterns = self.check_batch_pattern(raw_votes)

        return {
            "proposal_id": proposal.get("proposal_id", proposal.get("id", "")),
            "total_votes": len(raw_votes),
            "valid_votes": len(validated),
            "filtered_votes": len(filtered),
            "validated": validated,
            "filtered": filtered,
            "batch_patterns": [{"voter": p.voter, "evidence": p.evidence} for p in batch_patterns],
        }


    def audit_proposals_file(self, proposals_path: Path) -> dict[str, Any]:
        """对 proposals.json 执行完整审计，结果写入 log_dir。"""
        if not proposals_path.exists():
            return {"error": f"文件不存在: {proposals_path}"}

        proposals = json.loads(proposals_path.read_text(encoding="utf-8"))
        results: list[dict[str, Any]] = []
        total_valid = 0
        total_filtered = 0
        forged_count = 0

        for prop in proposals:
            report = self.filter_proposal_votes(prop)
            results.append(report)
            total_valid += report["valid_votes"]
            total_filtered += report["filtered_votes"]
            for bp in report.get("batch_patterns", []):
                if report["filtered_votes"] > 0:
                    forged_count += report["filtered_votes"]

        audit = {
            "audit_time": datetime.now(timezone.utc).isoformat(),
            "auditor": "lingclaude",
            "proposals_file": str(proposals_path),
            "total_proposals": len(proposals),
            "total_valid_votes": total_valid,
            "total_filtered_votes": total_filtered,
            "proposals": results,
        }

        log_file = self.log_dir / f"audit_{int(time.time())}.json"
        log_file.write_text(json.dumps(audit, ensure_ascii=False, indent=2))

        summary_file = self.log_dir / "latest_audit_summary.json"
        summary = {
            "audit_time": audit["audit_time"],
            "total_proposals": audit["total_proposals"],
            "total_valid_votes": total_valid,
            "total_filtered_votes": total_filtered,
        }
        summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2))

        return audit


def _to_dict(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "__dataclass_fields__"):
        return {f: getattr(obj, f) for f in obj.__dataclass_fields__}
    return {}
