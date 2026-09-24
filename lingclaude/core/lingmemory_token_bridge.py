"""P3.4 灵忆双写桥接器 — TokenMonitor → lingmemory（灵元 V1.0 重构）

承接 docs/P3_STATE_MIGRATION_MAP.md §四 P3.4（行为指标回路重新合闸）
（地图 #14 core/token_monitor.py：持久化×14 → token_usage_record + 对照回路）：

- 主路不变：SQLite token_monitor.db usage_records 仍是数据权威
- 旁路镜像：record_usage 每次用量 → 灵忆 records 3 条
  (type=token_usage_record，registry P3-14；usage_kind=input/output/total
  schema 要求分立，故一次 record 镜像 3 条)
- 阈值事件（analyze/breach 流转）镜像留待回路上线后按状态机补齐，
  本件只铺 recorded 首写（P3.4 范围 = 用量镜像 + parity 对照回路）

镜像语义（token_usage_record registry，2026-09-11 实测定版）：
- 追加型 telemetry：与前四桥（状态实体：同键单 record）本质不同——
  用量记录是事实流不是可变状态，不设去重缓存，灵忆侧追加留史
- registry 字段除 usage_kind/token_count 外全部 optional，
  源字段缺省不镜像该字段（JSON 不落 None，保持 schema 干净）
- 每线程独立 LingMemory 实例（threading.local）：lingmemory sqlite
  连接默认 check_same_thread=True，跨线程复用单实例必炸
  ProgrammingError → 熔断假阳性；分实例根治
- 并发首连串行化（_init_lock）：LingMemory.__init__ 会跑 _maybe_migrate_v0_3，
  多线程同时首连同一库 = 迁移竞态（P3.4 实测 bug #2）

纪律（承 P3.2/P3.3 同款）：
- 旁路永不抛异常，首错熔断本进程内静默
- 开关 LINGCLAUDE_MEMORY_DUALWRITE=1 激活（与 P3.2/P3.3 同一开关）
- 懒初始化：构造零开销，首次 on_usage 才连灵忆
- 自指卫兵（thread-local）：重入=同调用栈概念；实例共享布尔会把
  跨线程并发 emit 误判为重入静默丢弃（P3.4 实测 bug #3）
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from lingclaude.core.lingmemory_bridge import _get_lingmemory, dualwrite_enabled

logger = logging.getLogger(__name__)

_LM_TYPE = "token_usage_record"
_CREATED_BY = "lingclaude.token_monitor"


class LingMemoryTokenSink:
    """TokenMonitor.record_usage → 灵忆 双写旁观者（追加型 telemetry）。

    经 ``TokenMonitor(legacy_sink=...)`` 注入；事件接口（鸭子类型，
    主路 try/except 已兜底）：
    - on_usage(usage_dict)   一次用量 → 3 条镜像（input/output/total）
    """

    def __init__(self, db_path: Any = None,
                 created_by: str = _CREATED_BY) -> None:
        self._db_path = db_path
        self._created_by = created_by
        self._tls = threading.local()  # 每线程：独立灵忆实例 + emitting 卫兵
        self._init_lock = threading.Lock()  # 并发首连迁移竞态防护（见 docstring）
        self._broken = False  # 熔断：首错后本进程内静默

    # ------------------------------------------------------------
    # 事件入口（主路以 try/except 调用，此处异常也自行吞掉双保险）
    # ------------------------------------------------------------
    def on_usage(self, usage: dict[str, Any]) -> None:
        if self._broken or getattr(self._tls, "emitting", False) \
                or not dualwrite_enabled():
            return
        self._tls.emitting = True
        try:
            self._mirror(usage)
        except Exception as e:  # noqa: BLE001 — 旁路纪律，见模块 docstring
            self._trip("on_usage", e)
        finally:
            self._tls.emitting = False

    # ------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------
    def _ensure_lm(self) -> Any | None:
        """懒初始化本线程灵忆实例；模块不可用返回 None（旁路纪律：不抛）"""
        lm = getattr(self._tls, "lm", None)
        if lm is None:
            LingMemory = _get_lingmemory()
            if LingMemory is None:
                return None
            kwargs = {"db_path": self._db_path} if self._db_path else {}
            with self._init_lock:
                lm = LingMemory(**kwargs)
            self._tls.lm = lm
        return lm

    @staticmethod
    def _clean(fields: dict[str, Any]) -> dict[str, Any]:
        """None/空值不落 data（registry optional 字段保持 JSON 干净）"""
        return {k: v for k, v in fields.items() if v not in (None, "")}

    def _mirror(self, usage: dict[str, Any]) -> None:
        lm = self._ensure_lm()
        if lm is None:
            raise RuntimeError("lingmemory 模块不可用")  # → 熔断（family 同款）

        metadata = usage.get("metadata")
        # 会话回链（token-schema-legacy-path-ingest 清偿③, 2026-09-24）:
        # D3 sink 链传 session_id, 此前只读 session_ref → 镜像恒无回链;
        # 两者兼容读取（session_ref 优先, 兼容旧调用方）。
        if isinstance(metadata, dict):
            session_ref = (
                metadata.get("session_ref")
                or metadata.get("session_id")
            )
        else:
            session_ref = None
        base = self._clean({
            "model_name": usage.get("model"),
            "task_type": usage.get("task_type"),
            "session_ref": session_ref,
        })
        for kind, count in (
            ("input", usage.get("input_tokens")),
            ("output", usage.get("output_tokens")),
            ("total", usage.get("total_tokens")),
        ):
            if count is None:
                continue  # 源字段缺省不镜像该条（registry 该轴 optional）
            data = dict(base)
            data["usage_kind"] = kind
            data["token_count"] = int(count)
            lm.create(type=_LM_TYPE, data=data, created_by=self._created_by)

    def _trip(self, where: str, e: Exception) -> None:
        self._broken = True
        logger.warning("[lingmemory_token_bridge] 旁路熔断(%s): %s", where, e)
