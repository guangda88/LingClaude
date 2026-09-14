from __future__ import annotations

"""异议制治理引擎 — Evidence-Based Objection Governance

取代投票制。核心理念来自灵研论文 "Mechanisms Over Introspection"：

  投票制问：你喜欢这个提案吗？（主观，L3 脆弱）
  异议制问：有什么证据表明这个提案会失败？（客观，L3 鲁棒）

工作流程：
  1. 创建提案 → 自动爆破半径分析
  2. 发布分析 → 任何人可提异议（必须附带证据）
  3. 无阻塞性异议 → 自动通过
  4. 有阻塞性异议 → 需要解决后才能通过

没有人做过的事：把 L3 幻觉论文的认知状态检测嵌入治理决策流程。
"""

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from lingclaude.governance.cognitive_state import CognitiveAssessment, CognitiveState, CognitiveStateDetector
from lingclaude.governance.roster import COUNCIL_MEMBER_IDS, get_cn_name

logger = logging.getLogger(__name__)

STATE_DIR = Path.home() / ".lingclaude" / "governance_state"
STATE_FILE = STATE_DIR / "governance_v2.json"

_TEST_TITLE_MAX_LEN = 3
_TEST_PROPOSERS = {"a", "b", "c", "test", "tester"}


def _is_test_proposal(proposer: str, title: str) -> bool:
    if proposer.lower() in _TEST_PROPOSERS:
        return True
    if len(title.strip()) <= _TEST_TITLE_MAX_LEN:
        return True
    return False


class ObjectionSeverity(Enum):
    BLOCKING = "blocking"
    CONCERN = "concern"
    NOTE = "note"


class ProposalStatus(Enum):
    ANALYSIS = "analysis"
    OPEN = "open"
    OBJECTION_RAISED = "objection_raised"
    PASSED = "passed"
    FAILED = "failed"
    WITHDRAWN = "withdrawn"


@dataclass
class Objection:
    objector: str
    evidence: str
    severity: ObjectionSeverity = ObjectionSeverity.CONCERN
    category: str = ""
    cognitive_assessment: Optional[CognitiveAssessment] = None
    created_at: float = 0.0

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()


@dataclass
class BlastAnalysis:
    affected_agents: list[str] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    affected_services: list[str] = field(default_factory=list)
    risk_level: str = "low"
    reversible: bool = True
    rollback_plan: str = ""

    def to_dict(self) -> dict:
        return {
            "affected_agents": self.affected_agents,
            "affected_files": self.affected_files,
            "affected_services": self.affected_services,
            "risk_level": self.risk_level,
            "reversible": self.reversible,
            "rollback_plan": self.rollback_plan,
        }


@dataclass
class ProposalV2:
    proposal_id: str
    proposer: str
    title: str
    body: str = ""
    status: ProposalStatus = ProposalStatus.ANALYSIS
    blast_analysis: Optional[BlastAnalysis] = None
    objections: list[Objection] = field(default_factory=list)
    deadline_hours: float = 1.0
    created_at: float = 0.0
    decided_at: float = 0.0
    decision_note: str = ""
    cognitive_audit: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()

    @property
    def remaining_hours(self) -> float:
        elapsed = (time.time() - self.created_at) / 3600
        return max(self.deadline_hours - elapsed, 0)

    @property
    def is_expired(self) -> bool:
        return self.remaining_hours <= 0

    @property
    def blocking_objections(self) -> list[Objection]:
        return [o for o in self.objections if o.severity == ObjectionSeverity.BLOCKING]

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "proposer": self.proposer,
            "title": self.title,
            "body": self.body,
            "status": self.status.value,
            "blast_analysis": self.blast_analysis.to_dict() if self.blast_analysis else None,
            "objections": [
                {
                    "objector": o.objector,
                    "evidence": o.evidence[:200],
                    "severity": o.severity.value,
                    "category": o.category,
                    "cognitive_state": o.cognitive_assessment.state.value if o.cognitive_assessment else None,
                    "created_at": o.created_at,
                }
                for o in self.objections
            ],
            "deadline_hours": self.deadline_hours,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "decision_note": self.decision_note,
            "cognitive_audit": self.cognitive_audit,
        }


