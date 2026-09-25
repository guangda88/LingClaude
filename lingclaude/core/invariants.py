"""不变量守护框架（建闸期③）——synthesis-20260925 评审合议 #5。

定位（atomcode 评审）：「不追求证明正确，追求错误立即被捕获」。
- 合法转换表：状态机的边白名单，from→to 未定义的边 = 可疑
- UndefinedTransitionError：strict 模式下非法转换立即抛（错误即失败）
- 三值判定（claude 评审）：legal / illegal / unclassified——
  from 态不在表中时是 unclassified（表未覆盖 ≠ 非法转换），
  unclassified 不抛错、只告警，交人工裁决后补表（「不可判定→人工裁决
  是合法 transition」的同构落地）
- 预置 PLUGIN_LIFECYCLE_TABLE：plugin_lifecycle 六态实测行为的边表

模式：
- strict  : 非法转换抛 UndefinedTransitionError（测试/验收期用）
- observe : 非法转换只记录 + 告警（生产默认，先观察补表再收紧）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

CLASS_LEGAL = "legal"
CLASS_ILLEGAL = "illegal"
CLASS_UNCLASSIFIED = "unclassified"


class UndefinedTransitionError(Exception):
    """strict 模式下出现合法转换表之外的转换。"""


def _build_plugin_lifecycle_table() -> dict[Optional[str], set[str]]:
    """plugin_lifecycle 六态边表（按实测赋值点整理，非凭空设计）。

    赋值点实测（plugin_lifecycle.py）：
    attach→PENDING；_activate→LOADING/ACTIVE/FAILED；
    _unload_fiber→UNLOADING/INACTIVE(finally)；refresh 熔断拒绝→INACTIVE。
    None 表示初态（attach 创建）。
    """
    return {
        None: {"PENDING"},
        # PENDING→UNLOADING：detach 对未激活 fiber / attach 同名覆盖旧 fiber
        # 均走 _unload_fiber（:463 无前置状态过滤）——实测合法边。
        "PENDING": {"LOADING", "UNLOADING", "INACTIVE"},
        "LOADING": {"ACTIVE", "FAILED"},
        # ACTIVE→FAILED 补边（2026-09-25 读证）：_activate 中赋 ACTIVE(:447)
        # 之后 compute_epoch(:448)/record_success(:449) 仍在同一 try 窗口，
        # 抛异常走 except 落 FAILED(:452)——except 窗口可达的结构事实边。
        # 另 refresh(:508-509) 语义：ACTIVE 再激活必先 UNLOADING（与下边一致）。
        "ACTIVE": {"UNLOADING", "FAILED"},
        "UNLOADING": {"INACTIVE"},
        # 注意：不存在 INACTIVE/FAILED→ACTIVE 直跳边——真实代码 _activate
        # 先赋 LOADING（:435）再赋 ACTIVE（:447），观察者按赋值粒度必然
        # 看到 LOADING 中间态。直跳 ACTIVE 只会是 bug，必须被守卫抓住。
        "INACTIVE": {"LOADING", "UNLOADING", "INACTIVE"},
        "FAILED": {"LOADING", "UNLOADING", "INACTIVE"},
    }


PLUGIN_LIFECYCLE_TABLE = _build_plugin_lifecycle_table()


@dataclass
class TransitionViolation:
    """一次可疑转换的记录。"""
    subject: str
    frm: Optional[str]
    to: str
    classification: str          # illegal / unclassified
    detail: str = ""


class TransitionTable:
    """状态转换边表：define 边 + classify 三值判定。"""

    def __init__(self, edges: dict[Optional[str], Iterable[str]] | None = None,
                 name: str = "table") -> None:
        self.name = name
        self._edges: dict[Optional[str], set[str]] = {}
        for frm, tos in (edges or {}).items():
            self.define(frm, tos)

    def define(self, frm: Optional[str], tos: Iterable[str]) -> None:
        """定义/扩充 from 态的合法出边。"""
        cur = self._edges.setdefault(frm, set())
        cur.update(tos)

    def classify(self, frm: Optional[str], to: str) -> str:
        """三值判定：legal / illegal / unclassified。

        - from 态在表中且 to 在出边内 → legal
        - from 态在表中且 to 不在出边内 → illegal（明确禁止）
        - from 态不在表中 → unclassified（表未覆盖，不断言非法）
        """
        if frm not in self._edges:
            return CLASS_UNCLASSIFIED
        return CLASS_LEGAL if to in self._edges[frm] else CLASS_ILLEGAL

    def edges(self) -> dict[Optional[str], set[str]]:
        return {k: set(v) for k, v in self._edges.items()}


class StateInvariantGuard:
    """状态不变量守卫：观察转换、分类、按模式处置。"""

    def __init__(self, table: TransitionTable | None = None,
                 mode: str = "observe",
                 subject: str = "state") -> None:
        if mode not in ("strict", "observe"):
            raise ValueError("guard mode 必须是 strict 或 observe")
        self.table = table or TransitionTable(name=subject)
        self.mode = mode
        self.subject = subject
        self.violations: list[TransitionViolation] = []

    def observe_transition(self, subject: str, frm: Optional[str], to: str) -> None:
        """观察一次转换，按模式处置：

        - legal      : 通过
        - illegal    : strict → UndefinedTransitionError；observe → 记录+告警
        - unclassified: 不抛（表未覆盖≠非法），记录+告警，待人工裁决补表
        """
        c = self.table.classify(frm, to)
        if c == CLASS_LEGAL:
            return
        rec = TransitionViolation(
            subject=subject, frm=frm, to=to, classification=c,
            detail=(f"{self.table.name}: {subject} {frm}→{to} 未定义"
                    if c == CLASS_ILLEGAL
                    else f"{self.table.name}: from 态 {frm!r} 不在表中（unclassified，待人工裁决）"))
        self.violations.append(rec)
        if c == CLASS_ILLEGAL and self.mode == "strict":
            raise UndefinedTransitionError(rec.detail)
        logger.warning("invariant: %s", rec.detail)

    def reset(self) -> None:
        self.violations.clear()
