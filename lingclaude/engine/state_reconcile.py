"""双写对账器（建闸期②）——synthesis-20260925 评审合议 #4。

背景（crush 评审「静默写偏」）：StateStore.save 双写期对 lingyi 侧失败只
logger.warning——新库失败被 best-effort 吞掉，累积成不可逆分叉。对账器是
解药：周期性 checksum 比对 json（事实标准）与 lingyi（镜像）两侧，把
「写偏」从沉默变成有账可查的告警。

判定三值语义（claude 评审）：consistent / drift / error。
- consistent: 规范化哈希一致
- drift:      双侧可读但内容不一致（真实写偏）
- error:      单侧不可读/不可达（无法判定≠有偏——不与 drift 混记）

用法（周期对账）::

    rec = StateReconciler(store).reconcile("session_state")
    if rec.verdict == "drift":
        ...  # 告警/冻结读切换（读切换前必须 rec.verdict == "consistent"）
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lingclaude.core.state_store import JsonFileBackend, LingYiBackend, StateStore

logger = logging.getLogger(__name__)

VERDICT_CONSISTENT = "consistent"
VERDICT_DRIFT = "drift"
VERDICT_ERROR = "error"


def canonical_json(payload: dict[str, Any]) -> str:
    """规范化 JSON（排序键 + 紧凑分隔符）——消除两侧编码差异的比对基础。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def payload_hash(payload: dict[str, Any]) -> str:
    """payload 规范化哈希（sha256 前 16 位）——对账比对单元。"""
    return hashlib.sha256(
        canonical_json(payload).encode("utf-8")).hexdigest()[:16]


@dataclass
class RecordVerdict:
    """单条记录的对账判定。"""
    record_type: str
    key: str
    verdict: str                    # consistent / drift / error
    json_hash: str | None = None
    lingyi_hash: str | None = None
    detail: str | None = None       # error 时的原因（drift 时可空）


@dataclass
class ReconcileReport:
    """一轮对账报告：verdict 聚合 + 逐条明细。

    - ok(): 无 drift 且无 error → 读切换（切片2→3）的前置满足
    - has_error 但无 drift: 灵忆侧问题（不可达/缺表），不是写偏——不冻结迁移动作，
      但读切换必须等 error 清零（error 侧读不到 = 无法证明一致）
    - has_drift: 真实写偏 → 冻结读切换，先修复再对账
    """
    record_type: str
    verdicts: list[RecordVerdict] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        out = {VERDICT_CONSISTENT: 0, VERDICT_DRIFT: 0, VERDICT_ERROR: 0}
        for v in self.verdicts:
            out[v.verdict] = out.get(v.verdict, 0) + 1
        return out

    @property
    def has_drift(self) -> bool:
        return any(v.verdict == VERDICT_DRIFT for v in self.verdicts)

    @property
    def has_error(self) -> bool:
        return any(v.verdict == VERDICT_ERROR for v in self.verdicts)

    def ok(self) -> bool:
        return not self.has_drift and not self.has_error

    def summary(self) -> str:
        c = self.counts
        head = (f"对账[{self.record_type}]: consistent={c.get(VERDICT_CONSISTENT, 0)} "
                f"drift={c.get(VERDICT_DRIFT, 0)} error={c.get(VERDICT_ERROR, 0)}")
        if self.ok():
            return head + " → 可读切换"
        if self.has_drift:
            keys = [v.key for v in self.verdicts if v.verdict == VERDICT_DRIFT]
            return head + f" → 冻结读切换，写偏 keys={keys[:8]}"
        return head + " → 灵忆侧不可达，不判写偏，读切换待恢复"


