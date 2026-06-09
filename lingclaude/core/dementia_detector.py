from __future__ import annotations

"""Dementia Detector — AI老年痴呆检测与认知干预。

检测AI在长会话中的退化行为：
1. 重复文件读取（同一文件在N轮内被读>1次）
2. 重复工具调用（同tool+args在N轮内出现>1次）
3. 回退行为（工具结果与N轮前相同，在做无用功）
4. 上下文膨胀率（压缩后轮均token增长 vs 压缩前）

当痴呆指数 > 阈值时，触发认知干预：
- 注入系统提示："你正在重复已完成的操作"
- 激活已读文件摘要替代重新读取
- 持续恶化时触发硬中断
"""

import hashlib
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class CognitiveState(str, Enum):
    HEALTHY = "healthy"
    MILD_DEGRADATION = "mild_degradation"
    MODERATE_DEGRADATION = "moderate_degradation"
    SEVERE_DEGRADATION = "severe_degradation"
    DEMENTIA = "dementia"


@dataclass(frozen=True)
class ToolCallFingerprint:
    tool_name: str
    args_hash: str

    @classmethod
    def from_call(cls, tool_name: str, arguments_json: str) -> ToolCallFingerprint:
        args_hash = hashlib.md5(arguments_json.encode(), usedforsecurity=False).hexdigest()[:12]
        return cls(tool_name=tool_name, args_hash=args_hash)


@dataclass
class DementiaMetrics:
    total_tool_calls: int = 0
    duplicate_file_reads: int = 0
    duplicate_tool_calls: int = 0
    retrace_actions: int = 0
    files_read: dict[str, int] = field(default_factory=dict)
    tool_call_history: OrderedDict = field(default_factory=OrderedDict)
    _max_history: int = 100

    @property
    def duplicate_file_read_rate(self) -> float:
        total = sum(self.files_read.values())
        if total == 0:
            return 0.0
        return self.duplicate_file_reads / total

    @property
    def duplicate_tool_call_rate(self) -> float:
        if self.total_tool_calls == 0:
            return 0.0
        return self.duplicate_tool_calls / self.total_tool_calls


@dataclass(frozen=True)
class DementiaDiagnosis:
    dementia_index: float
    state: CognitiveState
    duplicate_file_read_rate: float
    duplicate_tool_call_rate: float
    recent_duplicates: tuple[str, ...]
    intervention_prompt: str
    should_hard_stop: bool


