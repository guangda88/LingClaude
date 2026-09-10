"""P3.3 五层记忆(L2 Experience) → 灵忆 双写桥。

承接 docs/P3_STATE_MIGRATION_MAP.md §四 P3.3 第一件
（地图 #9 core/layered_memory.py，核心目标：五层降插片）：

- 主路不变：SQLite experience.db / InMemory 经验层仍是数据权威
- 旁路镜像：L2 Experience 全生命周期 → 灵忆 records
  (type=layered_memory_entry，registry P3-9)
- L0/L1 不入灵忆（L0 静态硬编码无状态，L1 纯内存 buffer）
- L3(meta)/L4(shared) JSON 写点本件不动，留待 memory_engine/l7_cognitive
  归并件一并处理（见地图 §四 P3.3 顺序）

镜像语义（layered_memory_entry registry，2026-09-11 实测定版）：
- registry 的 state（working/episodic/semantic/...）是**认知分层**，
  源码 MemoryLayer 是**架构分层**——两者不同轴。架构归属落
  data.layer_of_origin（schema 必填）；生命周期只走
  working →(event=forget)→ archived。
- LingMemory.create() 硬编码 default_state（lingmemory/core.py:167），
  首写必为 working；出清（decay 权重<0.05、主动遗忘）统一 transition
  event=forget → archived。灵忆侧保留历史，不做物理删除（主路删、镜像留）。
- 已知限制（承 P3.2 同款）：record_recall/record_deny 引起的权重漂移
  不回写镜像（registry 无 strength 更新事件），镜像 strength 以首写为准。

纪律（承 P3.2 lingmemory_bridge 同款）：
- 旁路永不抛异常，首错熔断本进程内静默
- 开关 LINGCLAUDE_MEMORY_DUALWRITE=1 激活（与 P3.2 同一开关）
- 懒初始化：构造零开销，首次 on_store 才连灵忆
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from lingclaude.core.lingmemory_bridge import _get_lingmemory, dualwrite_enabled

logger = logging.getLogger(__name__)

_LM_TYPE = "layered_memory_entry"
_CREATED_BY = "lingclaude.layered_memory"


class LingMemoryExperienceSink:
    """ExperienceStore / InMemoryExperienceStore → 灵忆 双写旁观者。

    经 ``ExperienceStore(legacy_sink=...)`` / ``InMemoryExperienceStore(
    legacy_sink=...)`` 注入；事件接口（鸭子类型，主路 try/except 已兜底）：
    - on_store(exp_dict)        首写镜像（同 id 重复 store 不重复建）
    - on_forget(exp_id)         单条遗忘 → archived
    - on_decay_forgotten(ids)   衰减出清批量遗忘 → archived
    """

    def __init__(self, db_path: Any = None,
                 created_by: str = _CREATED_BY) -> None:
        self._db_path = db_path
        self._created_by = created_by
        self._lm: Any = None
        self._record_ids: dict[str, str] = {}
        self._lock = threading.Lock()
        self._broken = False  # 熔断：首错后本进程内静默

    # ------------------------------------------------------------
    # 事件入口（主路以 try/except 调用，此处异常也自行吞掉双保险）
    # ------------------------------------------------------------
    def on_store(self, exp_dict: dict[str, Any]) -> None:
        if self._broken or not dualwrite_enabled():
            return
        try:
            with self._lock:
                exp_id = str(exp_dict["id"])
                record_id = self._record_ids.get(exp_id)
                if record_id is None:
                    # 内存映射未命中（如进程重启）：回退查灵忆侧 working record
                    record_id = self._lookup_active(exp_id)
                if record_id is None:
                    record_id = self._lm_create(exp_dict)
                self._record_ids[exp_id] = record_id
                # 已有 record：内容以首写为准（主路权威，同 P3.2 语义）
        except Exception as e:  # noqa: BLE001 — 旁路纪律，见模块 docstring
            self._trip("on_store", e)

    def on_forget(self, exp_id: str) -> None:
        if self._broken or not dualwrite_enabled():
            return
        try:
            with self._lock:
                record_id = self._record_ids.pop(exp_id, None)
            if record_id is None:
                record_id = self._lookup_active(exp_id)
            if record_id:
                self._lm.transition(record_id, "forget", actor=self._created_by)
        except Exception as e:  # noqa: BLE001
            self._trip("on_forget", e)

    def on_decay_forgotten(self, exp_ids: tuple[str, ...] | list[str]) -> None:
        for exp_id in exp_ids:
            self.on_forget(exp_id)

    # ------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------
    def _lookup_active(self, exp_id: str) -> str | None:
        """按 experience_id 找灵忆侧 working record（跨重启续接状态机）"""
        if self._lm is None:
            return None  # 本进程尚未触灵忆，无从查询
        items = self._lm.query(
            type=_LM_TYPE, state="working",
            data_filter={"experience_id": exp_id}, limit=1,
        )["items"]
        return items[0]["id"] if items else None

    def _lm_create(self, exp_dict: dict[str, Any]) -> str:
        if self._lm is None:
            LingMemory = _get_lingmemory()
            if LingMemory is None:
                raise RuntimeError("lingmemory 模块不可用")
            kwargs = {"db_path": self._db_path} if self._db_path else {}
            self._lm = LingMemory(**kwargs)
        # 必填字段：layer_of_origin（架构层归属）+ content
        content = " | ".join(
            f"{k}={exp_dict[k]}"
            for k in ("problem", "hypothesis", "action", "result", "reflection")
            if exp_dict.get(k)
        ) or str(exp_dict.get("problem", ""))
        # JSON 兼容：datetime → isoformat，Enum → value
        last_access = exp_dict.get("last_recalled", "")
        if hasattr(last_access, "isoformat"):
            last_access = last_access.isoformat()
        emotion = getattr(exp_dict.get("emotion", "none"), "value",
                          exp_dict.get("emotion", "none"))
        return self._lm.create(
            type=_LM_TYPE,
            data={
                "experience_id": str(exp_dict["id"]),
                "layer_of_origin": "experience",
                "content": content,
                "strength": round(float(exp_dict.get("weight", 1.0)), 4),
                "access_count": int(exp_dict.get("recall_count", 0)),
                "last_access_at": last_access,
                "emotion": emotion,
                "associations": list(exp_dict.get("associations") or ()),
                "deny_count": int(exp_dict.get("deny_count", 0)),
            },
            created_by=self._created_by,
        )

    def _trip(self, where: str, exc: Exception) -> None:
        """熔断：首错告警并停摆，本进程不再重试（旁路必须静默失败）"""
        self._broken = True
        logger.warning(
            "lingmemory_experience_bridge.%s 失败，双写本进程内停摆: %s",
            where, exc,
        )
