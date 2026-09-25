"""免事件化白名单 event_exempt（建闸期④，synthesis 修正版 A 裁定落地）。

教义 v2 三条款（review-20260925-thin-trunk-2t3a-kernel-proposal §增量预约）：
1. 豁免的是中间态事件，不是边界事件——白名单热路径只记 enter/exit/error
   三类边界事件；任何白名单动作至少留 3 条账，0 条才是违规；
2. 豁免登记在册、可撤销——豁免表本身是 StateStore 里的一条 record
   （record_type=event_exempt_whitelist），成员+理由+到期复查日；
   到期未复查自动失效回全记账（豁免本身要被审计，Goodhart 防御）；
3. error 事件永不豁免——白名单路径出错时升格全粒度（escalated=True），
   出错的热路径恰恰是最需要审计粒度的时刻。

三值判定 verdict：
- RECORD_FULL     全粒度落账（不在表 / 已到期 / phase=error / 表损坏 fail-open）
- RECORD_BOUNDARY 仅边界事件（在表 fiber 的 enter/exit）
- RECORD_EXEMPT   中间态豁免，不落账

fail-safe 方向铁律：任何异常（表损坏/读取失败/判定出错）一律回退全记账。
豁免失效只会多记，不会漏记——账本宁可膨胀，审计不可瞎掉。

命名隔离（docs/design/event_exempt_policy.md）：
- whitelist   一词在 lifecycle 语境专指熔断豁免（断路侧，53a0166 已落地）
- event_exempt 一词在 event 语境专指记账豁免（账本侧，本模块）
两者禁止跨用。本模块不触碰 state_store.py（L1 主干冻结区），
事件写入前置检查在 emit_boundary_event 内部实现。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from lingclaude.core.state_store import StateStore

logger = logging.getLogger(__name__)

# 豁免表 record 定位（StateStore 单键单表）
EVENT_EXEMPT_RECORD_TYPE = "event_exempt_whitelist"
EVENT_EXEMPT_RECORD_KEY = "global"

# 事件 record 类型（emit_boundary_event 的落账位）
PLUGIN_EVENTS_RECORD_TYPE = "plugin_events"

# 生命周期阶段常量（边界三事件）
PHASE_ENTER = "enter"
PHASE_EXIT = "exit"
PHASE_ERROR = "error"

# 三值判定
RECORD_FULL = "record_full"          # 全粒度
RECORD_BOUNDARY = "record_boundary"  # 仅边界事件
RECORD_EXEMPT = "record_exempt"      # 中间态不落账

# 默认到期复查窗口（天）
DEFAULT_REVIEW_DAYS = 30

_BOUNDARY_PHASES = (PHASE_ENTER, PHASE_EXIT)


@dataclass
class ExemptEntry:
    """单条豁免登记：谁、为什么、谁批的、何时复查。"""

    fiber: str
    reason: str              # 加白理由（如"实测单日 N 万次调用，逐条落账日增 X MB"）
    added_by: str
    added_at: str            # ISO 时间
    review_due: str          # ISO 时间，到期未复查自动失效
    evidence: dict[str, Any] = field(default_factory=dict)  # 支撑量级claim的实测数据

    def to_dict(self) -> dict[str, Any]:
        return {
            "fiber": self.fiber,
            "reason": self.reason,
            "added_by": self.added_by,
            "added_at": self.added_at,
            "review_due": self.review_due,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExemptEntry":
        return cls(
            fiber=d["fiber"],
            reason=d.get("reason", ""),
            added_by=d.get("added_by", ""),
            added_at=d.get("added_at", ""),
            review_due=d.get("review_due", ""),
            evidence=d.get("evidence", {}),
        )

    def is_expired(self, now: datetime) -> bool:
        """到期判定：review_due 解析失败视为已过期（fail-open 全记账）。"""
        try:
            return datetime.fromisoformat(self.review_due) < now
        except (ValueError, TypeError):
            return True


@dataclass
class EventExemptPolicy:
    """豁免策略（内存态）：从 StateStore record 加载，判定纯函数化。"""

    entries: list[ExemptEntry] = field(default_factory=list)
    _expired_warned: set = field(default_factory=set, repr=False)

    @classmethod
    def from_record(cls, payload: dict[str, Any] | None) -> "EventExemptPolicy":
        """从 record payload 构造；payload 非 dict / members 非列表 → 空表。"""
        if not isinstance(payload, dict):
            return cls()
        members = payload.get("members", [])
        if not isinstance(members, list):
            return cls()
        entries = []
        for m in members:
            if isinstance(m, dict) and "fiber" in m:
                try:
                    entries.append(ExemptEntry.from_dict(m))
                except (KeyError, TypeError) as e:
                    logger.warning("event_exempt: 豁免条目损坏，跳过（fail-open）: %s", e)
        return cls(entries=entries)

    def to_record(self) -> dict[str, Any]:
        return {"version": 1, "members": [e.to_dict() for e in self.entries]}

    def _active_entry(self, fiber: str, now: datetime) -> ExemptEntry | None:
        """取 fiber 的未到期豁免条目；到期即失效（回全记账）并提示复查。"""
        for e in self.entries:
            if e.fiber == fiber:
                if e.is_expired(now):
                    if fiber not in self._expired_warned:
                        logger.warning(
                            "event_exempt: fiber=%r 豁免已到期（review_due=%s），"
                            "自动失效回全记账，请复查续期或撤销",
                            fiber, e.review_due,
                        )
                        self._expired_warned.add(fiber)
                    return None
                return e
        return None

    def should_record(self, fiber: str, phase: str, now: datetime | None = None) -> str:
        """三值判定（纯函数，核心语义三条款全在此）。

        1. error 永不豁免 → 恒 RECORD_FULL（escalated 由写入点标注）
        2. 不在表/已到期/表损坏 → RECORD_FULL
        3. 在表 fiber 的 enter/exit → RECORD_BOUNDARY（边界三事件纪律）
        4. 在表 fiber 的中间态 → RECORD_EXEMPT
        """
        now = now or datetime.now()
        if phase == PHASE_ERROR:
            return RECORD_FULL  # 条款3：出错的热路径恰是最需要审计粒度的时刻
        if self._active_entry(fiber, now) is None:
            return RECORD_FULL
        if phase in _BOUNDARY_PHASES:
            return RECORD_BOUNDARY
        return RECORD_EXEMPT


def load_policy(store: StateStore, root: Any = None) -> EventExemptPolicy:
    """从 StateStore 读豁免表 record；读取异常 → 空 policy（全记账 fail-open）。"""
    try:
        payload = store.load(EVENT_EXEMPT_RECORD_TYPE, EVENT_EXEMPT_RECORD_KEY, root)
    except Exception as e:  # best-effort：表不可读绝不阻断事件路径
        logger.warning("event_exempt: 豁免表读取失败，按空表处理（全记账）: %s", e)
        return EventExemptPolicy()
    return EventExemptPolicy.from_record(payload)


def save_policy(store: StateStore, policy: EventExemptPolicy, root: Any = None) -> None:
    """豁免表 record 回写（经 StateStore 双写纪律）。"""
    store.save(EVENT_EXEMPT_RECORD_TYPE, EVENT_EXEMPT_RECORD_KEY, policy.to_record(), root)


def add_exemption(
    store: StateStore,
    fiber: str,
    reason: str,
    review_days: int = DEFAULT_REVIEW_DAYS,
    added_by: str = "lingke",
    evidence: dict[str, Any] | None = None,
    root: Any = None,
    now: datetime | None = None,
) -> EventExemptPolicy:
    """登记豁免（读-改-写；已存在则覆盖理由与复查日）。

    加白必须给理由——豁免本身要被审计（Goodhart 防御条款2）。
    """
    now = now or datetime.now()
    policy = load_policy(store, root)
    entry = ExemptEntry(
        fiber=fiber,
        reason=reason,
        added_by=added_by,
        added_at=now.isoformat(),
        review_due=(now + timedelta(days=review_days)).isoformat(),
        evidence=evidence or {},
    )
    policy.entries = [e for e in policy.entries if e.fiber != fiber]
    policy.entries.append(entry)
    save_policy(store, policy, root)
    return policy


def revoke_exemption(
    store: StateStore, fiber: str, root: Any = None
) -> EventExemptPolicy:
    """撤销豁免：该 fiber 立即回全记账。"""
    policy = load_policy(store, root)
    before = len(policy.entries)
    policy.entries = [e for e in policy.entries if e.fiber != fiber]
    if len(policy.entries) != before:
        save_policy(store, policy, root)
    return policy


def emit_boundary_event(
    store: StateStore,
    fiber: str,
    phase: str,
    data: dict[str, Any] | None = None,
    root: Any = None,
    now: datetime | None = None,
) -> bool:
    """公共事件写入点：前置检查（should_record）+ 落账，本模块即"事件写入点"。

    - RECORD_EXEMPT  → 不写，返回 False（中间态豁免）
    - RECORD_BOUNDARY → 写边界粒度事件，返回 True
    - RECORD_FULL    → 写全粒度事件；phase=error 时 escalated=True
    - 任何落账异常只 warning 不上抛（best-effort，与 state_store 同纪律）
    """
    now = now or datetime.now()
    try:
        policy = load_policy(store, root)
        verdict = policy.should_record(fiber, phase, now)
    except Exception as e:  # 双保险：判定层异常也 fail-open 全记账
        logger.warning("event_exempt: 判定异常，按全记账处理: %s", e)
        verdict = RECORD_FULL

    if verdict == RECORD_EXEMPT:
        return False

    ts = now.isoformat(timespec="microseconds")
    payload: dict[str, Any] = {
        "fiber": fiber,
        "phase": phase,
        "ts": ts,
        "granularity": "boundary" if verdict == RECORD_BOUNDARY else "full",
        "data": data or {},
    }
    if phase == PHASE_ERROR:
        payload["escalated"] = True  # 条款3：错误升格全粒度标记
    try:
        store.save(PLUGIN_EVENTS_RECORD_TYPE, f"{fiber}/{ts}", payload, root)
        return True
    except Exception as e:
        logger.warning("event_exempt: 事件落账失败（不影响主流程）: %s", e)
        return False
