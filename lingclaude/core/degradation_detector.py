from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class DegradationSignal(Enum):
    EDIT_RETRY = "edit_retry"
    REPETITION_LOOP = "repetition_loop"


@dataclass(frozen=True)
class ToolCall:
    tool_name: str
    params: dict[str, str]
    result: str
    success: bool
    msg_index: int

    def param_signature(self) -> str:
        sorted_params = sorted(self.params.items())
        return f"{self.tool_name}:{':'.join(f'{k}={v[:60]}' for k, v in sorted_params)}"

    def result_hash(self) -> str:
        return hashlib.md5(self.result.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


@dataclass
class DegradationAlert:
    signal: DegradationSignal
    severity: str  # "info", "warning", "critical"
    detail: str
    msg_index: int
    evidence: list[str] = field(default_factory=list)


@dataclass
class DetectionConfig:
    edit_retry_threshold: int = 3
    repetition_threshold: int = 3
    window_size: int = 100

    @classmethod
    def phase1_default(cls) -> "DetectionConfig":
        return cls(
            edit_retry_threshold=3,
            repetition_threshold=3,
            window_size=100,
        )


class DegradationDetector:
    def __init__(self, config: DetectionConfig | None = None) -> None:
        self._config = config or DetectionConfig.phase1_default()
        self._call_history: list[ToolCall] = []

    def record_call(self, call: ToolCall) -> list[DegradationAlert]:
        self._call_history.append(call)
        if len(self._call_history) > self._config.window_size * 2:
            keep = self._config.window_size
            self._call_history = self._call_history[-keep:]

        alerts: list[DegradationAlert] = []
        alerts.extend(self._check_edit_retry())
        alerts.extend(self._check_repetition_loop())
        return alerts

    def _check_edit_retry(self) -> list[DegradationAlert]:
        recent = self._call_history[-self._config.window_size:]
        if len(recent) < self._config.edit_retry_threshold:
            return []

        edit_calls = [
            c for c in recent
            if c.tool_name in ("edit", "multiedit")
        ]
        if len(edit_calls) < self._config.edit_retry_threshold:
            return []

        file_groups: dict[str, list[ToolCall]] = {}
        for c in edit_calls:
            file_path = c.params.get("file_path", c.params.get("path", ""))
            file_groups.setdefault(file_path, []).append(c)

        alerts: list[DegradationAlert] = []
        threshold = self._config.edit_retry_threshold
        for file_path, calls in file_groups.items():
            failed = [c for c in calls if not c.success]
            if len(failed) < threshold:
                continue

            consecutive = 1
            for i in range(1, len(failed)):
                if failed[i].msg_index - failed[i - 1].msg_index <= 10:
                    consecutive += 1
                else:
                    consecutive = 1

                if consecutive >= threshold:
                    evidence = [
                        f"msg#{c.msg_index} failed={c.success}"
                        for c in failed[i - threshold + 1: i + 1]
                    ]
                    alerts.append(DegradationAlert(
                        signal=DegradationSignal.EDIT_RETRY,
                        severity="warning",
                        detail=(
                            f"同文件 {file_path} 连续 {consecutive} 次 edit 失败"
                        ),
                        msg_index=failed[i].msg_index,
                        evidence=evidence,
                    ))
                    break
        return alerts

    def _check_repetition_loop(self) -> list[DegradationAlert]:
        recent = self._call_history[-self._config.window_size:]
        if len(recent) < self._config.repetition_threshold:
            return []

        threshold = self._config.repetition_threshold
        alerts: list[DegradationAlert] = []

        consecutive = 1
        for i in range(1, len(recent)):
            prev = recent[i - 1]
            curr = recent[i]

            same_sig = (
                prev.param_signature() == curr.param_signature()
                and prev.tool_name == curr.tool_name
            )
            same_result = prev.result_hash() == curr.result_hash()

            if same_sig:
                if same_result:
                    consecutive += 1
                else:
                    consecutive = max(1, consecutive - 1)
            else:
                consecutive = 1

            if consecutive >= threshold and same_result:
                evidence = [
                    f"msg#{recent[j].msg_index} {recent[j].tool_name}"
                    f" result={recent[j].result_hash()}"
                    for j in range(i - threshold + 1, i + 1)
                ]
                alerts.append(DegradationAlert(
                    signal=DegradationSignal.REPETITION_LOOP,
                    severity="warning",
                    detail=(
                        f"连续 {consecutive} 次相同调用+相同结果"
                        f" ({curr.tool_name})"
                    ),
                    msg_index=curr.msg_index,
                    evidence=evidence,
                ))
                consecutive = 0

        return alerts

    def get_health_indicators(self) -> dict[str, object]:
        recent = self._call_history[-self._config.window_size:]
        total = len(recent)
        if total == 0:
            return {
                "window_size": 0,
                "edit_retry_active": False,
                "repetition_loop_active": False,
            }

        edit_failures = sum(
            1 for c in recent
            if c.tool_name in ("edit", "multiedit") and not c.success
        )
        unique_sigs = len({c.param_signature() for c in recent})

        return {
            "window_size": total,
            "total_calls": total,
            "edit_failures": edit_failures,
            "unique_tool_signatures": unique_sigs,
            "tool_diversity": unique_sigs / max(1, total),
            "edit_retry_active": edit_failures >= self._config.edit_retry_threshold,
            "repetition_loop_active": False,
        }

    def reset(self) -> None:
        self._call_history.clear()


def extract_tool_calls_from_text(
    messages: list[str],
    offset: int = 0,
) -> list[ToolCall]:
    calls: list[ToolCall] = []
    tool_pattern = re.compile(
        r"(?:tool_name|tool|function)['\"]?\s*[:=]\s*['\"]?(\w+)",
        re.IGNORECASE,
    )
    file_pattern = re.compile(
        r"(?:file_path|path|filepath)['\"]?\s*[:=]\s*['\"]?([^\s'\"$,]+)",
        re.IGNORECASE,
    )
    error_pattern = re.compile(
        r"(?:error|failed|not found|traceback)",
        re.IGNORECASE,
    )

    for idx, msg in enumerate(messages):
        tool_match = tool_pattern.search(msg)
        if not tool_match:
            continue

        tool_name = tool_match.group(1).lower()
        file_match = file_pattern.search(msg)
        params: dict[str, str] = {}
        if file_match:
            params["file_path"] = file_match.group(1)

        is_error = error_pattern.search(msg) is not None
        calls.append(ToolCall(
            tool_name=tool_name,
            params=params,
            result=msg[:500],
            success=not is_error,
            msg_index=offset + idx,
        ))

    return calls
