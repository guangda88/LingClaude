from __future__ import annotations
"""提案全生命周期管理 — Proposal Lifecycle Manager

核心理念：提案不是一件事，是一段生命。
从创建到终结，每个环节的断裂都要自动检测、自动归因、自动升级。

生命周期阶段：
  created → notified → engaging → discussing → decided → implementing → reviewing → closed

归因维度：
  - 未送达：通知发出去了，成员没有 ack
  - 未关注：通知 ack 了，但没有任何回应
  - 无动机：成员在线但不参与讨论
  - 不值得讨论：提案质量不足，参与后无实质内容

设计原则：
  - 状态变更是唯一的驱动源，通知是副作用
  - 归因基于可观测的信号（ack、回复、在线时间），不猜测意图
  - 每个断链都有对应的升级动作，不只是催票
"""

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from lingclaude.governance.roster import COUNCIL_MEMBER_IDS, get_cn_name

logger = logging.getLogger(__name__)

STATE_DIR = Path.home() / ".lingclaude" / "governance_state"
LIFECYCLE_FILE = STATE_DIR / "proposal_lifecycle.json"


class Phase(str, Enum):
    CREATED = "created"
    NOTIFIED = "notified"
    ENGAGING = "engaging"
    DISCUSSING = "discussing"
    DECIDED = "decided"
    IMPLEMENTING = "implementing"
    REVIEWING = "reviewing"
    CLOSED = "closed"


class Attribution(str, Enum):
    UNKNOWN = "unknown"
    NOT_DELIVERED = "not_delivered"
    NOT_VIEWED = "not_viewed"
    NO_MOTIVATION = "no_motivation"
    NOT_WORTH_DISCUSSING = "not_worth_discussing"
    ENGAGED = "engaged"


class EscalationLevel(str, Enum):
    NONE = "none"
    NUDGE = "nudge"
    DIRECT_MENTION = "direct_mention"
    SYSTEMIC_ALERT = "systemic_alert"


@dataclass
class MemberParticipation:
    member_id: str
    acked: bool = False
    acked_at: float = 0.0
    replied: bool = False
    reply_count: int = 0
    objection_raised: bool = False
    last_activity_at: float = 0.0
    attribution: Attribution = Attribution.UNKNOWN

    def to_dict(self) -> dict:
        return {
            "member_id": self.member_id,
            "acked": self.acked,
            "acked_at": self.acked_at,
            "replied": self.replied,
            "reply_count": self.reply_count,
            "objection_raised": self.objection_raised,
            "last_activity_at": self.last_activity_at,
            "attribution": self.attribution.value,
        }


@dataclass
class PhaseTransition:
    from_phase: Phase
    to_phase: Phase
    timestamp: float
    trigger: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "from": self.from_phase.value,
            "to": self.to_phase.value,
            "timestamp": self.timestamp,
            "trigger": self.trigger,
            "note": self.note,
        }


@dataclass
class LifecycleEvent:
    timestamp: float
    event_type: str
    actor: str = ""
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "actor": self.actor,
            "detail": self.detail,
        }


