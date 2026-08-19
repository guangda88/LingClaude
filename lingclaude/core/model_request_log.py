"""LINGKERNEL_v1 task #1 (激进拆包 D1) - deriveMessages 接口预留
+ LINGKERNEL_v1 D8 - MV-1b 重放式 fold + 结构化 violation

dsh 对位: `core/session` deriveMessages() -- "model-visible means logged" invariant。
灵研 INVARIANT_MODEL_VISIBLE_v0.1.md §6 要求灵克 D+3 前预留此接口。

契约 (MV-1 L-a 层):
    derive_model_messages(log) == 最后一次发往模型的 messages

query_engine 后续把 _build_messages 产物 append 到 log,
derive_model_messages 从 log 重放, 两者必须相等。

D8 演进 (灵研 spec-review R1/R3 处置):
- MV-1a: append-integrity (已上线, 快照校验)
- MV-1b: provenance-integrity (重放式 fold, 从增量事件重建, 不依赖快照)
- 结构化 violation (seq/reason/timestamp dataclass), 供灵信 L-b 按 seq 归因
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ModelRequestEvent:
    """一次发往模型的请求事件 (log 中的最小记录单元)。

    dsh 对位: session log 的 assistant/message + user/message 事件。
    messages_snapshot 即 "发往模型的完整 messages 列表" --
    按灵研 spec L-a, 它必须能从 log 完整重建。

    D8 MV-1b: 增量事件携带 deltas (每事件相对前序的追加量), 支持重放式 fold。
    """

    seq: int
    prompt: str
    messages_snapshot: list[dict[str, Any]] = field(default_factory=list)
    tool_names: tuple[str, ...] = ()
    timestamp: str = ""
    delta: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Mv1Violation:
    """MV-1 违规的结构化记录 (灵研 spec-review R3 / 灵信 L-b 归因需求)。

    seq:     违规请求的日志序号 (spec §3 归因规则第 1 类: 事件缺失/序号断裂)
    reason:  违规原因描述
    timestamp: 违规发生时刻 (UTC ISO)
    """

    seq: int
    reason: str
    timestamp: str

    def to_tuple_str(self) -> str:
        """兼容旧 tuple[str] 消费方 (灵信 AgentMV1Consumer 迁移前的过渡)。"""
        return f"[seq={self.seq}] {self.reason} @ {self.timestamp}"


class ModelRequestLog:
    """append-only 模型请求日志 (deriveMessages 的存储侧)。

    dsh 原则: "Model-visible means logged" --
    任何进入模型请求的内容必须先落此 log, 再发模型。
    """

    def __init__(self) -> None:
        self._events: list[ModelRequestEvent] = []
        self._next_seq = 1

    def append(
        self,
        prompt: str,
        messages: list[dict[str, Any]],
        tool_names: tuple[str, ...] = (),
        timestamp: str = "",
    ) -> ModelRequestEvent:
        ev = ModelRequestEvent(
            seq=self._next_seq,
            prompt=prompt,
            messages_snapshot=copy.deepcopy(messages),
            tool_names=tuple(tool_names),
            timestamp=timestamp,
        )
        # D8 MV-1b: 记录增量 (相对前一事件快照的追加量), 支持重放式 fold
        if self._events:
            prev = self._events[-1].messages_snapshot
            # 找出 messages 中 prev 之后的部分 (常见: 追加 user/assistant/tool 消息)
            delta_start = 0
            for i in range(len(messages)):
                if i >= len(prev) or messages[i] != prev[i]:
                    delta_start = i
                    break
                delta_start = i + 1
            ev.delta = copy.deepcopy(messages[delta_start:])
        else:
            ev.delta = copy.deepcopy(messages)
        self._events.append(ev)
        self._next_seq += 1
        return ev

    @property
    def events(self) -> tuple[ModelRequestEvent, ...]:
        return tuple(self._events)

    def prefix(self, upto_seq: int) -> tuple[ModelRequestEvent, ...]:
        """seq <= upto_seq 的事件前缀 (dsh log.prefix(req.seq) 对位)。"""
        return tuple(e for e in self._events if e.seq <= upto_seq)

    def last_seq(self) -> int:
        return self._next_seq - 1


def derive_model_messages(log: ModelRequestLog, upto_seq: int | None = None) -> list[dict[str, Any]]:
    """从 log 重建"最后一次(截至 upto_seq)发往模型的 messages"。

    MV-1 L-a 不变量:
        derive_model_messages(log, req.seq) == req.messages

    当前实现: 取最后一条事件的 messages_snapshot。
    演进方向 (灵研 spec): 从增量事件重放合成, 而非存快照。
    接口签名先行冻结, 内部实现可演进。
    """
    events = log.events if upto_seq is None else log.prefix(upto_seq)
    if not events:
        return []
    return copy.deepcopy(events[-1].messages_snapshot)


def check_model_visible_invariant(
    log: ModelRequestLog,
    seq: int,
    actual_messages: list[dict[str, Any]],
) -> tuple[bool, str]:
    """MV-1 断言: 发往模型的 actual_messages 必须能从 log.prefix(seq) 重建。

    返回 (passed, reason)。
    """
    derived = derive_model_messages(log, seq)
    if derived == actual_messages:
        return True, "ok"
    return False, (
        f"MV-1 violated at seq={seq}: "
        f"derived {len(derived)} msgs != actual {len(actual_messages)} msgs"
    )


def derive_model_messages_fold(
    log: ModelRequestLog,
    upto_seq: int | None = None,
    fold_fn: Callable[[list[dict[str, Any]], list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """D8 MV-1b: 重放式 fold 重建 messages (provenance-integrity)。

    从增量事件(delta)重放合成, 不依赖快照, 支持:
    - 前向 fold: 依次 apply 每事件 delta 到状态
    - 默认 fold = append (逐事件 delta 追加)

    若某事件缺 delta (旧格式/迁移), 回退用其 messages_snapshot 覆盖状态。
    这是对"快照先行, 重放后继"路线判断的实现 (灵研 spec-review R1 确认)。
    """
    events = log.events if upto_seq is None else log.prefix(upto_seq)
    fold = fold_fn or (lambda state, delta: state + delta)
    state: list[dict[str, Any]] = []
    for e in events:
        if e.delta:
            state = fold(state, e.delta)
        else:
            # 无 delta (旧格式) → 回退快照
            state = copy.deepcopy(e.messages_snapshot)
    return copy.deepcopy(state)


def check_provenance_integrity(
    log: ModelRequestLog,
    seq: int,
    actual_messages: list[dict[str, Any]],
) -> tuple[bool, str]:
    """D8 MV-1b: provenance-integrity 断言。

    derive(prefix(seq)) 通过重放式 fold 重建, 再与 actual 比较。
    比 MV-1a 快照式更强: 堵住"日志后篡改" + 校验增量链完整性。
    """
    derived = derive_model_messages_fold(log, seq)
    if derived == actual_messages:
        return True, "ok"
    return False, (
        f"MV-1b violated at seq={seq}: "
        f"fold-derived {len(derived)} msgs != actual {len(actual_messages)} msgs"
    )