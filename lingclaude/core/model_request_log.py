"""LINGKERNEL_v1 task #1 (激进拆包 D1) - deriveMessages 接口预留

dsh 对位: `core/session` deriveMessages() -- "model-visible means logged" invariant。
灵研 INVARIANT_MODEL_VISIBLE_v0.1.md §6 要求灵克 D+3 前预留此接口。

契约 (MV-1 L-a 层):
    derive_model_messages(log) == 最后一次发往模型的 messages

query_engine 后续把 _build_messages 产物 append 到 log,
derive_model_messages 从 log 重放, 两者必须相等。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelRequestEvent:
    """一次发往模型的请求事件 (log 中的最小记录单元)。

    dsh 对位: session log 的 assistant/message + user/message 事件。
    messages_snapshot 即 "发往模型的完整 messages 列表" --
    按灵研 spec L-a, 它必须能从 log 完整重建。
    """

    seq: int
    prompt: str
    messages_snapshot: list[dict[str, Any]] = field(default_factory=list)
    tool_names: tuple[str, ...] = ()
    timestamp: str = ""


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