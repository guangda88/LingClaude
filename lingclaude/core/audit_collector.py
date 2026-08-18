"""LINGKERNEL_v1 task #1 (激进拆包 D1) - AuditCollector 模块

dsh 对位: 横切 telemetry/audit -- L5 audit + degradation + behavior track。
从 query_engine.py 抽取: _apply_l5_audit / _check_degradation /
_track_behavior / _check_behavior / _check_intent / get_degradation_alerts。

设计:
- AuditCollector 不知道 QueryEngine
- 输入 prompt/output/used_tools, 输出审计决策
- degradation 检测独立 (可注入 detector)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DegradationAlert:
    kind: str
    message: str


@dataclass
class AuditResult:
    """一次 audit 的输出。output 可被替换 (L5 rewrite)。"""

    output: str
    rewritten: bool = False
    alerts: list[DegradationAlert] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class AuditCollector:
    """L5 审计 + 行为追踪 + 退化检测 (query_engine 审计层抽取)。"""

    def __init__(
        self,
        *,
        l5_orchestrator: Any | None = None,
        degradation_detector: Any | None = None,
    ) -> None:
        self._l5 = l5_orchestrator
        self._degradation = degradation_detector
        self._audit_history: list[AuditResult] = []

    # ----- L5 -----

    def apply_l5(self, prompt: str, output: str) -> AuditResult:
        """L5 audit (可 rewrite output)。orchestrator 缺席时原样返回。"""
        result = AuditResult(output=output)
        if self._l5 is None:
            return result
        try:
            rewritten = self._l5.audit(prompt, output)
            if rewritten is not None and rewritten != output:
                result.output = rewritten
                result.rewritten = True
        except Exception as e:
            logger.warning("L5 audit failed: %s", e)
            result.notes.append(f"l5-error: {e}")
        self._audit_history.append(result)
        return result

    # ----- degradation -----

    def check_degradation(self, prompt: str, output: str) -> list[DegradationAlert]:
        if self._degradation is None:
            return []
        try:
            raw = self._degradation.check(prompt, output)
            return [
                DegradationAlert(kind=a.get("kind", "unknown"), message=a.get("message", ""))
                for a in raw
            ]
        except Exception as e:
            logger.warning("degradation check failed: %s", e)
            return []

    def degradation_health(self) -> dict[str, Any]:
        if self._degradation is None:
            return {"status": "not-configured"}
        try:
            return self._degradation.health()
        except Exception:
            return {"status": "error"}

    # ----- behavior -----

    def check_behavior_gate(self, prompt: str, rules: list[str] | None = None) -> str | None:
        """行为规则拦截。返回 block 理由或 None (放行)。

        rules: 形如 ["block:rm -rf", "warn:sudo"] 的规则串。
        """
        for rule in rules or []:
            if rule.startswith("block:") and rule[6:] in prompt:
                return f"blocked by rule: {rule[6:]}"
        return None

    # ----- history -----

    @property
    def history(self) -> list[AuditResult]:
        return list(self._audit_history)

    def clear_history(self) -> None:
        self._audit_history.clear()