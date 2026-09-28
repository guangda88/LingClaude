"""灵元 P1: Slot / SlotHandle / SlotManager —— 统一 swap 协议（主干第二层，冻结）。

本模块是「极薄主干 + 分形插片」的运行时热更闭环。三层主干定义：
- 状态层：lingmemory 2T3A（create/transition/query）
- 生命周期层：本文件的 swap 协议（自指悖论 → 不可插片化，必须进主干）
- 结构层：coding_wiring manifest 装配入口

swap 语义（§四，全库唯一一套，插片不许自创热更协议）：
1. 先建后换：factory 失败旧实例原地不动（fail-soft）
2. 换手即生效：新 lease 解析到新实例
3. 在途排空：旧实例等租约归零再释放（drain）
4. 重建判据用 config_hash（解析后 dict 的 hash），不用 mtime
5. 槽内禁止构造期绑定他槽实例：持 SlotHandle，调用时解析
6. 子代理改持 SlotHandle 调用时解析 —— 根治 provider 值快照问题

C4 裁定：每次 swap 写 type=slot_swap record（lingmemory 不可用时降级为
进程内审计 list，best-effort 不阻断主链路）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

MAX_SLOTS = 8  # 槽数预算（守卫：超出即红，防"槽地狱"）


def config_digest(config: Any) -> str:
    """对解析后的配置对象取稳定 hash（sha256 of canonical json）。

    用解析后 dict 的 hash 而非 mtime —— 天然免疫"同长度改写漏检"。
    对象不可序列化时退化为 repr()。
    """
    try:
        canonical = json.dumps(config, sort_keys=True, default=str)
    except (TypeError, ValueError):
        canonical = repr(config)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass
class SwapRecord:
    """一次槽 swap 的审计记录（C4：热更历史成为可 query 的真相）。"""

    slot_name: str
    epoch: int
    old_digest: str | None
    new_digest: str
    reason: str
    drained_leases: int
    ts: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": "slot_swap",
            "slot": self.slot_name,
            "epoch": self.epoch,
            "old_digest": self.old_digest,
            "new_digest": self.new_digest,
            "reason": self.reason,
            "drained_leases": self.drained_leases,
            "ts": self.ts,
        }


class SlotLease:
    """单个在途租约。exit 时回调 Slot._release。支持 with 语句。"""

    def __init__(self, slot: "Slot", epoch: int) -> None:
        self._slot = slot
        self._epoch = epoch
        self._released = False

    @property
    def epoch(self) -> int:
        return self._epoch

    @property
    def instance(self) -> Any:
        return self._slot._instance_at(self._epoch)

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._slot._release(self._epoch)

    def __enter__(self) -> "SlotLease":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()

    def __del__(self) -> None:  # 兜底防泄漏（best-effort）
        try:
            self.release()
        except Exception:  # noqa: BLE001 — 解释器 shutdown 阶段不保证状态
            pass


class Slot:
    """一个重机械槽：持单个可热拔插实例 + epoch/lease 排空协议。

    - checkout()/checkout_callable() 加租约；lease.release()/with 减租约
    - swap() 先建后换，旧实例等租约归零再释放（drain）
    - instance/factory 只读暴露给 SlotManager
    """

    def __init__(
        self,
        name: str,
        initial: Any = None,
        factory: Callable[[], Any] | None = None,
        on_drain: Callable[[Any], None] | None = None,
    ) -> None:
        self.name = name
        self._factory = factory
        self._on_drain = on_drain
        self._lock = threading.RLock()
        self._cv = threading.Condition(self._lock)
        self._instance: Any = initial
        self._epoch: int = 0
        self._leases: dict[int, int] = {}  # epoch -> outstanding lease count
        self._drained: dict[int, Any] = {}  # draining 中的旧实例（等租约归零）

    @property
    def epoch(self) -> int:
        with self._lock:
            return self._epoch

    @property
    def instance(self) -> Any:
        with self._lock:
            return self._instance

    @property
    def factory(self) -> Callable[[], Any] | None:
        return self._factory

    def set_factory(self, factory: Callable[[], Any]) -> None:
        with self._lock:
            self._factory = factory

    def _instance_at(self, epoch: int) -> Any:
        """租约按 epoch 解析实例：swap 后旧 epoch lease 仍拿旧实例（正确排空）。"""
        with self._lock:
            if epoch == self._epoch:
                return self._instance
            return self._drained.get(epoch)

    def checkout(self) -> SlotLease:
        """取一个在途租约，lease.instance 为当前（或租约 epoch）实例。"""
        with self._cv:
            epoch = self._epoch
            self._leases[epoch] = self._leases.get(epoch, 0) + 1
            return SlotLease(self, epoch)

    def checkout_callable(self) -> Callable[[], Any]:
        """无状态调用点便捷口：返回零参 callable，解析到租约时刻的实例。"""
        lease = self.checkout()

        def _invoke(*args: Any, **kwargs: Any) -> Any:
            try:
                return lease.instance(*args, **kwargs)
            finally:
                lease.release()

        return _invoke

    def _release(self, epoch: int) -> None:
        with self._cv:
            count = self._leases.get(epoch, 0)
            if count > 1:
                self._leases[epoch] = count - 1
            else:
                self._leases.pop(epoch, None)
                # 该 epoch 已 draining 且租约归零 → 真正释放旧实例
                if epoch in self._drained:
                    old = self._drained.pop(epoch)
                    self._call_drain(old)
            self._cv.notify_all()

    def _call_drain(self, old: Any) -> None:
        if self._on_drain is not None and old is not None:
            try:
                self._on_drain(old)
            except Exception:  # noqa: BLE001 — drain 回调故障不阻断 swap
                logger.warning("slot %s on_drain 回调失败（best-effort）", self.name, exc_info=True)

    def outstanding(self, epoch: int | None = None) -> int:
        with self._lock:
            if epoch is not None:
                return self._leases.get(epoch, 0)
            return sum(self._leases.values())

    def swap(self, new: Any, *, reason: str = "manual") -> SwapRecord:
        """先建后换：新实例就位后换手，旧实例等租约归零 drain。

        调用方负责先调 factory 建好 new（fail-soft 在上层）；本方法只做换手+排空。
        """
        with self._cv:
            old = self._instance
            old_epoch = self._epoch
            old_digest = config_digest(old) if old is not None else None
            self._instance = new
            self._epoch = old_epoch + 1
            new_epoch = self._epoch
            # 旧 epoch 实例移到 drained 区，等对应租约归零
            if old is not None and self._leases.get(old_epoch, 0) > 0:
                self._drained[old_epoch] = old
            elif old is not None:
                self._call_drain(old)
            record = SwapRecord(
                slot_name=self.name,
                epoch=new_epoch,
                old_digest=old_digest,
                new_digest=config_digest(new) if new is not None else None,
                reason=reason,
                drained_leases=self._leases.get(old_epoch, 0),
            )
            self._cv.notify_all()
            return record

    def drain(self, timeout: float | None = None) -> bool:
        """等待当前 epoch 租约归零（swap 后旧实例释放用）。"""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cv:
            while self.outstanding() > 0:
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self._cv.wait(remaining)
                else:
                    self._cv.wait()
            return True


class SlotHandle:
    """插片侧持柄：构造期不绑定实例，调用时经 SlotManager 解析（防快照泄漏）。

    三种解析形态：
    - .instance / .callable() → 无在途追踪（轻调用点）
    - .checkout() / .with_lease() → 有 lease 排空保护（长操作/在途请求）
    """

    def __init__(self, manager: "SlotManager", name: str) -> None:
        self._manager = manager
        self._name = name

    @property
    def slot_name(self) -> str:
        return self._name

    def instance(self) -> Any:
        return self._manager.get(self._name)

    def checkout(self) -> SlotLease:
        return self._manager.checkout(self._name)

    def with_lease(self) -> SlotLease:
        return self.checkout()

    def resolver(self) -> Callable[..., Any]:
        """每次调用重新解析实例的零参入口（便捷轻调用点）。"""
        slot = self._manager.slot(self._name)
        return slot.checkout_callable()

    def __getattr__(self, item: str) -> Any:
        # 透传到当前实例属性（便捷：handle.complete(...) → instance.complete(...)）。
        # 仅实例属性未命中才走到这里；方法名（如 resolver）优先于 __getattr__。
        return getattr(self.instance(), item)


class TransparentSlotHandle(SlotHandle):
    """透明句柄：插片侧以 method 形状直接消费，内部惰性解析到当前实例。

    与 SlotHandle 的差别：SlotHandle 是「句柄对象」，消费方须显式 .instance()；
    TransparentSlotHandle 把 method 调用直接代理到当前实例 —— 消费方写
    `runtime._todo_store.list()` 时拿到的是句柄，但调用经 __getattr__ 解析到
    当前代际实例。形状与裸实例一致（duck-typing），现有属性访问代码零改动，
    同时获得 swap 后解析新实例的能力（防快照泄漏的 P2 轻量形态）。

    注意：仅 method 访问可透明代理；非 method 属性（数据字段）会被快照
    （构建句柄那一刻的值）。P2 两处（todo_store/session_runtime）消费方
    全是 method 调用，安全。
    """

    def __init__(self, manager: "SlotManager", name: str) -> None:
        super().__init__(manager, name)


class SlotManager:
    """唯一槽注册表（防双注册表漂移）。集中管理 Slot，写 swap record。"""

    def __init__(self, *, max_slots: int = MAX_SLOTS) -> None:
        self._lock = threading.RLock()
        self._slots: dict[str, Slot] = {}
        self._handles: dict[str, SlotHandle] = {}
        self._digests: dict[str, str] = {}
        self._max = max_slots
        self._swap_log: list[SwapRecord] = []  # 2T3A 不可用时的进程内降级台账

    def register(
        self,
        name: str,
        initial: Any = None,
        factory: Callable[[], Any] | None = None,
        on_drain: Callable[[Any], None] | None = None,
        *,
        transparent: bool = False,
    ) -> SlotHandle:
        with self._lock:
            if name in self._slots:
                raise ValueError(f"slot '{name}' 已注册（双注册表漂移防线）")
            if len(self._slots) >= self._max:
                raise ValueError(
                    f"槽数预算超限：{len(self._slots) + 1} > {self._max}（防槽地狱，守卫红）"
                )
            slot = Slot(name, initial=initial, factory=factory, on_drain=on_drain)
            self._slots[name] = slot
            # 初始 digest：让首次 needs_rebuild(config) 有可比基线（None→有值必触发）
            self._digests[name] = config_digest(initial) if initial is not None else None
            handle = TransparentSlotHandle(self, name) if transparent else SlotHandle(self, name)
            self._handles[name] = handle
            return handle

    def get(self, name: str) -> Any:
        with self._lock:
            slot = self._slots.get(name)
        if slot is None:
            raise KeyError(f"未知槽 '{name}'")
        return slot.instance

    def slot(self, name: str) -> Slot:
        with self._lock:
            slot = self._slots.get(name)
        if slot is None:
            raise KeyError(f"未知槽 '{name}'")
        return slot

    def handle(self, name: str) -> SlotHandle:
        with self._lock:
            handle = self._handles.get(name)
        if handle is None:
            raise KeyError(f"未知槽 '{name}'")
        return handle

    def checkout(self, name: str) -> SlotLease:
        return self.slot(name).checkout()

    def names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._slots.keys())

    # ---- 热更通路 ----
    def needs_rebuild(self, name: str, config: Any) -> bool:
        """config_hash 判据：与上次 rebuild 的 digest 不同才重建（§四.4）。"""
        return self._digests.get(name) != config_digest(config)

    def rebuild(self, name: str, factory: Callable[[], Any], *, reason: str = "config") -> SwapRecord:
        """先建后换（fail-soft 在调用方 factory 内保证：异常则旧实例不动）。"""
        slot = self.slot(name)
        new = factory()
        record = slot.swap(new, reason=reason)
        self._digests[name] = record.new_digest
        self._record_swap(record)
        return record

    def _record_swap(self, record: SwapRecord) -> None:
        """C4：写 type=slot_swap record。lingmemory 可用写 2T3A，否则进程内降级。"""
        self._swap_log.append(record)
        try:
            from lingmemory import LingMemory  # 延迟 import，best-effort

            mem = LingMemory()
            # create(type, data)：type 须已在 type_registry.yaml 注册（slot_swap，P6）
            mem.create("slot_swap", record.as_dict())
        except Exception:  # noqa: BLE001 — 2T3A 缺席不阻断热更主链路（L2 降级）
            logger.debug("slot_swap record 降级为进程内台账（lingmemory 缺席）")

    def swap_history(self, slot: str | None = None) -> list[SwapRecord]:
        with self._lock:
            records = list(self._swap_log)
        if slot is not None:
            records = [r for r in records if r.slot_name == slot]
        return records

    def query_swap_history(
        self, slot: str | None = None, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        """P6/C4：热更台账持久 query —— 2T3A 优先，缺席降级进程内台账。

        返回统一形态：list[dict]（SwapRecord.as_dict() 兼容，均带 type=slot_swap）。
        跨进程/重启后 2T3A 仍可查；进程内降级仅覆盖本进程 swap 历史。
        """
        try:
            from lingmemory import LingMemory

            data_filter = {"slot": slot} if slot is not None else None
            result = LingMemory().query(
                type="slot_swap", data_filter=data_filter, limit=limit
            )
            items = result.get("items", [])
            out: list[dict[str, Any]] = []
            for item in items:
                d = item.get("data", item) if isinstance(item, dict) else {}
                if isinstance(d, dict) and d:
                    out.append(d)
            if out:
                return out
        except Exception:  # noqa: BLE001 — lingmemory 缺席/异常降级进程内
            logger.debug("slot_swap query 降级为进程内台账")
        return [r.as_dict() for r in self.swap_history(slot)]
