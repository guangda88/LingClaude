"""P3.3 三件之三：l7_cognitive 双写灵忆镜像桥。

映射语义（type=l7_state，registry P3-14，2026-09-11 schema-first 定版）：
- 主路权威：内容以 CognitiveStore.put_* 首写为准，重复 put（INSERT OR REPLACE
  同 id）不重复建 record（anchor 去重，跨进程重启回查续接）
- 生命周期：默认 active；P3.x 后续件可用 cool/rewarm/archive 迁移留史
- 复合键：edge 用 ``src->rel->tgt``（与主路 f-string 双锚一致，防漂移）
- 纪律承袭（P3.2/P3.3 全套）：旁路永不抛 / 懒初始化 / 首错熔断 /
  LINGCLAUDE_MEMORY_DUALWRITE 开关 / 防递归由主路 _emit 卫兵承担
- 并发注：_lm_create 在锁外执行（防测试 sink 重入主路时锁自死锁），
  同 anchor 并发首写可能产生重复 record，属 best-effort 可接受（主路权威）
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from lingclaude.core.lingmemory_bridge import (
    _LingMemorySinkBase,
    _get_lingmemory,
    dualwrite_enabled,
)

logger = logging.getLogger(__name__)

_LM_TYPE = "l7_state"
_CREATED_BY = "lingclaude.l7_cognitive"


def l7_edge_key(source_id: str, relation: str, target_id: str) -> str:
    """edge 复合键：与 CognitiveStore.put_edge 主路 f-string 双锚。"""
    return f"{source_id}->{relation}->{target_id}"


class LingMemoryL7Sink(_LingMemorySinkBase):

    _LM_TYPE = "l7_state"
    _CREATED_BY = "lingclaude.l7_cognitive"
    """CognitiveStore.put_* → 灵忆 双写旁观者。

    事件接口（鸭子类型，主路 _emit 已兜底）：
    - on_memory / on_doc / on_glossary / on_edge / on_session_log

    记录去重锚：``origin:entry_key`` 复合（entry_key 内已含业务唯一键）。
    """

    # ------------------------------------------------------------
    # 事件入口（主路 _emit 已吞异常，此处自身再兜一层双保险）
    # ------------------------------------------------------------
    def on_memory(self, payload: dict[str, Any]) -> None:
        self._mirror(payload)

    def on_doc(self, payload: dict[str, Any]) -> None:
        self._mirror(payload)

    def on_glossary(self, payload: dict[str, Any]) -> None:
        self._mirror(payload)

    def on_edge(self, payload: dict[str, Any]) -> None:
        self._mirror(payload)

    on_session_log = on_memory  # 事件同构：镜像逻辑一致，仅 payload 不同

    # ------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------
    def _mirror(self, payload: dict[str, Any]) -> None:
        if self._broken or not dualwrite_enabled():
            return
        try:
            anchor = f'{payload.get("origin", "")}:{payload.get("entry_key", "")}'
            record_id = self._record_ids.get(anchor)
            if record_id is None:
                record_id = self._lookup_active(anchor)
            if record_id is None:
                record_id = self._lm_create(payload)  # 锁外：防重入死锁
            with self._lock:
                self._record_ids[anchor] = record_id
                # 已有 record：内容以首写为准（主路权威，同 P3.2/P3.3 前两件语义）
        except Exception as e:  # noqa: BLE001 — 旁路纪律，见模块 docstring
            self._trip("_mirror", e)

    def _lookup_active(self, anchor: str) -> str | None:
        """按 anchor 找灵忆侧 active record（跨重启续接状态机）"""
        if not self._ensure_lm():
            return None  # lingmemory 不可用，无从查询
        origin, _, key = anchor.partition(":")
        items = self._lm.query(
            type=self._LM_TYPE, state="active",
            data_filter={"origin": origin, "entry_key": key}, limit=1,
        )["items"]
        return items[0]["id"] if items else None

    def _lm_create(self, payload: dict[str, Any]) -> str:
        if not self._ensure_lm():
            raise RuntimeError("lingmemory 模块不可用")
        return self._lm.create(
            type=self._LM_TYPE,
            data={
                "origin": str(payload.get("origin", "")),
                "origin_id": str(payload.get("origin_id", "")),
                "entry_key": str(payload.get("entry_key", "")),
                "summary": str(payload.get("summary", "")),
                "importance": payload.get("importance", 5),
                "tier": str(payload.get("tier", "")),
            },
            created_by=self._created_by,
        )

