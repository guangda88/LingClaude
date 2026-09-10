"""P3.2 灵忆双写桥接器 — ContextCache → lingmemory (灵元 V1.0 重构试点)

职责（V3 §五 P3.2 试点双写）：
- 接收 ContextCache 的生命周期事件（store/evict），旁路写入灵忆 records 表
- 主路（SQLite cache_entries）零改动：桥接器是旁观者，不是参与者
- 永不抛异常：旁路任何故障（库锁/校验失败/import 失败）只降级为 warning

事件映射（依据 lingmemory/type_registry.yaml context_cache type）：
- store  → LingMemory.create(type="context_cache", state=active)
- evict  → LingMemory.transition(record_id, event="evict") → state=evicted
- hit    → 暂不回写（registry 无 hit 事件定义，hit_count 回写留待下版本，
           见 docs/P3_STATE_MIGRATION_MAP.md §四 P3.2 已知限制）

开关：LINGCLAUDE_MEMORY_DUALWRITE=1 激活（默认关，防测试/工具进程污染生产库）
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# 懒加载 lingmemory（可能不存在/未安装）
_LingMemory = None
_LingMemory_ImportError = None


def _get_lingmemory():
    """懒加载 lingmemory.LingMemory，失败返回 None。"""
    global _LingMemory, _LingMemory_ImportError
    if _LingMemory is not None or _LingMemory_ImportError is not None:
        return _LingMemory
    try:
        from lingmemory import LingMemory as _LM
        _LingMemory = _LM
    except ImportError as e:
        _LingMemory_ImportError = e
        logger.warning("lingmemory 模块不可用，双写桥接器将静默失效: %s", e)
    return _LingMemory

_DUALWRITE_ENV = "LINGCLAUDE_MEMORY_DUALWRITE"


class _CacheEventSink(Protocol):
    """ContextCache 对外的事件接口（结构化鸭子类型，不做强依赖）"""

    def on_store(self, file_path: str, file_hash: str, content: str,
                 read_count: int, first_read_at: str, last_read_at: str) -> None: ...

    def on_evict(self, file_path: str) -> None: ...


def dualwrite_enabled() -> bool:
    """双写开关：仅显式 env=1 时激活（试点期默认关，见模块 docstring）"""
    return os.environ.get(_DUALWRITE_ENV, "").strip() == "1"


class LingMemoryCacheBridge:
    """ContextCache → 灵忆 双写桥。

    懒初始化：构造零开销（不连库不读 yaml），首次 on_store 才动灵忆。
    单文件去重：同一 file_path 只 create 一条 record，后续 store 复用
    record_id（灵忆主干无 update-data 操作，内容以首写为准——对缓存
    语义无损：主路 SQLite 才是数据权威，灵忆侧是状态镜像）。
    """

    def __init__(self, db_path: Any = None, created_by: str = "lingclaude.context_cache"):
        self._db_path = db_path
        self._created_by = created_by
        self._lm: Any = None
        self._record_ids: dict[str, str] = {}
        self._lock = threading.Lock()
        self._broken = False  # 熔断：连续失败后本进程内静默

    # ------------------------------------------------------------
    # ContextCache 事件入口
    # ------------------------------------------------------------
    def on_store(self, file_path: str, file_hash: str, content: str,
                 read_count: int, first_read_at: str, last_read_at: str) -> None:
        if self._broken or not dualwrite_enabled():
            return
        try:
            with self._lock:
                record_id = self._record_ids.get(file_path)
                if record_id is None:
                    # 内存映射未命中（如进程重启）：回退查灵忆侧 active record
                    record_id = self._lookup_active(file_path)
                if record_id is None:
                    record_id = self._lm_create(file_path, file_hash, content,
                                                read_count, first_read_at, last_read_at)
                self._record_ids[file_path] = record_id
                # 已有 record：灵忆侧不覆盖内容（主路 SQLite 为权威，见类 docstring）
        except Exception as e:  # noqa: BLE001 — 旁路吞一切，见模块纪律
            self._trip("on_store", e)

    def on_evict(self, file_path: str) -> None:
        if self._broken or not dualwrite_enabled():
            return
        try:
            with self._lock:
                record_id = self._record_ids.pop(file_path, None)
            if record_id is None:
                # 内存映射未命中（如进程重启）：回退查灵忆侧 active record
                record_id = self._lookup_active(file_path)
            if record_id:
                self._lm.transition(record_id, "evict", actor=self._created_by)
        except Exception as e:  # noqa: BLE001
            self._trip("on_evict", e)

    # ------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------
    def _lookup_active(self, file_path: str) -> str | None:
        """按 cache_key 找灵忆侧 active record（跨重启续接状态机）"""
        if self._lm is None:
            return None  # 本进程尚未动过灵忆，无从查询
        items = self._lm.query(
            type="context_cache", state="active",
            data_filter={"cache_key": file_path}, limit=1,
        )["items"]
        return items[0]["id"] if items else None

    def _lm_create(self, file_path: str, file_hash: str, content: str,
                   read_count: int, first_read_at: str, last_read_at: str) -> str:
        if self._lm is None:
            LingMemory = _get_lingmemory()
            if LingMemory is None:
                raise RuntimeError("lingmemory 模块不可用")
            # 模块级 import 已就绪；此处仅实例化（连库），保持懒初始化语义
            kwargs = {"db_path": self._db_path} if self._db_path else {}
            self._lm = LingMemory(**kwargs)
        # cache_key 必填（P3.1 schema）；长路径做哈希前缀保证可读+唯一性
        cache_key = file_path if len(file_path) <= 200 else (
            hashlib.md5(file_path.encode(), usedforsecurity=False).hexdigest()
        )
        return self._lm.create(
            type="context_cache",
            data={
                "cache_key": cache_key,
                "content": content,
                "token_size": len(content),
                "layer": "l1",
                "hit_count": read_count,
                "file_path": file_path,
                "file_hash": file_hash,
                "first_read_at": first_read_at,
                "last_read_at": last_read_at,
            },
            created_by=self._created_by,
        )

    def _trip(self, where: str, exc: Exception) -> None:
        """熔断：首错告警并停摆，本进程不再重试（旁路必须静默失败）"""
        self._broken = True
        logger.warning("lingmemory_bridge.%s 失败，双写本进程内停摆: %s", where, exc)
