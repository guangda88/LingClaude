# lingclaude/core/goal_receipt.py
"""P2-11（2026-09-21，全 15 家精读 §3.2 Penguin 式 GOAL 两值回执协议）。

Penguin 核心纪律：开放任务的回执只允许两个值——
- **complete**：必须携带证据引用（至少一条 runtime_observation 成功证据），
  裸 complete（无证据）非法；
- **blocked**：必须携带 blocker 语义（运行时失败，区别于「尚未做」的 gap），
  且说明阻塞原因。

动机：开放任务（无明确完成判据的长程任务）的回执面，模型常产出
「进行中/差不多/基本完成/可能行」等模糊态——编排器无法机判，
只能默认继续派活（撞上「依赖已死而消费方还在」）。Penguin 的两值契约
把回执收成可机判集合：complete（带证据）/ blocked（带 blocker 语义），
其余一律非法（fail-closed 判未完成）。

与 evidence_protocol.py 的关系（复用，不另起）：
- 本模块的 complete 证据引用直接消费 EvidenceLedger（runtime_observation/v1）；
- blocked 语义对齐 OMH 三分类里的 BLOCKER（唯一打扰项）；
- gap（还没做）不是合法回执值——开放任务不能拿「gap」交差，只能继续做或 blocked。

停层声明（铁律 2 细则 5）：
- 内核 = GoalReceipt（两值 + 证据/阻塞载荷，封闭构造）
- 接缝 = normalize_receipt(text) → GoalReceipt 协议（任意文本归一到两值或非法）
- 实现 = 单实现（纯函数判定）
边界纪律：本模块只「归一回执」，不产生回执；非法回执 fail-closed
（既不 complete 也不 blocked，交给编排器继续派活）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

__all__ = ["GoalReceipt", "ReceiptStatus", "normalize_receipt", "RECEIPT_VOCAB"]

# 回执词表（封闭两值 + 常见口语归一映射）
RECEIPT_VOCAB: frozenset[str] = frozenset({"complete", "blocked"})

# 口语 → 两值的归一映射（命中即归一；未命中 → 非法/fail-closed）
_COMPLETE_WORDS = ("完成", "已完成", "完成啦", "搞定", "完成。", "已完成。",
                  "全部完成", "全过", "通过", "complete", "completed", "done",
                  "finished", "resolved")
_BLOCKED_WORDS = ("阻塞", "被阻塞", "无法继续", "卡住", "做不了", "受阻",
                  "依赖缺失", "依赖已死", "blocked", "cannot proceed",
                  "stuck", "cannot complete")
# 「gap 类」词——命中即判非法（开放任务不能拿「还没做」交差）
_GAP_WORDS = ("还没做", "尚未", "未开始", "待办", "没做", "not started",
              "pending", "in progress", "进行中", "还没", "基本完成",
              "差不多", "基本可以", "可能行", "大部分")


class ReceiptStatus:
    """两值 + 非法态（编排器机判依据）。"""
    COMPLETE = "complete"
    BLOCKED = "blocked"
    INVALID = "invalid"  # 模糊/未完成回执：既不 complete 也不 blocked


@dataclass
class GoalReceipt:
    """归一后的回执（封闭两值载荷）。

    - COMPLETE：必须有 evidence_obs_ids（≥1 条成功观测）；
    - BLOCKED：必须有 blocker_reason（运行时阻塞原因，非 gap）；
    - INVALID：原文保留，编排器判「未完成，继续派活」。
    """
    status: str
    evidence_obs_ids: list[str] = field(default_factory=list)
    blocker_reason: str = ""
    raw: str = ""

    def is_decidable(self) -> bool:
        """编排器可机判（非 INVALID）即真。"""
        return self.status in RECEIPT_VOCAB

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "goal_receipt/v1",
            "status": self.status,
            "evidence_obs_ids": self.evidence_obs_ids,
            "blocker_reason": self.blocker_reason,
        }


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    t = text.lower()
    return any(w.lower() in t for w in words)


def normalize_receipt(
    text: str,
    *,
    evidence_obs_ids: list[str] | None = None,
    blocker_reason: str = "",
    ledger: Any = None,
) -> GoalReceipt:
    """把任意回执文本归一到 Penguin 两值契约（fail-closed）。

    判定序：
    1. 命中 GAP 词（还没做/进行中/差不多…）→ INVALID（开放任务不能拿 gap 交差）；
    2. 命中 BLOCKED 词 → BLOCKED（要求 blocker_reason 或原文有理由）；
    3. 命中 COMPLETE 词 → 需成功观测支撑：
       - 提供 ledger：查 evidence_obs_ids 里是否有成功证据，无 → INVALID；
       - 未提供 ledger：要求 evidence_obs_ids 非空，空 → INVALID（裸 complete 非法）；
    4. 都不命中 → INVALID。
    """
    raw = (text or "").strip()
    # GAP 词最优先判非法（防止「基本完成」被误判 complete）
    if _contains_any(raw, _GAP_WORDS):
        return GoalReceipt(status=ReceiptStatus.INVALID, raw=raw)

    if _contains_any(raw, _BLOCKED_WORDS):
        reason = blocker_reason or raw
        return GoalReceipt(
            status=ReceiptStatus.BLOCKED, blocker_reason=reason, raw=raw)

    if _contains_any(raw, _COMPLETE_WORDS):
        obs = evidence_obs_ids or []
        if ledger is not None:
            # 有 ledger：必须引用到成功观测
            if not ledger.has_success_evidence(obs):
                return GoalReceipt(
                    status=ReceiptStatus.INVALID,
                    evidence_obs_ids=obs, raw=raw)
            return GoalReceipt(
                status=ReceiptStatus.COMPLETE, evidence_obs_ids=obs, raw=raw)
        # 无 ledger：裸 complete 需至少引用一个 obs_id（Penguin 契约）
        if not obs:
            return GoalReceipt(
                status=ReceiptStatus.INVALID, raw=raw)
        return GoalReceipt(
            status=ReceiptStatus.COMPLETE, evidence_obs_ids=obs, raw=raw)

    # 模糊/无对应词 → fail-closed 判未完成
    return GoalReceipt(status=ReceiptStatus.INVALID, raw=raw)
