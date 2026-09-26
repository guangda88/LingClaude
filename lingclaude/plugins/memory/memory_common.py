"""plugins/memory/ 域公共接缝——N7 共享层（跨桥共享代码的唯一合法去处）。

背景（N7 律，tests/test_iron_law_guards.py::_n7_scan）：
    插片间禁止 import 他插片内部符号，跨桥共享必须收敛到 plugins/<域>/
    直下公共接缝模块（同 agents/mcp_common.py 惯例）。
    迁移源：lingmemory_bridge/bridge.py（域内历史共享宿主）。

迁移符号（2026-09-26，来源 bridge.py:25-207 全量盘点）：
- _DUALWRITE_ENV      双写开关 env 名
- _get_lingmemory     灵忆懒加载（失败静默 None，旁路纪律：不抛）
- dualwrite_enabled   开关判定（env 显式 =1 才开）
- _CacheEventSink     ContextCache 事件接口 Protocol
- _LingMemorySinkBase 三 sink（l7/experience/memstore）共享基类
                       （_ensure_lm 懒初始化 / _trip 熔断 / 六字段同构 __init__；
                        token 桥自有线程模型，仅共享函数不继承基类）

兼容性契约（向后兼容，防外部 patch 断链）：
    lingmemory_bridge/bridge.py 保留同名 re-export，老消费方零改动。
    注意：跨模块符号解析在 import 时绑定——若 monkeypatch
    ``memory_common._get_lingmemory``，则 ``bridge._get_lingmemory``
    与 ``_LingMemorySinkBase._ensure_lm`` 的解析目标同步改变
    （同一函数对象）；仅 patch ``bridge._get_lingmemory`` 只影响
    bridge 模块自身的名字绑定。
"""
from __future__ import annotations

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
    """双写开关：仅显式 env=1 时激活（试点期默认关，见 bridge 模块 docstring）"""
    return os.environ.get(_DUALWRITE_ENV, "").strip() == "1"


class _LingMemorySinkBase:
    """四 sink（L7/Experience/MemStore/Token）共享基类 —— 找不变砍到最薄。

    2026-09-14 (lingyuan 真重复收敛): l7/experience/memstore 三处
    _ensure_lm（懒初始化灵忆）逐字相同、_trip 逻辑相同、__init__ 六字段
    同构 —— 提取为基类，各 sink 只保留真实变化：
      - _LM_TYPE / _CREATED_BY 常量（每域不同）
      - _lookup_active（state 与 data_filter 不同）
      - _lm_create（payload 结构不同）
      - on_* 事件入口（业务域不同）
    2026-09-26 (N7 收敛): 本体从 lingmemory_bridge/bridge.py 迁入域公共
    接缝，四桥改为从本模块 import（插片横向 import 消灭）。
    纪律：只收逐字重复与同构骨架，不强行收"形似神异"的变化（灵元：砍真重复）。
    """

    _LM_TYPE: str = ""
    _CREATED_BY: str = ""
    # 子类覆写：日志前缀（默认取类名）
    _TRIP_PREFIX: str = ""

    def __init__(
        self,
        db_path: str | None = None,
        created_by: str | None = None,
    ) -> None:
        self._db_path = db_path
        self._created_by = created_by or self._CREATED_BY
        self._lm: Any = None
        self._record_ids: dict = {}
        self._lock = threading.Lock()
        self._broken = False  # 熔断：首错后本进程内静默

    def _ensure_lm(self) -> bool:
        """懒初始化灵忆实例；模块不可用返回 False（旁路纪律：不抛）"""
        if self._lm is None:
            LingMemory = _get_lingmemory()
            if LingMemory is None:
                return False
            kwargs = {"db_path": self._db_path} if self._db_path else {}
            self._lm = LingMemory(**kwargs)
        return True

    def _trip(self, where: str, exc: Exception) -> None:
        """熔断：首错告警并停摆，本进程不再重试（旁路必须静默失败）"""
        self._broken = True
        prefix = self._TRIP_PREFIX or type(self).__name__.lower()
        logger.warning(
            "%s.%s 失败，双写本进程内停摆: %s", prefix, where, exc,
        )