class StateReconciler:
    """json（事实标准）× lingyi（镜像）周期对账器。

    - key 空间：并集比对（json 有/lingyi 无 = missing_in_lingyi 写偏；
      反向 = missing_in_json，镜像侧多出——同属 drift，方向记入 detail）
    - lingyi 不可达（DSN 缺失/asyncpg 缺失/连接失败）：全部记 error，不记 drift
    - 比对用规范化哈希：两侧编码差异（indent/键序/ensure_ascii）不产生假阳性
    - 对账动作本身绝不写任何一侧（只读比照，修复动作由调用方显式发起）
    """

    def __init__(self, store: StateStore | None = None,
                 json_backend: JsonFileBackend | None = None,
                 lingyi_backend: LingYiBackend | None = None) -> None:
        if store is not None:
            self._json = store._json_backend
            self._lingyi = store._lingyi_backend or store._get_lingyi()
        else:
            self._json = json_backend or JsonFileBackend()
            self._lingyi = lingyi_backend or LingYiBackend()

    # ---- 单记录判定 ----

    def _judge(self, record_type: str, key: str, root: Path | None) -> RecordVerdict:
        v = RecordVerdict(record_type=record_type, key=key,
                          verdict=VERDICT_CONSISTENT)
        # 读 json（事实标准）
        try:
            jp = self._json.load(record_type, key, root)
        except Exception as exc:  # noqa: BLE001
            v.verdict = VERDICT_ERROR
            v.detail = f"json 读失败: {type(exc).__name__}: {exc}"
            return v
        # 读 lingyi（镜像）
        try:
            lp = asyncio.run(self._lingyi.load(record_type, key, root))
        except Exception as exc:  # noqa: BLE001
            v.verdict = VERDICT_ERROR
            v.detail = f"lingyi 不可达: {type(exc).__name__}: {exc}"
            return v
        # 缺失组合判定（error 优先于 drift：读不到≠不存在？——json 文件读不到
        # load 返回 None；lingyi 表读不到也返回 None。None 是合法值，
        # 缺失方向性是 drift 证据；但 lingyi 侧系统性不可达已在上面转 error）
        if jp is None and lp is None:
            return v  # 双侧皆无 → 一致（空对空）
        if jp is None or lp is None:
            v.verdict = VERDICT_DRIFT
            v.detail = ("missing_in_lingyi" if lp is None
                        else "missing_in_json")
            v.json_hash = payload_hash(jp) if jp is not None else None
            v.lingyi_hash = payload_hash(lp) if lp is not None else None
            return v
        jh, lh = payload_hash(jp), payload_hash(lp)
        v.json_hash, v.lingyi_hash = jh, lh
        if jh != lh:
            v.verdict = VERDICT_DRIFT
        return v

    # ---- 空间对账 ----

    def reconcile(self, record_type: str, keys: list[str] | None = None,
                  root: Path | None = None) -> ReconcileReport:
        """对某 record_type 全量（或指定 keys）对账。

        key 空间 = 两侧 list_keys 的并集；keys 显式给出时用给定集合。
        """
        report = ReconcileReport(record_type=record_type)
        # 两侧 key 枚举（lingyi 枚举失败 → error 判定并入报告，不抛）
        try:
            json_keys = self._json.list_keys(record_type, root)
        except Exception as exc:  # noqa: BLE001
            report.verdicts.append(RecordVerdict(
                record_type=record_type, key="<list_keys>",
                verdict=VERDICT_ERROR,
                detail=f"json 枚举失败: {type(exc).__name__}: {exc}"))
            return report
        try:
            ly_keys = asyncio.run(self._lingyi.list_keys(record_type, root))
        except Exception as exc:  # noqa: BLE001
            # lingyi 整体不可达：全 keys 记 error（不判 drift）
            for k in (keys if keys is not None else json_keys) or ["<none>"]:
                report.verdicts.append(RecordVerdict(
                    record_type=record_type, key=k, verdict=VERDICT_ERROR,
                    detail=f"lingyi 不可达: {type(exc).__name__}: {exc}"))
            return report
        union = set(keys) if keys is not None else (set(json_keys) | set(ly_keys))
        for k in sorted(union):
            if keys is None and k not in json_keys and k not in ly_keys:
                continue
            report.verdicts.append(self._judge(record_type, k, root))
        return report
