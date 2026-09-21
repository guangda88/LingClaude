# lingclaude/core/evidence_protocol.py
"""P2-9（2026-09-21，全 15 家精读 §3.2 OMH 式证据边界协议化）。

OMH 核心纪律（oh-my-hermes）：
- 「宣称必须有观测」：没有 runtime_observation/v1 事件，UI 禁止说
  「已通过 CI / 已合并 / 已修复」。状态只能渲染**观测到的事件**。
- 「progress / gap / blocker 三语义分离」：
  - gap = 没人做过的步骤（不是失败，是空缺，不打扰用户）；
  - blocker = 运行时失败（唯一真正打扰用户的类别）；
  - progress = 正在进行的步骤。
  混淆三者会导致「把没做的当成做不了」或「把失败当普通进度」两类幻觉。

lc 现状：H17（声明-验证闭环）是**单向**的——prior_verifier 检测「已提交/
已完成」裸宣称并打回，但没有「宣称必须绑定一条 runtime_observation 事件 ID」
的协议面。本模块把 H17 升格为协议层：

1. **EvidenceLedger**：runtime_observation/v1 事件的登记簿（事件流），
   每条观测有稳定 obs_id（tool_call/命令输出/测试结果……都是观测源）。
2. **ClaimVerifier**：任何完成/成功宣称必须引用 ≥1 个 obs_id；
   无观测引用 → fail-closed 判「未验证」（不可渲染为成功态）。
3. **三语义分类器**：把一条状态消息分类为 progress / gap / blocker，
   只有 blocker 标打扰（interrupt_user=True），progress/gap 不打扰。

与 H17/prior_verifier 的关系（不替换，叠加）：
- prior_verifier 仍是「裸宣称文本检测」（启发式）；
- 本模块是「宣称-观测绑定」的协议面（结构化）——prior_verifier 命中后，
  进一步要求引用 obs_id，没有就 fail-closed。
- 置信词表闭集（plan/running/seen/failed/verified/blocked/cancelled）：
  未识别状态 fail-closed 判 not_run（OMH 同款）。

停层声明（铁律 2 细则 5）：
- 内核 = EvidenceLedger（观测事件登记，append-only）
- 接缝 = ClaimVerifier.verify(claim, cited_obs_ids) 协议
- 实现 = 单实现（进程内存 + 可选落盘）
边界纪律：本模块只「判定宣称是否可渲染为成功」，不产生宣称（生产者
是模型/工具调用方）；fail-closed 语义——无证据 = 不可宣称成功。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "ObservationKind",
    "RuntimeObservation",
    "EvidenceLedger",
    "StateClass",
    "CONFIDENCE_VOCAB",
    "classify_state",
    "ClaimVerifier",
    "VerifiedClaim",
]


# ── 置信词表闭集（OMH：未识别 fail-closed 判 not_run） ──
CONFIDENCE_VOCAB: frozenset[str] = frozenset({
    "plan", "running", "seen", "failed", "verified", "blocked", "cancelled",
})


class StateClass(str, Enum):
    """OMH 三语义 + 打扰标记。只有 BLOCKER 真正打扰用户。"""
    PROGRESS = "progress"   # 进行中（不打扰）
    GAP = "gap"             # 没人做过的步骤（不打扰，非失败）
    BLOCKER = "blocker"    # 运行时失败（唯一打扰项）


@dataclass(frozen=True)
class RuntimeObservation:
    """runtime_observation/v1 事件（观测源：工具调用/命令输出/测试结果）。

    宣称必须引用这些事件的 obs_id 才可渲染为成功态。
    """
    obs_id: str
    kind: str
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    # 该观测是否构成「成功证据」（如测试结果通过 / 命令 exit=0）
    is_success_evidence: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "runtime_observation/v1",
            "obs_id": self.obs_id, "kind": self.kind, "source": self.source,
            "payload": self.payload, "ts": self.ts,
            "is_success_evidence": self.is_success_evidence,
        }


class EvidenceLedger:
    """runtime_observation 事件登记簿（append-only，进程内 + 可选落盘）。

    用法：
        ledger = EvidenceLedger()
        obs = ledger.record_observation("tool_call", source="bash",
                                        payload={"cmd": "pytest", "exit": 0},
                                        is_success_evidence=True)
        # ... 模型宣称「测试已通过」引用 obs.obs_id ...
        verifier = ClaimVerifier(ledger)
        claim = verifier.verify("测试已通过", cited_obs_ids=[obs.obs_id])
        assert claim.verifiable
    """

    def __init__(self) -> None:
        self._obs: dict[str, RuntimeObservation] = {}
        self._order: list[str] = []

    def record_observation(
        self,
        kind: str,
        *,
        source: str = "",
        payload: dict[str, Any] | None = None,
        is_success_evidence: bool = False,
    ) -> RuntimeObservation:
        """登记一条观测（带稳定 obs_id，供宣称引用）。"""
        obs_id = f"obs-{uuid.uuid4().hex[:12]}"
        obs = RuntimeObservation(
            obs_id=obs_id, kind=kind, source=source,
            payload=payload or {}, is_success_evidence=is_success_evidence,
        )
        self._obs[obs_id] = obs
        self._order.append(obs_id)
        return obs

    def get(self, obs_id: str) -> RuntimeObservation | None:
        return self._obs.get(obs_id)

    def has_success_evidence(self, obs_ids: list[str]) -> bool:
        """引用的观测里是否有 ≥1 条成功证据（宣称可渲染为成功的依据）。"""
        return any(
            self._obs[o].is_success_evidence for o in obs_ids if o in self._obs
        )

    def unknown(self, obs_ids: list[str]) -> list[str]:
        """引用了不存在的 obs_id（伪造证据，fail-closed）。"""
        return [o for o in obs_ids if o not in self._obs]

    def all(self) -> list[RuntimeObservation]:
        return [self._obs[o] for o in self._order]

    def __len__(self) -> int:
        return len(self._order)


def classify_state(text: str, *, confidence_word: str | None = None) -> StateClass:
    """把一条状态消息三语义分类（OMH：只有 blocker 打扰用户）。

    - confidence_word 命中闭集时按其归语义：
      failed/blocked → BLOCKER；running → PROGRESS；plan/seen/cancelled/verified
      按上下文（verified=有成功观测，cancelled=非打扰）。
    - 未识别 confidence_word → fail-closed 判 not_run（归 GAP，不打扰）。
    - 文本启发式：含「失败/报错/阻塞/无法/失败退出」且非否定语境 → BLOCKER；
      含「进行中/正在/处理中」→ PROGRESS；含「尚未/未做/缺/空缺」→ GAP。
    """
    word = (confidence_word or "").lower()
    if word:
        if word in ("failed", "blocked"):
            return StateClass.BLOCKER
        if word == "running":
            return StateClass.PROGRESS
        if word in ("plan", "seen", "cancelled", "verified"):
            # 这些不直接打扰；verified 需另配成功观测才渲染成功，此处归 GAP（保守）
            return StateClass.GAP if word != "verified" else StateClass.GAP
        # 未识别 → fail-closed 判 not_run
        return StateClass.GAP

    t = (text or "").lower()
    # BLOCKER 词（运行时失败）
    blocker_words = ("失败", "报错", "阻塞", "无法", "错误", "exit=1", "exception", "error:")
    progress_words = ("进行中", "正在", "处理中", "running", "in progress", "pending")
    gap_words = ("尚未", "未做", "未开始", "缺", "空缺", "没有", "not yet", "missing")
    # 失败类最优先（blocker 是唯一天扰项，误判代价低）
    if any(w in t for w in blocker_words):
        return StateClass.BLOCKER
    if any(w in t for w in progress_words):
        return StateClass.PROGRESS
    if any(w in t for w in gap_words):
        return StateClass.GAP
    # 默认：未知状态归 GAP（保守，不打扰，不误报失败）
    return StateClass.GAP


@dataclass
class VerifiedClaim:
    """宣称的协议判定结果。"""
    text: str
    verifiable: bool           # 是否有 ≥1 条成功观测支撑（可渲染为成功）
    state: StateClass
    interrupt_user: bool       # 仅 blocker 打扰用户（progress/gap 不打扰）
    cited_obs_ids: list[str]
    unknown_obs: list[str]     # 引用了不存在的 obs_id（伪造，fail-closed）
    reason: str = ""


class ClaimVerifier:
    """宣称-观测绑定校验器（H17 协议化的核心）。

    OMH 铁律：没有 runtime_observation/v1 事件，禁止宣称「已通过/已修复」。
    - 成功类宣称必须引用 ≥1 条 is_success_evidence 的 obs_id；
    - 引用了不存在的 obs_id → 伪造证据，fail-closed 不可验证；
    - 无任何引用 → 不可验证（H17 裸宣称 fail-closed）。
    """

    # 成功类宣称标记词（命中才要求观测支撑；非成功宣称不强制）。
    # 2026-09-21: 补「通过/修复完成/搞定/搞定/全绿」等常见口语完成态——
    # 此前仅 "已通过" 等长词，"测试全部通过" 这类口语成功宣称漏网（C 场景
    # 实证：verifiable 误判 True）。
    _SUCCESS_MARKERS = ("已通过", "通过", "已修复", "修复完成", "已合并", "已提交",
                        "已完成", "完成", "成功", "搞定", "全绿", "全过", "全通过",
                        "passed", "fixed", "merged", "resolved", "verified",
                        "done", "completed")

    def __init__(self, ledger: EvidenceLedger) -> None:
        self._ledger = ledger

    def verify(
        self,
        claim_text: str,
        *,
        cited_obs_ids: list[str] | None = None,
        confidence_word: str | None = None,
    ) -> VerifiedClaim:
        """判定一条宣称是否可渲染为成功态。

        - 宣称含成功标记词 + 无有效成功观测 → verifiable=False（fail-closed）；
        - 三语义分类决定 interrupt_user（仅 blocker 打扰）；
        - 引用不存在 obs_id → unknown_obs 非空 + verifiable=False（伪造证据）。
        """
        cited = cited_obs_ids or []
        unknown = self._ledger.unknown(cited)
        state = classify_state(claim_text, confidence_word=confidence_word)

        has_success_marker = any(m in claim_text for m in self._SUCCESS_MARKERS)
        has_evidence = self._ledger.has_success_evidence(cited) if cited else False

        # 成功宣称必须有成功证据（fail-closed）
        if has_success_marker:
            verifiable = has_evidence and not unknown
        else:
            # 非成功类宣称（进度/状态描述）不强制观测，按三语义放行
            verifiable = not unknown

        interrupt = state is StateClass.BLOCKER
        reason_parts: list[str] = []
        if unknown:
            reason_parts.append(f"引用了 {len(unknown)} 个不存在的 obs_id（伪造证据）")
        if has_success_marker and not has_evidence:
            reason_parts.append("成功宣称无成功观测支撑（H17 fail-closed）")
        return VerifiedClaim(
            text=claim_text,
            verifiable=verifiable,
            state=state,
            interrupt_user=interrupt,
            cited_obs_ids=cited,
            unknown_obs=unknown,
            reason="；".join(reason_parts),
        )
