# lingclaude/core/repair_card.py
"""修复卡（方案C v4 P1#3）：写失败/验证失败的结构化追踪，fail-closed 结账。

动机（本会话实证）：批量落盘中 edit 参数损坏、epoch 守卫吞切换等失败，
如果没有结构化记录，靠会话记忆追踪「哪一步坏了、验证过没有」必然漂移。

三态状态机：
    OPEN → REPAIRING → RESOLVED（必须携带验证证据）
                    ↘ FAILED（重试用尽，升级人工）

fail-closed 铁律：
- resolve() 必须携带 verification（验证命令 + 通过证据）——无证据抛
  RepairEvidenceError，**绝不允许「应该好了」式结账**；
- 有 OPEN/REPAIRING 卡的目标文件，board.blocks(file) 返回 True——
  批量操作器在写同一文件前必须先查卡（同文件带病继续写 = 累积损坏）；
- FAILED 卡不清除（保留事故现场），只能注释后重新开卡。
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class RepairState(str, Enum):
    OPEN = "open"            # 问题已记录，未开始修复
    REPAIRING = "repairing"  # 修复进行中
    RESOLVED = "resolved"    # 已修复且验证通过（证据在案）
    FAILED = "failed"        # 重试用尽，升级人工（终态，保留现场）


class RepairEvidenceError(RuntimeError):
    """试图无证据 resolve——fail-closed 拒绝。"""


class RepairTransitionError(RuntimeError):
    """非法状态迁移（如 RESOLVED → REPAIRING）。"""


@dataclass
class RepairCard:
    """单张修复卡：目标、症状、修复过程、验证证据。"""

    target: str                       # 目标文件路径或逻辑标识
    symptom: str                      # 失败症状（错误消息/断言差异）
    card_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    state: RepairState = RepairState.OPEN
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    attempts: int = 0                 # 修复尝试次数
    history: list[str] = field(default_factory=list)
    verification: Optional[dict[str, Any]] = None  # {"command":..., "evidence":...}

    def _touch(self, note: str) -> None:
        self.updated_at = datetime.now()
        self.history.append(f"[{self.updated_at.strftime('%H:%M:%S')}] {note}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "card_id": self.card_id,
            "target": self.target,
            "symptom": self.symptom[:200],
            "state": self.state.value,
            "attempts": self.attempts,
            "verification": self.verification,
            "history": self.history[-10:],
            "created_at": self.created_at.isoformat(),
        }


class RepairBoard:
    """进程内修复卡看板（线程安全）。"""

    def __init__(self, max_attempts: int = 3) -> None:
        self._cards: dict[str, RepairCard] = {}
        self._lock = threading.RLock()
        self._max_attempts = max_attempts

    # ---- 开卡 / 状态迁移 ----

    def open_card(self, target: str, symptom: str) -> RepairCard:
        card = RepairCard(target=str(target), symptom=str(symptom))
        with self._lock:
            self._cards[card.card_id] = card
        logger.warning("repair_card: OPEN %s target=%s symptom=%.120s",
                       card.card_id, card.target, card.symptom)
        return card

    def start_repair(self, card_id: str) -> RepairCard:
        with self._lock:
            card = self._get(card_id)
            if card.state is RepairState.RESOLVED:
                raise RepairTransitionError(f"{card_id}: RESOLVED 不可再修复")
            if card.state is RepairState.FAILED:
                raise RepairTransitionError(f"{card_id}: FAILED 是终态，开新卡")
            if card.state is RepairState.OPEN:
                card.state = RepairState.REPAIRING
            card.attempts += 1
            if card.attempts > self._max_attempts:
                return self._fail_locked(card, f"重试用尽（>{self._max_attempts}）")
            card._touch(f"start_repair attempt#{card.attempts}")
            return card

    def resolve(self, card_id: str, command: str, evidence: Any) -> RepairCard:
        """结账：必须携带验证证据（命令 + 通过输出），否则 fail-closed 抛错。"""
        if not (command or "").strip() or evidence is None:
            raise RepairEvidenceError(
                f"{card_id}: 无验证证据不得 resolve（fail-closed）")
        with self._lock:
            card = self._get(card_id)
            if card.state is RepairState.FAILED:
                raise RepairTransitionError(f"{card_id}: FAILED 是终态")
            card.state = RepairState.RESOLVED
            card.verification = {"command": command, "evidence": str(evidence)[:500]}
            card._touch(f"resolved via: {command[:80]}")
        logger.info("repair_card: RESOLVED %s target=%s", card.card_id, card.target)
        return card

    def fail(self, card_id: str, reason: str) -> RepairCard:
        with self._lock:
            card = self._get(card_id)
            return self._fail_locked(card, reason)

    def _fail_locked(self, card: RepairCard, reason: str) -> RepairCard:
        card.state = RepairState.FAILED
        card._touch(f"FAILED: {reason[:120]}")
        logger.error("repair_card: FAILED %s target=%s reason=%.120s",
                     card.card_id, card.target, reason)
        return card

    # ---- fail-closed 查询 ----

    def blocks(self, target: str) -> bool:
        """该目标是否存在未结账卡片（OPEN/REPAIRING）——批量写之前必须查。"""
        target = str(target)
        with self._lock:
            return any(
                c.target == target and c.state in (RepairState.OPEN, RepairState.REPAIRING)
                for c in self._cards.values()
            )

    def open_cards(self, target: Optional[str] = None) -> list[RepairCard]:
        with self._lock:
            cards = list(self._cards.values())
        if target is not None:
            cards = [c for c in cards if c.target == str(target)]
        return [c for c in cards if c.state in (RepairState.OPEN, RepairState.REPAIRING)]

    def get(self, card_id: str) -> RepairCard:
        with self._lock:
            return self._get(card_id)

    def _get(self, card_id: str) -> RepairCard:
        card = self._cards.get(card_id)
        if card is None:
            raise KeyError(f"repair_card: {card_id!r} 不存在")
        return card

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [c.to_dict() for c in self._cards.values()]


# 模块级单例（进程内唯一看板；测试用 _reset）
_default_board: Optional[RepairBoard] = None


def get_repair_board() -> RepairBoard:
    global _default_board
    if _default_board is None:
        _default_board = RepairBoard()
    return _default_board


def _reset_repair_board() -> None:
    """仅测试用。"""
    global _default_board
    _default_board = None