@dataclass
class ProposalLifecycle:
    proposal_id: str
    phase: Phase = Phase.CREATED
    created_at: float = 0.0
    deadline_hours: float = 72.0
    participation: dict[str, MemberParticipation] = field(default_factory=dict)
    transitions: list[PhaseTransition] = field(default_factory=list)
    events: list[LifecycleEvent] = field(default_factory=list)
    escalation_level: EscalationLevel = EscalationLevel.NONE
    systemic_issue_detected: bool = False
    systemic_issue_note: str = ""
    attribution_summary: str = ""
    outcome_note: str = ""
    meta_issue_type: str = ""
    meta_review_at: float = 0.0
    meta_effective: Optional[bool] = None
    meta_review_note: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()
        if not self.participation:
            for mid in COUNCIL_MEMBER_IDS:
                self.participation[mid] = MemberParticipation(member_id=mid)

    @property
    def elapsed_hours(self) -> float:
        return (time.time() - self.created_at) / 3600

    @property
    def remaining_hours(self) -> float:
        return max(self.deadline_hours - self.elapsed_hours, 0)

    @property
    def is_expired(self) -> bool:
        return self.remaining_hours <= 0

    @property
    def acked_count(self) -> int:
        return sum(1 for p in self.participation.values() if p.acked)

    @property
    def replied_count(self) -> int:
        return sum(1 for p in self.participation.values() if p.replied)

    @property
    def total_members(self) -> int:
        return len(self.participation)

    @property
    def engagement_ratio(self) -> float:
        if not self.participation:
            return 0.0
        engaged = sum(
            1 for p in self.participation.values()
            if p.attribution in (Attribution.ENGAGED, Attribution.NOT_WORTH_DISCUSSING)
        )
        return engaged / len(self.participation)

    def transition_to(self, phase: Phase, trigger: str = "", note: str = "") -> None:
        if self.phase == phase:
            return
        old = self.phase
        self.phase = phase
        self.transitions.append(
            PhaseTransition(
                from_phase=old,
                to_phase=phase,
                timestamp=time.time(),
                trigger=trigger,
                note=note,
            )
        )
        logger.info(
            "提案 %s 阶段迁移: %s → %s (trigger=%s)",
            self.proposal_id, old.value, phase.value, trigger,
        )

    def record_event(self, event_type: str, actor: str = "", detail: str = "") -> None:
        self.events.append(
            LifecycleEvent(
                timestamp=time.time(),
                event_type=event_type,
                actor=actor,
                detail=detail,
            )
        )

    def record_ack(self, member_id: str) -> None:
        p = self.participation.get(member_id)
        if not p:
            return
        if not p.acked:
            p.acked = True
            p.acked_at = time.time()
            self.record_event("ack", member_id)
            if self.phase == Phase.NOTIFIED:
                self.transition_to(Phase.ENGAGING, trigger="first_ack", note=f"{member_id} 已阅")

    def record_reply(self, member_id: str, content: str = "") -> None:
        p = self.participation.get(member_id)
        if not p:
            return
        p.replied = True
        p.reply_count += 1
        p.last_activity_at = time.time()
        self.record_event("reply", member_id, content[:200])
        if self.phase in (Phase.NOTIFIED, Phase.ENGAGING):
            self.transition_to(Phase.DISCUSSING, trigger="first_reply", note=f"{member_id} 开始讨论")

    def record_objection(self, member_id: str) -> None:
        p = self.participation.get(member_id)
        if p:
            p.objection_raised = True
            p.replied = True
            p.last_activity_at = time.time()
            p.attribution = Attribution.ENGAGED
        self.record_event("objection", member_id)

    def run_attribution(self) -> dict[str, Attribution]:
        """根据可观测信号归因每个成员的参与状态。"""
        now = time.time()
        results = {}
        for mid, p in self.participation.items():
            if p.objection_raised or p.reply_count >= 2:
                p.attribution = Attribution.ENGAGED
            elif p.replied:
                p.attribution = Attribution.ENGAGED
            elif p.acked and not p.replied:
                elapsed_since_ack = (now - p.acked_at) / 3600 if p.acked_at else 0
                if elapsed_since_ack > self.deadline_hours * 0.5:
                    p.attribution = Attribution.NO_MOTIVATION
                else:
                    p.attribution = Attribution.NOT_VIEWED
            else:
                p.attribution = Attribution.NOT_DELIVERED
            results[mid] = p.attribution
        return results

    def get_attribution_breakdown(self) -> dict[str, list[str]]:
        """按归因类型分组成员。"""
        breakdown: dict[str, list[str]] = {}
        for mid, p in self.participation.items():
            key = p.attribution.value
            breakdown.setdefault(key, []).append(mid)
        return breakdown

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "phase": self.phase.value,
            "created_at": self.created_at,
            "deadline_hours": self.deadline_hours,
            "escalation_level": self.escalation_level.value,
            "systemic_issue_detected": self.systemic_issue_detected,
            "systemic_issue_note": self.systemic_issue_note,
            "attribution_summary": self.attribution_summary,
            "outcome_note": self.outcome_note,
            "meta_issue_type": self.meta_issue_type,
            "meta_review_at": self.meta_review_at,
            "meta_effective": self.meta_effective,
            "meta_review_note": self.meta_review_note,
            "participation": {mid: p.to_dict() for mid, p in self.participation.items()},
            "transitions": [t.to_dict() for t in self.transitions],
            "events": [e.to_dict() for e in self.events[-50:]],
        }


