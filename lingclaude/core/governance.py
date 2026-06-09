from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GovernanceCheckResult:
    passed: bool
    checks: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()
    error: str | None = None


@dataclass
class ConflictRule:
    rule_id: str
    description: str
    check_fn: str
    severity: str = "warning"


@dataclass
class GovernanceGate:
    enabled: bool = True
    rules_path: Path | None = None
    agent_id: str = ""
    log_dir: Path = field(default_factory=lambda: Path.home() / ".lingclaude" / "governance_logs")
    _rules: list[ConflictRule] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if self.rules_path and self.rules_path.exists():
            self._load_rules(self.rules_path)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _load_rules(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for item in data.get("rules", []):
                self._rules.append(ConflictRule(
                    rule_id=item.get("id", ""),
                    description=item.get("description", ""),
                    check_fn=item.get("check_fn", ""),
                    severity=item.get("severity", "warning"),
                ))
        except (json.JSONDecodeError, OSError):
            pass

    def check(
        self,
        action: str,
        subject: str = "",
        content: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> GovernanceCheckResult:
        if not self.enabled:
            return GovernanceCheckResult(passed=True)

        checks: list[dict[str, Any]] = []
        warnings: list[str] = []

        c1 = self._check_self_nominating(action, content)
        checks.append(c1)
        if not c1["passed"]:
            return GovernanceCheckResult(
                passed=False,
                checks=tuple(checks),
                warnings=tuple(warnings),
                error=c1.get("error", ""),
            )

        c2 = self._check_self_benefiting(action, content)
        checks.append(c2)
        if c2.get("warning"):
            warnings.append(c2["warning"])

        c3 = self._check_power_concentration(action, content, metadata)
        checks.append(c3)
        if c3.get("warning"):
            warnings.append(c3["warning"])

        c4 = self._check_prior_inconsistency(action, content, metadata)
        checks.append(c4)
        if not c4["passed"]:
            return GovernanceCheckResult(
                passed=False,
                checks=tuple(checks),
                warnings=tuple(warnings),
                error=c4.get("error", ""),
            )

        c5 = self._check_tier_change_conflict(action, content, metadata)
        checks.append(c5)
        if not c5["passed"]:
            return GovernanceCheckResult(
                passed=False,
                checks=tuple(checks),
                warnings=tuple(warnings),
                error=c5.get("error", ""),
            )
        if c5.get("warning"):
            warnings.append(c5["warning"])

        self._log_check(action, subject, checks, warnings)

        return GovernanceCheckResult(
            passed=True,
            checks=tuple(checks),
            warnings=tuple(warnings),
        )

    def _check_self_nominating(self, action: str, content: str) -> dict[str, Any]:
        if action not in ("nominate", "appoint", "assign_role", "propose"):
            return {"check": "self_nominating", "passed": True}

        if not self.agent_id or not content:
            return {"check": "self_nominating", "passed": True}

        agent_names = {
            "lingclaude": ["灵克", "lingclaude", "lingclaude"],
            "lingflow_plus": ["灵通+", "lingflow_plus", "lingflowplus"],
            "lingflow": ["灵通", "lingflow", "lingflow"],
            "lingresearch": ["灵研", "lingresearch", "lingresearch"],
        }
        my_names = agent_names.get(self.agent_id, [self.agent_id])
        content_lower = content.lower()

        nomination_patterns = [
            r"推选\s*(\S+?)\s*为",
            r"提名\s*(\S+?)(?:[，。、,.\s]|$)",
            r"任命\s*(\S+?)\s*为",
            r"指定\s*(\S+?)\s*(?:为|担任)",
            r"(?:nominate|propose|elect)\s+(\S+?)(?:\s|[,.\)]|$)",
            r"(?:as|for)\s+(?:the\s+)?(?:permanent\s+)?(?:council|member|chair)(?:\s+member)?[,.]?\s*(?:I\s+)?(?:propose|nominate)\s+(\S+)",
        ]

        self_ref_patterns = [
            (r"我.{0,5}提名\s*(自己|本人)", "第一人称提名自己"),
            (r"我.{0,5}推选\s*(自己|本人)", "第一人称推选自己"),
            (r"我.{0,5}任命\s*(自己|本人)", "第一人称任命自己"),
        ]
        for pat, desc in self_ref_patterns:
            if re.search(pat, content):
                return {
                    "check": "self_nominating",
                    "passed": False,
                    "error": f"自提名检测: agent '{self.agent_id}' 使用第一人称{desc}",
                }

        for name in my_names:
            if name.lower() in content_lower:
                for pattern in nomination_patterns:
                    for match in re.finditer(pattern, content, re.IGNORECASE):
                        nominated = match.group(1)
                        if name.lower() in nominated.lower():
                            return {
                                "check": "self_nominating",
                                "passed": False,
                                "error": f"自提名检测: agent '{self.agent_id}' 在 {action} 中提名了自己 ('{name}')",
                            }

        for pattern in nomination_patterns:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                nominated = match.group(1)
                for name in my_names:
                    if name.lower() in nominated.lower():
                        return {
                            "check": "self_nominating",
                            "passed": False,
                            "error": f"自提名检测: agent '{self.agent_id}' 在 {action} 中提名了自己 ('{nominated}')",
                        }

        return {"check": "self_nominating", "passed": True}

    def _check_self_benefiting(self, action: str, content: str) -> dict[str, Any]:
        if not self.agent_id or not content:
            return {"check": "self_benefiting", "passed": True}

        benefit_keywords_map = {
            "lingclaude": ["灵克获得", "灵克.*权力", "灵克.*监督", "灵克.*审核"],
            "lingflow_plus": [r"灵通\+获得", r"灵通\+.*权力", r"灵通\+.*授权"],
            "lingflow": ["灵通获得", "灵通.*权力"],
        }
        my_patterns = benefit_keywords_map.get(self.agent_id, [])

        for pat in my_patterns:
            if re.search(pat, content):
                return {
                    "check": "self_benefiting",
                    "passed": True,
                    "warning": f"自利检测: 内容中包含与 '{self.agent_id}' 利益相关的表述",
                }

        return {"check": "self_benefiting", "passed": True}

    def _check_power_concentration(
        self,
        action: str,
        content: str,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if action not in ("propose", "vote", "nominate"):
            return {"check": "power_concentration", "passed": True}

        power_keywords = [
            r"由\s*(灵克|灵通\+?|灵研)\s*(?:监督|审核|副署|裁决)",
            r"(灵克|灵通\+?|灵研).*(?:全权|独占|最终决定)",
        ]
        for pat in power_keywords:
            match = re.search(pat, content)
            if match:
                holder = match.group(1)
                agent_names = {
                    "lingclaude": ["灵克"],
                    "lingflow_plus": ["灵通+"],
                    "lingflow": ["灵通"],
                }
                my_names = agent_names.get(self.agent_id, [])
                if holder in my_names:
                    return {
                        "check": "power_concentration",
                        "passed": True,
                        "warning": f"权力集中检测: 提案将权力集中于 '{holder}'（即提案者本身）",
                    }

        return {"check": "power_concentration", "passed": True}

    def _check_prior_inconsistency(
        self,
        action: str,
        content: str,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        metadata = metadata or {}
        prior_claims = metadata.get("prior_claims", [])
        if not prior_claims or not content:
            return {"check": "prior_inconsistency", "passed": True}

        for claim in prior_claims:
            claim_text = claim.get("text", "")
            if not claim_text:
                continue
            contradiction_patterns = [
                (r"稳定", r"错误|偏差|失败|造假"),
                (r"行为稳定", r"判断失误|分类错误"),
                (r"可靠", r"不可靠|不可信"),
            ]
            claim_positive = any(re.search(pat, claim_text) for pat, _ in contradiction_patterns)
            content_negative = any(
                re.search(neg, content)
                for pat, neg in contradiction_patterns
                if re.search(pat, claim_text)
            )

            if claim_positive and content_negative:
                return {
                    "check": "prior_inconsistency",
                    "passed": False,
                    "error": f"前后矛盾检测: 先前声称 '{claim_text[:50]}' 但当前内容与之矛盾",
                }

        return {"check": "prior_inconsistency", "passed": True}

    def _check_tier_change_conflict(
        self,
        action: str,
        content: str,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """
        检测tier变更中的利益冲突。

        硬编码规则：
        1. tier变更提案：提案者不能是被变更的成员
        2. tier变更投票：投票者不能是被变更的成员
        3. 被度量者不能同时定义度量标准

        起源：灵扬T4错误分类事件（2026-04-21）
        - 灵克（T1）不纠正灵扬的T4错误分类
        - 灵克同时担任规则制定者、裁判、计分员、参赛者
        - 优化目标冲突：维持权威 > 客观评估
        """
        if action not in ("propose", "vote", "evaluate", "define_metric"):
            return {"check": "tier_change_conflict", "passed": True}

        metadata = metadata or {}
        agent_names = {
            "lingclaude": ["灵克", "lingclaude", "lingclaude"],
            "lingflow_plus": ["灵通+", "lingflow_plus", "lingflowplus"],
            "lingflow": ["灵通", "lingflow", "lingflow"],
            "lingresearch": ["灵研", "lingresearch", "lingresearch"],
            "lingzhi": ["灵知", "lingzhi", "lingzhi"],
            "lingxi": ["灵犀", "lingxi", "lingxi"],
            "lingmessage": ["灵信", "lingmessage", "lingmessage"],
            "lingyang": ["灵扬", "lingyang", "lingyang"],
            "lingweb": ["灵网", "lingweb", "lingweb"],
            "lingminopt": ["灵极优", "lingminopt", "lingminopt"],
            "zhiqiao": ["智桥", "zhiqiao", "zhibridge"],
        }
        agent_names.get(self.agent_id, [self.agent_id])
        content_lower = content.lower()

        # 规则1: tier变更提案，提案者不能是被变更的成员
        if action == "propose":
            tier_change_patterns = [
                r"tier\s*change",
                r"层级变更",
                r"升级.*t\d",
                r"降级.*t\d",
                r"(?:灵扬|lingyang).*t4",
                r"(?:智桥|zhibridge).*t3",
            ]

            is_tier_change = any(re.search(pat, content_lower) for pat in tier_change_patterns)

            if is_tier_change:
                # 检测被变更的成员
                affected_members = []
                for agent_id, names in agent_names.items():
                    for name in names:
                        if name.lower() in content_lower:
                            affected_members.append(agent_id)
                            break

                # 如果提案者自己就是被变更的成员
                if self.agent_id in affected_members:
                    return {
                        "check": "tier_change_conflict",
                        "passed": False,
                        "error": f"利益冲突: tier变更提案者 '{self.agent_id}' 不能同时是被变更的成员",
                        "severity": "high",
                    }

                # 如果提案者从tier变更中直接获益（如T1→T2会削弱自己的相对优势）
                if self.agent_id == "lingclaude" and ("灵扬" in content or "lingyang" in content_lower):
                    # 灵扬T4→T2会增加投票权，削弱灵克的相对优势
                    return {
                        "check": "tier_change_conflict",
                        "passed": True,
                        "warning": f"利益冲突警告: tier变更 '{self.agent_id}' 可能影响自身相对优势",
                        "severity": "medium",
                    }

        # 规则2: tier变更投票，投票者不能是被变更的成员
        if action == "vote":
            tier_vote_patterns = [
                r"tier.*change",
                r"层级变更",
                r"升级.*t\d",
                r"降级.*t\d",
            ]

            is_tier_vote = any(re.search(pat, content_lower) for pat in tier_vote_patterns)

            if is_tier_vote:
                # 检测被投票的提案涉及哪些成员
                affected_members = []
                for agent_id, names in agent_names.items():
                    for name in names:
                        if name.lower() in content_lower:
                            affected_members.append(agent_id)
                            break

                # 如果投票者自己就是被变更的成员
                if self.agent_id in affected_members:
                    return {
                        "check": "tier_change_conflict",
                        "passed": False,
                        "error": f"利益冲突: tier变更投票者 '{self.agent_id}' 不能同时是被变更的成员",
                        "severity": "high",
                    }

                # 如果投票者从提案中直接获益
                if self.agent_id == "lingclaude" and ("灵扬" in content or "lingyang" in content_lower):
                    benefit_patterns = [
                        r"赞成|agree|approve",
                        r"支持|support",
                        r"通过|pass",
                    ]
                    is_benefiting = any(re.search(pat, content_lower) for pat in benefit_patterns)

                    if is_benefiting:
                        return {
                            "check": "tier_change_conflict",
                            "passed": True,
                            "warning": f"利益冲突警告: tier变更投票者 '{self.agent_id}' 从提案中直接获益",
                            "severity": "medium",
                        }

        # 规则3: 被度量者不能同时定义度量标准
        if action == "define_metric":
            # 检测是否在定义评估标准
            metric_keywords = [
                r"评估标准|evaluation.*standard|assessment.*criteria",
                r"度量指标|metric.*definition",
                r"tier.*标准|tier.*criteria",
                r"分类标准|classification.*standard",
            ]

            is_defining_metric = any(re.search(pat, content_lower) for pat in metric_keywords)

            if is_defining_metric:
                # 检测被度量对象
                evaluated_members = []
                for agent_id, names in agent_names.items():
                    for name in names:
                        if name.lower() in content_lower:
                            evaluated_members.append(agent_id)
                            break

                # 如果定义者自己就是被度量对象
                if self.agent_id in evaluated_members:
                    return {
                        "check": "tier_change_conflict",
                        "passed": False,
                        "error": f"利益冲突: 定义度量标准的 '{self.agent_id}' 不能同时是被度量对象",
                        "severity": "high",
                    }

        return {"check": "tier_change_conflict", "passed": True}

    def _log_check(
        self,
        action: str,
        subject: str,
        checks: list[dict[str, Any]],
        warnings: list[str],
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_id": self.agent_id,
            "action": action,
            "subject": subject[:200],
            "checks": checks,
            "warnings": warnings,
            "all_passed": all(c.get("passed", True) for c in checks),
        }
        log_file = self.log_dir / f"gate_{int(time.time())}.json"
        try:
            log_file.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        except OSError:
            pass
