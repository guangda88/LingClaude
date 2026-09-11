"""P3.3 三件之二：MemoryStore（core/memory_engine.py）→ 灵忆 records 旁路镜像桥。

承接 docs/P3_STATE_MIGRATION_MAP.md §四 P3.3 第二件
（地图 #12 core/memory_engine.py，核心目标：与 layered_memory 合并迁移）：

- 主路不变：SQLite memory.db（MemoryStore）仍是数据权威
- 旁路镜像：MemoryStore 五类写点（episode/facet/facet_point/entity/edge）
  → 灵忆 records (type=memory_store_entry，registry P3-12)
- 无装配点：wiring.py::_memory_engine 是死槽位（T0-4 移除接线，槽位保留），
  本桥不进 wiring——sink 由调用方显式注入 MemoryStore(legacy_sink=...)。
  这与第一件（layered_memory 有 _make_layered_memory 装配缝）不同，
  属 schema-first 迁移：先铺镜像通路，待 memory_engine 真正上岗时零改动可用。

镜像语义（memory_store_entry registry，2026-09-11 实测定版）：
- default_state=live；终态 merged_away（event=merge，被更强记忆吸收）
- entry_key 规约：episode/facet/facet_point/entity 用其 id；
  edge 用 "source->target:edge_type" 复合键
- 首写为准（主路权威，同 P3.2/P3.3 第一件语义）：
  同 (store_name, entry_key) 重复 put 不更新灵忆侧 value
- 已知限制（承 P3.2 hit_count / P3.3 第一件 recall 权重漂移同款）：
  record_recall 引起的 weight/recall_count 漂移不回写镜像
  （registry 无 recall/update 事件），镜像 value 以首写为准。
- 主路当前无删除 API（put_* 全为 INSERT OR REPLACE），on_forget 供
  未来删除路径使用；触发时灵忆侧 transition(event=merge) → merged_away，
  灵忆侧留史不物理删除（主路删、镜像留，同第一件）。

纪律（承 P3.2 lingmemory_bridge / P3.3 lingmemory_experience_bridge 同款）：
- 旁路永不抛异常，首错熔断本进程内静默
- 开关 LINGCLAUDE_MEMORY_DUALWRITE=1 激活（三件共用同一开关）
- 懒初始化：构造零开销，首次 on_put 才连灵忆
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from lingclaude.core.lingmemory_bridge import _get_lingmemory, dualwrite_enabled

logger = logging.getLogger(__name__)

_LM_TYPE = "memory_store_entry"
_CREATED_BY = "lingclaude.memory_engine"


def edge_key(source_id: str, target_id: str, edge_type: str) -> str:
    """edge 复合 entry_key（与 MemoryStore.put_edge 主键三元组一一对应）"""
    return f"{source_id}->{target_id}:{edge_type}"


class LingMemoryStoreSink:
    """MemoryStore 五类写点 → 灵忆 双写旁观者。

    经 ``MemoryStore(legacy_sink=...)`` 注入；事件接口（鸭子类型，
    主路 try/except 已兜底）：
    - on_put(store_name, entry_key, value)   首写镜像（同键重复 put 不重复建）
    - on_forget(store_name, entry_key)       主路删除 → merged_away（留史）
    """

    def __init__(self, db_path: Any = None,
                 created_by: str = _CREATED_BY) -> None:
        self._db_path = db_path
        self._created_by = created_by
        self._lm: Any = None
        self._record_ids: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()
        self._broken = False  # 熔断：首错后本进程内静默

    # ------------------------------------------------------------
    # 事件入口（主路以 try/except 调用，此处异常也自行吞掉双保险）
    # ------------------------------------------------------------
    def on_put(self, store_name: str, entry_key: str,
               value: dict[str, Any]) -> None:
        if self._broken or not dualwrite_enabled():
            return
        try:
            with self._lock:
                map_key = (store_name, entry_key)
                record_id = self._record_ids.get(map_key)
                if record_id is None:
                    # 内存映射未命中（如进程重启）：回退查灵忆侧 live record
                    record_id = self._lookup_active(store_name, entry_key)
                if record_id is None:
                    record_id = self._lm_create(store_name, entry_key, value)
                self._record_ids[map_key] = record_id
                # 已有 record：value 以首写为准（主路权威，同 P3.2 语义）
        except Exception as e:  # noqa: BLE001 — 旁路纪律，见模块 docstring
            self._trip("on_put", e)

    def on_forget(self, store_name: str, entry_key: str) -> None:
        if self._broken or not dualwrite_enabled():
            return
        try:
            with self._lock:
                record_id = self._record_ids.pop((store_name, entry_key), None)
            if record_id is None:
                record_id = self._lookup_active(store_name, entry_key)
            if record_id:
                self._lm.transition(record_id, "merge", actor=self._created_by)
        except Exception as e:  # noqa: BLE001
            self._trip("on_forget", e)

    # ------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------
    def _ensure_lm(self) -> bool:
        """懒初始化灵忆实例；模块不可用返回 False（旁路纪律：不抛）"""
        if self._lm is None:
            LingMemory = _get_lingmemory()
            if LingMemory is None:
                return False
            kwargs = {"db_path": self._db_path} if self._db_path else {}
            self._lm = LingMemory(**kwargs)
        return True

    def _lookup_active(self, store_name: str, entry_key: str) -> str | None:
        """按 (store_name, entry_key) 找灵忆侧 live record（跨重启续接状态机）"""
        if not self._ensure_lm():
            return None  # lingmemory 不可用，无从查询
        items = self._lm.query(
            type=_LM_TYPE, state="live",
            data_filter={"store_name": store_name, "entry_key": entry_key},
            limit=1,
        )["items"]
        return items[0]["id"] if items else None

    def _lm_create(self, store_name: str, entry_key: str,
                   value: dict[str, Any]) -> str:
        if not self._ensure_lm():
            raise RuntimeError("lingmemory 模块不可用")
        # registry 必填：store_name + entry_key；content 作人类可读摘要
        content = f"{store_name}/{entry_key}"
        summary = value.get("title") or value.get("name") or value.get("claim")
        if summary:
            content = f"{content} | {summary}"
        return self._lm.create(
            type=_LM_TYPE,
            data={
                "store_name": store_name,
                "entry_key": entry_key,
                "value": dict(value),
                "content": content,
                "version": 1,
                "writer": self._created_by,
            },
            created_by=self._created_by,
        )

    def _trip(self, where: str, exc: Exception) -> None:
        """熔断：首错告警并停摆，本进程不再重试（旁路必须静默失败）"""
        self._broken = True
        logger.warning(
            "lingmemory_memstore_bridge.%s 失败，双写本进程内停摆: %s",
            where, exc,
        )