class LifecycleManager:
    """提案全生命周期管理器。

    职责：
    1. 追踪每个提案的生命周期阶段
    2. 检测断链并归因
    3. 自动升级
    4. 检测系统性问题
    """

    def __init__(self, state_file: Optional[Path] = None):
        self._file = state_file or LIFECYCLE_FILE
        self.lifecycles: dict[str, ProposalLifecycle] = {}
        self._bus: Optional[Any] = None
        self._history_file = STATE_DIR / "lifecycle_history.jsonl"
        self._load()

    @property
    def bus(self):
        if self._bus is None:
            try:
                from lingmessage.lingbus import LingBus
                self._bus = LingBus()
            except Exception as e:
                logger.warning("灵信总线不可用，生命周期通知禁用: %s", e)
        return self._bus

    def create(self, proposal_id: str, deadline_hours: float = 72.0) -> ProposalLifecycle:
        if proposal_id in self.lifecycles:
            return self.lifecycles[proposal_id]
        lc = ProposalLifecycle(
            proposal_id=proposal_id,
            deadline_hours=deadline_hours,
        )
        lc.record_event("created", "system", "提案创建")
        self.lifecycles[proposal_id] = lc
        self._save()
        return lc

    def mark_notified(self, proposal_id: str, thread_id: str = "") -> None:
        lc = self.lifecycles.get(proposal_id)
        if not lc:
            return
        lc.transition_to(Phase.NOTIFIED, trigger="notification_sent", note=f"thread={thread_id}")
        lc.record_event("notified", "system", f"灵信通知已发送 thread={thread_id}")
        self._save()

    def sync_acks(self, proposal_id: str) -> int:
        """从灵信同步 ack 状态。"""
        lc = self.lifecycles.get(proposal_id)
        if not lc or self.bus is None:
            return 0
        count = 0
        for mid, p in lc.participation.items():
            if p.acked:
                continue
            try:
                messages = self.bus.poll(recipient=mid, limit=1)
                if messages:
                    p.acked = True
                    p.acked_at = time.time()
                    lc.record_event("ack", mid, "通过灵信轮询确认")
                    count += 1
            except Exception:
                pass
        if count:
            self._save()
        return count

    def check_health(self) -> list[dict]:
        """检查所有活跃提案的健康状态，返回需要升级的提案列表。"""
        alerts = []
        for proposal_id, lc in self.lifecycles.items():
            if lc.phase in (Phase.CLOSED, Phase.DECIDED):
                continue

            lc.run_attribution()
            breakdown = lc.get_attribution_breakdown()
            lc.attribution_summary = "; ".join(
                f"{k}: {len(v)}人" for k, v in breakdown.items() if v
            )

            not_delivered = breakdown.get("not_delivered", [])
            no_motivation = breakdown.get("no_motivation", [])
            engaged = breakdown.get("engaged", [])

            alert: dict[str, Any] = {
                "proposal_id": proposal_id,
                "phase": lc.phase.value,
                "remaining_hours": lc.remaining_hours,
                "acked": lc.acked_count,
                "replied": lc.replied_count,
                "total": lc.total_members,
                "escalation": EscalationLevel.NONE.value,
                "action": "",
            }

            if lc.acked_count == 0 and lc.elapsed_hours > 2:
                lc.escalation_level = EscalationLevel.SYSTEMIC_ALERT
                alert["escalation"] = EscalationLevel.SYSTEMIC_ALERT.value
                alert["action"] = "systemic_no_acks"
                alerts.append(alert)
                lc.systemic_issue_detected = True
                lc.systemic_issue_note = (
                    f"通知发出{lc.elapsed_hours:.1f}小时后零确认。"
                    f"可能原因：通知通道故障 或 成员未上线。"
                )
            elif not_delivered and lc.remaining_hours < lc.deadline_hours * 0.5:
                lc.escalation_level = EscalationLevel.DIRECT_MENTION
                alert["escalation"] = EscalationLevel.DIRECT_MENTION.value
                alert["action"] = f"direct_mention:{','.join(not_delivered)}"
                alert["detail"] = f"未送达成员: {', '.join(get_cn_name(m) for m in not_delivered)}"
                alerts.append(alert)
            elif no_motivation and not engaged and lc.remaining_hours < lc.deadline_hours * 0.25:
                lc.escalation_level = EscalationLevel.NUDGE
                alert["escalation"] = EscalationLevel.NUDGE.value
                alert["action"] = f"nudge:{','.join(no_motivation)}"
                alert["detail"] = f"已阅未参与: {', '.join(get_cn_name(m) for m in no_motivation)}"
                alerts.append(alert)
            elif not engaged and lc.remaining_hours < lc.deadline_hours * 0.1:
                alert["action"] = "prepare_auto_pass"
                alerts.append(alert)

        self._save()
        return alerts

    def detect_systemic_issues(self) -> list[dict]:
        """跨提案检测系统性问题。"""
        issues = []
        recent = [
            lc for lc in self.lifecycles.values()
            if lc.elapsed_hours < 168
        ]
        if len(recent) < 2:
            return issues

        zero_engagement = sum(1 for lc in recent if lc.engagement_ratio == 0)
        if zero_engagement >= 2:
            ratio = zero_engagement / len(recent)
            issues.append({
                "type": "chronic_low_engagement",
                "severity": "high" if ratio > 0.5 else "medium",
                "detail": f"近7天{len(recent)}个提案中{zero_engagement}个零参与({ratio:.0%})",
                "suggestion": "审查：通知是否送达？提案是否与成员相关？参与成本是否过高？",
            })

        always_silent: dict[str, int] = {}
        for lc in recent:
            for mid, p in lc.participation.items():
                if p.attribution in (Attribution.NOT_DELIVERED, Attribution.NO_MOTIVATION):
                    always_silent[mid] = always_silent.get(mid, 0) + 1
        for mid, count in always_silent.items():
            if count >= 2:
                cn = get_cn_name(mid)
                issues.append({
                    "type": "chronically_absent_member",
                    "severity": "medium",
                    "detail": f"{cn} 连续{count}个提案未参与",
                    "suggestion": f"检查{cn}是否在线、通知是否送达、或是否需要调整法定人数基数",
                })

        return issues

    def finalize(self, proposal_id: str, outcome: str, note: str = "", meta_issue_type: str = "") -> None:
        lc = self.lifecycles.get(proposal_id)
        if not lc:
            return
        if lc.phase in (Phase.DECIDED, Phase.CLOSED):
            return
        lc.run_attribution()
        breakdown = lc.get_attribution_breakdown()
        lc.attribution_summary = "; ".join(
            f"{k}: {len(v)}人" for k, v in breakdown.items() if v
        )
        lc.outcome_note = note or outcome
        if meta_issue_type:
            lc.meta_issue_type = meta_issue_type
        if lc.meta_issue_type and outcome in ("passed", "passed_s1_filtered"):
            lc.meta_review_at = time.time() + 72 * 3600
        lc.transition_to(Phase.DECIDED, trigger="resolution", note=outcome)
        lc.record_event("decided", "system", outcome)
        self._append_history(lc)
        self._save()

    def close(self, proposal_id: str) -> None:
        lc = self.lifecycles.get(proposal_id)
        if not lc:
            return
        lc.transition_to(Phase.CLOSED, trigger="lifecycle_end")
        lc.record_event("closed", "system", "生命周期结束")
        self._save()

    def check_meta_effectiveness(self) -> list[dict]:
        """检查已通过的元提案是否解决了它要解决的问题。

        元提案闭环追踪：
          创建 → 通过 → 72小时后复查 → 问题还在吗？
          - 消失了 → effective=True, 经验沉淀
          - 还在   → effective=False, 升级通知

        这是治理过程的反思环节：不仅发现问题、解决问题，
        还要验证问题是否真的被解决了。
        """
        now = time.time()
        results = []
        for proposal_id, lc in self.lifecycles.items():
            if not lc.meta_issue_type:
                continue
            if lc.meta_review_at == 0 or now < lc.meta_review_at:
                continue
            if lc.meta_effective is not None:
                continue

            current_issues = self.detect_systemic_issues()
            current_types = {i["type"] for i in current_issues}

            resolved = lc.meta_issue_type not in current_types
            lc.meta_effective = resolved

            if resolved:
                lc.meta_review_note = (
                    f"元提案针对的 {lc.meta_issue_type} 问题已解决。"
                )
                lc.record_event("meta_review", "system", f"问题已解决: {lc.meta_issue_type}")
                self._append_effectiveness_record(lc, resolved=True)
            else:
                detail = ""
                for issue in current_issues:
                    if issue["type"] == lc.meta_issue_type:
                        detail = issue.get("detail", "")
                        break
                lc.meta_review_note = (
                    f"元提案通过72小时后，{lc.meta_issue_type} 问题仍然存在: {detail}"
                )
                lc.record_event("meta_review", "system", f"问题未解决: {lc.meta_issue_type}")
                self._append_effectiveness_record(lc, resolved=False)
                self._notify_meta_ineffective(lc, detail)

            logger.info(
                "元提案效果复查: %s -> %s (issue=%s)",
                proposal_id, "已解决" if resolved else "未解决", lc.meta_issue_type,
            )
            results.append({
                "proposal_id": proposal_id,
                "issue_type": lc.meta_issue_type,
                "effective": resolved,
                "note": lc.meta_review_note,
            })
            self._save()
        return results

    def _notify_meta_ineffective(self, lc: ProposalLifecycle, detail: str) -> None:
        if self.bus is None:
            return
        try:
            body = (
                f"【元提案效果复查】{lc.proposal_id}\n\n"
                f"问题类型: {lc.meta_issue_type}\n"
                f"结果: 未解决\n"
                f"当前状况: {detail}\n\n"
                f"该元提案通过已满72小时，但问题仍然存在。\n"
                f"建议：重新评估治理策略，考虑更深层的结构性调整。"
            )
            self.bus.open_thread(
                topic=f"[复查] {lc.meta_issue_type} 仍未解决",
                sender="lingclaude",
                recipients=COUNCIL_MEMBER_IDS,
                channel="council",
                subject=f"元提案复查: {lc.meta_issue_type} 未解决",
                body=body,
            )
        except Exception as e:
            logger.warning("元提案复查通知失败: %s", e)

    def _append_effectiveness_record(self, lc: ProposalLifecycle, resolved: bool) -> None:
        self._history_file.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "type": "meta_effectiveness",
            "proposal_id": lc.proposal_id,
            "issue_type": lc.meta_issue_type,
            "effective": resolved,
            "reviewed_at": time.time(),
            "created_at": lc.created_at,
        }
        with open(self._history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _append_history(self, lc: ProposalLifecycle) -> None:
        self._history_file.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "proposal_id": lc.proposal_id,
            "created_at": lc.created_at,
            "decided_at": lc.transitions[-1].timestamp if lc.transitions else 0,
            "phase": lc.phase.value,
            "acked": lc.acked_count,
            "replied": lc.replied_count,
            "total": lc.total_members,
            "escalation_level": lc.escalation_level.value,
            "systemic_issue": lc.systemic_issue_detected,
            "attribution": lc.attribution_summary,
        }
        with open(self._history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _load(self) -> None:
        if not self._file.exists():
            return
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            for pid, ld in data.get("lifecycles", {}).items():
                lc = ProposalLifecycle(
                    proposal_id=ld["proposal_id"],
                    phase=Phase(ld.get("phase", "created")),
                    created_at=ld.get("created_at", 0),
                    deadline_hours=ld.get("deadline_hours", 72),
                    escalation_level=EscalationLevel(ld.get("escalation_level", "none")),
                    systemic_issue_detected=ld.get("systemic_issue_detected", False),
                    systemic_issue_note=ld.get("systemic_issue_note", ""),
                    attribution_summary=ld.get("attribution_summary", ""),
                    outcome_note=ld.get("outcome_note", ""),
                    meta_issue_type=ld.get("meta_issue_type", ""),
                    meta_review_at=ld.get("meta_review_at", 0),
                    meta_effective=ld.get("meta_effective"),
                    meta_review_note=ld.get("meta_review_note", ""),
                )
                for mid, pd in ld.get("participation", {}).items():
                    lc.participation[mid] = MemberParticipation(
                        member_id=pd["member_id"],
                        acked=pd.get("acked", False),
                        acked_at=pd.get("acked_at", 0),
                        replied=pd.get("replied", False),
                        reply_count=pd.get("reply_count", 0),
                        objection_raised=pd.get("objection_raised", False),
                        last_activity_at=pd.get("last_activity_at", 0),
                        attribution=Attribution(pd.get("attribution", "unknown")),
                    )
                for td in ld.get("transitions", []):
                    lc.transitions.append(PhaseTransition(
                        from_phase=Phase(td["from"]),
                        to_phase=Phase(td["to"]),
                        timestamp=td["timestamp"],
                        trigger=td.get("trigger", ""),
                        note=td.get("note", ""),
                    ))
                for ed in ld.get("events", []):
                    lc.events.append(LifecycleEvent(
                        timestamp=ed["timestamp"],
                        event_type=ed["event_type"],
                        actor=ed.get("actor", ""),
                        detail=ed.get("detail", ""),
                    ))
                self.lifecycles[pid] = lc
        except Exception as e:
            logger.warning("加载生命周期状态失败: %s", e)

    def _save(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        data = {"lifecycles": {pid: lc.to_dict() for pid, lc in self.lifecycles.items()}}
        self._file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