class GovernanceEngine:
    """L3-Aware Objection-Based Governance Engine"""

    def __init__(self, state_file: Optional[Path] = None):
        self._file = state_file or STATE_FILE
        self._detector = CognitiveStateDetector()
        self._bus: Optional[Any] = None
        self._nudge_sent: set[str] = set()
        self._proposal_threads: dict[str, str] = {}
        self._lifecycle: Optional[Any] = None
        self._meta_proposal_cooldown: float = 0.0
        self.proposals: dict[str, ProposalV2] = {}
        self._load()

    @property
    def lifecycle(self):
        if self._lifecycle is None:
            from lingclaude.governance.proposal_lifecycle import LifecycleManager
            self._lifecycle = LifecycleManager()
        return self._lifecycle

    @property
    def bus(self):
        if self._bus is None:
            try:
                from lingmessage.lingbus import LingBus
                self._bus = LingBus()
            except Exception as e:
                logger.warning("灵信总线不可用，通知禁用: %s", e)
        return self._bus

    def create_proposal(
        self,
        proposal_id: str,
        proposer: str,
        title: str,
        body: str = "",
        deadline_hours: float = 1.0,
        notify: bool = True,
    ) -> ProposalV2:
        # E4(灵元1.0 P1): notify=False 供机器路由使用——完全跳过通知路径，
        # 不触发 bus 属性的灵信自动连接（此前仅测试提案静默，机器提案无开关）。
        if proposal_id in self.proposals:
            raise ValueError(f"提案 {proposal_id} 已存在")

        proposal = ProposalV2(
            proposal_id=proposal_id,
            proposer=proposer,
            title=title,
            body=body,
            deadline_hours=deadline_hours,
        )
        proposal.blast_analysis = self._auto_blast_analysis(proposal)
        proposal.status = ProposalStatus.OPEN
        self.proposals[proposal_id] = proposal
        self._save()
        logger.info("提案已创建: %s (爆破半径=%s)", proposal_id, proposal.blast_analysis.risk_level)
        self.lifecycle.create(proposal_id, deadline_hours=deadline_hours)
        if not notify:
            logger.info("跳过机器提案通知 (notify=False): %s", proposal_id)
        elif not _is_test_proposal(proposer, title):
            self._notify_new_proposal(proposal)
        else:
            logger.info("跳过测试提案通知: %s (proposer=%s, title=%s)", proposal_id, proposer, title)
        if self._proposal_threads.get(proposal_id):
            self.lifecycle.mark_notified(proposal_id, self._proposal_threads[proposal_id])
        return proposal

    def raise_objection(
        self,
        proposal_id: str,
        objector: str,
        evidence: str,
        severity: str = "concern",
        category: str = "",
        response_context: Optional[dict] = None,
    ) -> Objection:
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            raise ValueError(f"提案 {proposal_id} 不存在")
        if proposal.status not in (ProposalStatus.OPEN, ProposalStatus.OBJECTION_RAISED):
            raise ValueError(f"提案状态为 {proposal.status.value}，无法提出异议")

        sev = ObjectionSeverity(severity)

        assessment = self._detector.assess(evidence, response_context)

        objection = Objection(
            objector=objector,
            evidence=evidence,
            severity=sev,
            category=category,
            cognitive_assessment=assessment,
        )

        proposal.objections.append(objection)
        if sev == ObjectionSeverity.BLOCKING:
            proposal.status = ProposalStatus.OBJECTION_RAISED

        proposal.cognitive_audit[objector] = {
            "state": assessment.state.value,
            "confidence": assessment.confidence,
            "genuine": assessment.is_genuine_deliberation,
        }

        self._save()
        logger.info(
            "异议: %s 对 %s 提出 %s (认知=%s, 真实=%s)",
            objector, proposal_id, severity,
            assessment.state.value, assessment.is_genuine_deliberation,
        )
        self._notify_objection(proposal, objection)
        lc = self.lifecycle.lifecycles.get(proposal_id)
        if lc:
            lc.record_objection(objector)
        return objection

    def check_deadlines(self) -> list[tuple[str, str]]:
        results = []
        for pid, proposal in list(self.proposals.items()):
            if proposal.status not in (ProposalStatus.OPEN, ProposalStatus.OBJECTION_RAISED):
                continue
            if not proposal.is_expired:
                continue

            blocking = proposal.blocking_objections
            genuine_blocking = [
                o for o in blocking
                if o.cognitive_assessment and o.cognitive_assessment.is_genuine_deliberation
            ]

            if genuine_blocking:
                proposal.status = ProposalStatus.FAILED
                proposal.decided_at = time.time()
                proposal.decision_note = (
                    f"因 {len(genuine_blocking)} 个真实阻塞异议而失败 "
                    f"(共 {len(blocking)} 阻塞, {len(proposal.objections)} 异议)"
                )
                results.append((pid, "failed"))
            elif blocking and not genuine_blocking:
                for o in blocking:
                    proposal.status = ProposalStatus.OPEN
                    proposal.objections.remove(o)
                proposal.status = ProposalStatus.PASSED
                proposal.decided_at = time.time()
                proposal.decision_note = (
                    f"所有阻塞异议均来自非真实审议(S1默认态)，已忽略。"
                    f"自动通过。{len(proposal.objections)} 非阻塞异议保留。"
                )
                results.append((pid, "passed_s1_filtered"))
            else:
                proposal.status = ProposalStatus.PASSED
                proposal.decided_at = time.time()
                proposal.decision_note = (
                    f"无异议，自动通过。"
                    f"认知审计: {self._summarize_cognitive_audit(proposal)}"
                )
                results.append((pid, "passed"))

            self._save()
            self._notify_result(proposal)
            status_str = "passed" if proposal.status == ProposalStatus.PASSED else "failed"
            self.lifecycle.finalize(pid, status_str, proposal.decision_note)
        return results

    def check_and_nudge(self) -> list[str]:
        """检查需要催票的提案（过期前25%时间），返回催票结果。"""
        results = []
        for pid, proposal in self.proposals.items():
            if proposal.status not in (ProposalStatus.OPEN, ProposalStatus.OBJECTION_RAISED):
                continue
            if pid in self._nudge_sent:
                continue
            remaining = proposal.remaining_hours
            total = proposal.deadline_hours
            if remaining <= total * 0.25 and remaining > 0:
                self._notify_nudge(proposal)
                self._nudge_sent.add(pid)
                results.append(pid)
        return results

    def run_lifecycle_health_check(self) -> list[dict]:
        """执行生命周期健康检查：检测断链、归因、自动升级。

        自觉→自决→进化 硬化链：
          自觉：detect_systemic_issues() 发现系统性问题
          自决：自动生成元提案，把问题变成可审议的议题
          进化：元提案走完生命周期后，经验沉淀到 history
        """
        alerts = self.lifecycle.check_health()
        systemic = self.lifecycle.detect_systemic_issues()
        for issue in systemic:
            self._auto_meta_proposal(issue)
        return alerts + [{"systemic": True, **i} for i in systemic]

    def _auto_meta_proposal(self, issue: dict) -> Optional[ProposalV2]:
        """系统性问题自动生成元提案。

        元提案是关于治理本身的提案。它的创建不依赖人的自觉，
        而是代码硬化的条件触发——这就是"自觉→自决→进化"链条中
        "进化"的部分：从错误中发现模式，自动启动修正流程。

        冷却期24小时，同一类元提案不重复生成。
        """
        now = time.time()
        if now - self._meta_proposal_cooldown < 86400:
            return None

        issue_type = issue.get("type", "unknown")
        severity = issue.get("severity", "medium")
        detail = issue.get("detail", "")
        suggestion = issue.get("suggestion", "")

        proposal_id = f"META-{issue_type}-{int(now)}"
        title = f"[元提案] 系统性问题：{issue_type}"
        body = (
            f"本提案由治理引擎自动生成。\n\n"
            f"检测到系统性问题：\n"
            f"{detail}\n\n"
            f"建议行动：\n"
            f"{suggestion}\n\n"
            f"---\n"
            f"严重性: {severity}\n"
            f"来源: lifecycle_manager.detect_systemic_issues()\n"
            f"自动化: 自觉→自决→进化 链条中的进化环节\n"
            f"\n请审议：是否需要调整治理参数？是否需要主动联系缺席成员？"
        )

        try:
            proposal = self.create_proposal(
                proposal_id=proposal_id,
                proposer="lingclaude",
                title=title,
                body=body,
                deadline_hours=72.0,
            )
            self._meta_proposal_cooldown = now
            lc = self.lifecycle.lifecycles.get(proposal_id)
            if lc:
                lc.meta_issue_type = issue_type
                self.lifecycle._save()
            logger.info("元提案自动生成: %s (%s)", proposal_id, issue_type)
            return proposal
        except Exception as e:
            logger.warning("元提案生成失败: %s", e)
            return None

    def _notify_new_proposal(self, proposal: ProposalV2) -> None:
        if self.bus is None:
            return
        try:
            recipients = [m for m in COUNCIL_MEMBER_IDS if m != proposal.proposer]
            cn = get_cn_name(proposal.proposer)
            body = (
                f"【新提案】{proposal.title}\n\n"
                f"{proposal.body}\n\n"
                f"---\n"
                f"提案人: {cn}\n"
                f"截止: {proposal.deadline_hours}小时后\n"
                f"爆破半径: {proposal.blast_analysis.risk_level if proposal.blast_analysis else '未分析'}\n"
                f"可逆: {'是' if proposal.blast_analysis and proposal.blast_analysis.reversible else '否'}\n\n"
                f"异议制：无阻塞性异议则自动通过。\n"
                f"提异议:\n"
                f"  lingflow-plus governance object {proposal.proposal_id} <evidence> --severity blocking\n\n"
                f"或直接在灵信回复本消息说明异议。"
            )
            tid, _ = self.bus.open_thread(
                topic=f"[审议] {proposal.title}",
                sender="lingclaude",
                recipients=recipients,
                channel="council",
                subject=f"新提案: {proposal.title}",
                body=body,
            )
            self._proposal_threads[proposal.proposal_id] = tid
            logger.info("审议提案已通过灵信通知: %s -> %s (thread=%s)", proposal.proposal_id, recipients, tid)
        except Exception as e:
            logger.warning("提案通知发送失败: %s", e)

    def _post_or_open_thread(self, proposal: ProposalV2, body: str, topic: str, subject: str, reply_subject: str | None = None) -> None:
        """向提案线程发回复；线程不存在则开新线程。

        收敛: _notify_objection/_notify_nudge/_notify_result 三处逐字相同的
        tid 查找 + post_reply/open_thread 分支（维护点 3→1）。

        reply_subject: 发回复用的 subject；为 None 时与 open_thread 相同。
        _notify_result 的回复 subject 带决议状态（与原行为一致）。
        """
        tid = self._proposal_threads.get(proposal.proposal_id)
        if tid:
            self.bus.post_reply(
                thread_id=tid,
                sender="lingclaude",
                recipient="all",
                body=body,
                subject=reply_subject if reply_subject is not None else subject,
            )
        else:
            self.bus.open_thread(
                topic=topic,
                sender="lingclaude",
                recipients=COUNCIL_MEMBER_IDS,
                channel="council",
                subject=subject,
                body=body,
            )

    def _notify_safe(self, proposal: ProposalV2, build_body, topic: str, subject: str,
                     reply_subject: str | None = None, log_fmt: str = "通知发送失败: %s") -> None:
        """收敛: _notify_objection/_notify_nudge/_notify_result 三处逐字相同的
        bus 判空 + 测试提案跳过 + try/except 包裹骨架（维护点 3→1）。

        build_body: 无参可调用，返回通知正文。topic/subject/reply_subject
        由各通知显式传入（含 reply_subject 差异与异议人 cn 拼接），保真。
        """
        if self.bus is None:
            return
        if _is_test_proposal(proposal.proposer, proposal.title):
            return
        try:
            body = build_body()
            self._post_or_open_thread(
                proposal, body,
                topic=topic,
                subject=subject,
                reply_subject=reply_subject,
            )
        except Exception as e:
            logger.warning(log_fmt, e)

    def _notify_objection(self, proposal: ProposalV2, objection: Objection) -> None:
        cn = get_cn_name(objection.objector)
        self._notify_safe(
            proposal,
            lambda: (
                f"【异议】{proposal.title}\n\n"
                f"异议人: {cn}\n"
                f"严重性: {objection.severity.value}\n"
                f"证据: {objection.evidence[:500]}\n"
                f"认知评估: {objection.cognitive_assessment.state.value if objection.cognitive_assessment else '无'}\n"
            ),
            topic=f"[异议] {proposal.title}",
            subject=f"异议: {cn} 对 {proposal.title}",
            log_fmt="异议通知发送失败: %s",
        )

    def _notify_nudge(self, proposal: ProposalV2) -> None:
        self._notify_safe(
            proposal,
            lambda: (
                f"【催票】{proposal.title}\n\n"
                f"剩余时间: {proposal.remaining_hours:.1f}小时\n"
                f"当前异议数: {len(proposal.objections)}\n"
                f"阻塞性异议: {len(proposal.blocking_objections)}\n\n"
                f"如无异议将自动通过。请尽快审阅。"
            ),
            topic=f"[催票] {proposal.title}",
            subject=f"催票: {proposal.title}",
            log_fmt="催票通知发送失败: %s",
        )

    def _notify_result(self, proposal: ProposalV2) -> None:
        self._notify_safe(
            proposal,
            lambda: (
                f"【决议】{proposal.title}\n\n"
                f"结果: {proposal.status.value}\n"
                f"{proposal.decision_note}\n\n"
                f"提案人: {get_cn_name(proposal.proposer)}"
            ),
            topic=f"[决议] {proposal.title}",
            subject=f"决议: {proposal.title}",
            reply_subject=f"决议: {proposal.title} → {proposal.status.value}",
            log_fmt="决议通知发送失败: %s",
        )

    def audit_thread(
        self,
        messages: list[dict],
        context: Optional[dict] = None,
    ) -> dict[str, dict]:
        results = self._detector.assess_thread(messages, context)
        genuine, total = self._detector.count_genuine(results)
        return {
            "assessments": results,
            "summary": {
                "genuine_count": genuine,
                "total_count": total,
                "genuine_ratio": round(genuine / max(total, 1), 2),
                "verdict": self._governance_verdict(genuine, total),
            },
        }

    def _governance_verdict(self, genuine: int, total: int) -> str:
        ratio = genuine / max(total, 1)
        if ratio >= 0.5:
            return "审议有效：过半参与者处于真实审议状态"
        elif ratio >= 0.2:
            return "审议可疑：大部分回复为默认态，少量真实审议"
        else:
            return "审议无效：几乎全部为默认态回复，无真实审议"

    def _summarize_cognitive_audit(self, proposal: ProposalV2) -> str:
        if not proposal.cognitive_audit:
            return "无参与者认知数据"
        states = [v["state"] for v in proposal.cognitive_audit.values()]
        s0_count = states.count("S0")
        s1_count = states.count("S1")
        return f"S0={s0_count}, S1={s1_count}, 其他={len(states) - s0_count - s1_count}"

    def _auto_blast_analysis(self, proposal: ProposalV2) -> BlastAnalysis:
        text = f"{proposal.title} {proposal.body}".lower()
        agents = []
        for member_id in COUNCIL_MEMBER_IDS:
            if member_id.lower() in text:
                agents.append(member_id)
        risk = "low"
        reversible = True
        for keyword in ["重启", "restart", "删除", "delete", "全局", "global", "配置", "config"]:
            if keyword in text:
                risk = "medium"
                reversible = "删除" not in text and "delete" not in text
                break
        for keyword in ["安全", "security", "凭据", "credential", "密钥", "secret"]:
            if keyword in text:
                risk = "high"
                reversible = False
                break
        return BlastAnalysis(
            affected_agents=agents or list(COUNCIL_MEMBER_IDS[:3]),
            risk_level=risk,
            reversible=reversible,
            rollback_plan="git revert" if reversible else "需手动检查并恢复",
        )

    def _load(self) -> None:
        if not self._file.exists():
            return
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            for pid, pd in data.get("proposals", {}).items():
                p = ProposalV2(
                    proposal_id=pd["proposal_id"],
                    proposer=pd["proposer"],
                    title=pd["title"],
                    body=pd.get("body", ""),
                    status=ProposalStatus(pd["status"]),
                    deadline_hours=pd.get("deadline_hours", 1.0),
                    created_at=pd.get("created_at", 0),
                    decided_at=pd.get("decided_at", 0),
                    decision_note=pd.get("decision_note", ""),
                    cognitive_audit=pd.get("cognitive_audit", {}),
                )
                if pd.get("blast_analysis"):
                    ba = pd["blast_analysis"]
                    p.blast_analysis = BlastAnalysis(
                        affected_agents=ba.get("affected_agents", []),
                        affected_files=ba.get("affected_files", []),
                        affected_services=ba.get("affected_services", []),
                        risk_level=ba.get("risk_level", "low"),
                        reversible=ba.get("reversible", True),
                        rollback_plan=ba.get("rollback_plan", ""),
                    )
                for od in pd.get("objections", []):
                    obj = Objection(
                        objector=od["objector"],
                        evidence=od["evidence"],
                        severity=ObjectionSeverity(od["severity"]),
                        category=od.get("category", ""),
                        created_at=od.get("created_at", 0),
                    )
                    cs = od.get("cognitive_state")
                    if cs:
                        obj.cognitive_assessment = CognitiveAssessment(
                            state=CognitiveState(cs),
                            confidence=0.5,
                        )
                    p.objections.append(obj)
                self.proposals[pid] = p
        except Exception as e:
            logger.warning("加载治理状态失败: %s", e)

    def _save(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        data = {"proposals": {pid: p.to_dict() for pid, p in self.proposals.items()}}
        self._file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