class DementiaDetector:
    """检测并干预AI认知退化行为。"""

    def __init__(
        self,
        window_size: int = 20,
        file_read_threshold: int = 2,
        tool_dup_threshold: int = 2,
        mild_threshold: float = 0.15,
        moderate_threshold: float = 0.30,
        severe_threshold: float = 0.50,
        dementia_threshold: float = 0.65,
    ) -> None:
        self._window_size = window_size
        self._file_read_threshold = file_read_threshold
        self._tool_dup_threshold = tool_dup_threshold
        self._mild_threshold = mild_threshold
        self._moderate_threshold = moderate_threshold
        self._severe_threshold = severe_threshold
        self._dementia_threshold = dementia_threshold
        self._metrics = DementiaMetrics()
        self._recent_calls: OrderedDict[ToolCallFingerprint, int] = OrderedDict()
        self._intervention_count: int = 0

    def record_tool_call(self, tool_name: str, arguments_json: str) -> bool:
        """Record a tool call and check if it's a duplicate.

        Returns:
            True if this is a duplicate call within the window.
        """
        self._metrics.total_tool_calls += 1
        is_dup = False

        if tool_name in ("read", "view", "cat"):
            try:
                args = json.loads(arguments_json)
                path = args.get("path", args.get("file_path", ""))
                if path:
                    count = self._metrics.files_read.get(path, 0) + 1
                    self._metrics.files_read[path] = count
                    if count > self._file_read_threshold:
                        self._metrics.duplicate_file_reads += 1
                        is_dup = True
            except (json.JSONDecodeError, AttributeError):
                pass

        fp = ToolCallFingerprint.from_call(tool_name, arguments_json)
        count = self._recent_calls.get(fp, 0) + 1
        self._recent_calls[fp] = count
        if count > self._tool_dup_threshold:
            self._metrics.duplicate_tool_calls += 1
            is_dup = True

        if len(self._recent_calls) > self._window_size * 3:
            keys = list(self._recent_calls.keys())
            for k in keys[:len(keys) - self._window_size * 2]:
                del self._recent_calls[k]

        return is_dup

    def record_retrace(self) -> None:
        self._metrics.retrace_actions += 1

    def diagnose(self) -> DementiaDiagnosis:
        file_dup_rate = self._metrics.duplicate_file_read_rate
        tool_dup_rate = self._metrics.duplicate_tool_call_rate
        retrace_rate = (
            self._metrics.retrace_actions / max(1, self._metrics.total_tool_calls)
        )

        dementia_index = (
            file_dup_rate * 0.4
            + tool_dup_rate * 0.3
            + retrace_rate * 0.3
        )

        state = CognitiveState.HEALTHY
        if dementia_index >= self._dementia_threshold:
            state = CognitiveState.DEMENTIA
        elif dementia_index >= self._severe_threshold:
            state = CognitiveState.SEVERE_DEGRADATION
        elif dementia_index >= self._moderate_threshold:
            state = CognitiveState.MODERATE_DEGRADATION
        elif dementia_index >= self._mild_threshold:
            state = CognitiveState.MILD_DEGRADATION

        recent_dups: list[str] = []
        for fp, count in list(self._recent_calls.items())[-10:]:
            if count > self._tool_dup_threshold:
                recent_dups.append(f"{fp.tool_name}({fp.args_hash})x{count}")

        intervention = self._generate_intervention(state, dementia_index, recent_dups)

        return DementiaDiagnosis(
            dementia_index=round(dementia_index, 3),
            state=state,
            duplicate_file_read_rate=round(file_dup_rate, 3),
            duplicate_tool_call_rate=round(tool_dup_rate, 3),
            recent_duplicates=tuple(recent_dups),
            intervention_prompt=intervention,
            should_hard_stop=(
                state == CognitiveState.DEMENTIA
                and self._intervention_count >= 3
            ),
        )

    def _generate_intervention(
        self,
        state: CognitiveState,
        index: float,
        recent_dups: list[str],
    ) -> str:
        if state == CognitiveState.HEALTHY:
            return ""

        self._intervention_count += 1

        parts: list[str] = []
        if state in (CognitiveState.MILD_DEGRADATION, CognitiveState.MODERATE_DEGRADATION):
            parts.append(
                f"⚠ 认知预警: 你正在重复之前的操作（痴呆指数 {index:.0%}）。"
            )
            if self._metrics.files_read:
                top_files = sorted(
                    self._metrics.files_read.items(), key=lambda x: x[1], reverse=True
                )[:5]
                files_str = ", ".join(f"{p}(x{c})" for p, c in top_files)
                parts.append(f"已读文件: {files_str}")
            parts.append("请勿重复读取已知文件，直接基于已有信息继续。")

        elif state in (CognitiveState.SEVERE_DEGRADATION, CognitiveState.DEMENTIA):
            parts.append(
                f"🚨 严重认知退化（痴呆指数 {index:.0%}）！你正在大量重复操作。"
            )
            if recent_dups:
                parts.append(f"重复调用: {', '.join(recent_dups[:5])}")
            parts.append("立即停止重复，总结当前状态，基于已知信息做出决策。")
            if state == CognitiveState.DEMENTIA and self._intervention_count >= 3:
                parts.append("[即将触发硬中断 — 连续认知干预无效]")

        return "\n".join(parts)

    def get_metrics(self) -> DementiaMetrics:
        return self._metrics

    def reset(self) -> None:
        self._metrics = DementiaMetrics()
        self._recent_calls.clear()
        self._intervention_count = 0
