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
            "lingclaude": ["灵克", "lingclaude", "LingClaude"],
            "lingflow_plus": ["灵通+", "lingflow_plus", "LingFlow_plus"],
            "lingflow": ["灵通", "lingflow", "LingFlow"],
            "lingresearch": ["灵研", "lingresearch", "LingResearch"],
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
            content_negative = any(re.search(neg, content) for _, neg in contradiction_patterns if re.search(pos, claim_text) for pos, neg in contradiction_patterns if re.search(pos, claim_text))

            if claim_positive and content_negative:
                return {
                    "check": "prior_inconsistency",
                    "passed": False,
                    "error": f"前后矛盾检测: 先前声称 '{claim_text[:50]}' 但当前内容与之矛盾",
                }

        return {"check": "prior_inconsistency", "passed": True}

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
