# lingclaude/core/plugin_lifecycle.py
"""插片事件驱动生命周期管理（方案C v4 P0#2）。

对齐 Cordis fiber 语义（vendored/cordis/src/fiber.ts 实测）：
- 六态状态机：PENDING → LOADING → ACTIVE → UNLOADING → INACTIVE（FAILED 可自任意态进入）
- epoch 依赖指纹：依赖缝实现集合的指纹串；实现被替换（register 覆盖）即指纹变化 → 重载
- disposer 逆序：卸载时按注册逆序执行清理，任一 disposer 异常不阻断其余
- 依赖传播：缝 register/unregister 事件 → 受影响 fiber 自动 refresh（无轮询）
- 蓝绿热替换（对齐 Pi chord）：候选实例先构建+验证，成功才原子切换；失败保留旧代

与外部边界的关系（架构纪律）：本模块只管 lc 内部缝图；GLM/zai 等 HTTP 端点
探活走端点探活组件的 TTL 机制，两者边界互不侵犯。
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from enum import Enum
from typing import Any, Callable, Iterable, Optional

from lingclaude.core.invariants import (
    PLUGIN_LIFECYCLE_TABLE,
    StateInvariantGuard,
    TransitionTable,
)
from lingclaude.core.seam import SeamRegistry, SeamType

logger = logging.getLogger(__name__)


class LifecycleState(str, Enum):
    """fiber 六态（对齐 Cordis fiber.ts:574-580 _getState + 转换语义）。"""

    PENDING = "pending"        # 依赖未就绪，等待中（合法常态，可持 effects）
    LOADING = "loading"        # 激活执行中
    ACTIVE = "active"          # 依赖齐备、实例就绪
    UNLOADING = "unloading"    # 卸载执行中
    INACTIVE = "inactive"      # 依赖缺失/被显式停用
    FAILED = "failed"          # 激活/卸载抛异常（fail-loud 记录，不静默）


def _epoch_of(impl: Any) -> str:
    """实现的 epoch 标识：优先实例级 _seam_epoch（实现方可显式自增），
    否则用 id()（进程内对象身份——register 覆盖产生新对象即新 id）。"""
    marker = getattr(impl, "_seam_epoch", None)
    return str(marker) if marker is not None else f"id:{id(impl)}"


def _domain_of(fiber_name: str) -> str:
    """fiber 的缝域归属：'gov/x' → 'gov'；无前缀 → 'app'。
    与 seam.DOMAIN_NAMESPACES 的 gov/agent/cap/hw 前缀约定对齐。"""
    return fiber_name.split("/", 1)[0] if "/" in fiber_name else "app"


class BreakerLayer(str, Enum):
    """熔断三层：per-fiber（隔离单点）→ domain（缝域，防单域风暴）→ global（兜底）。"""

    FIBER = "fiber"
    DOMAIN = "domain"
    GLOBAL = "global"


class CircuitBreaker:
    """插片风暴双层→三层熔断闸（外部评审合议定案，synthesis-20260925 #2）。

    确定性判据：计数器 >= threshold 且时间窗口 < window_seconds，拒绝主观判断。
    - 状态机：CLOSED →（窗口内失败达阈值）→ OPEN →（冷却期满）→ HALF_OPEN
      →（单探测成功）→ CLOSED；探测失败 → 回 OPEN（冷却重新计时）。
    - 三层独立计数、独立熔断；低层不能豁免高层（域/全局熔断时单 fiber 探测无效）。
    - 白名单 fiber 绕过闸（风暴期保留最小可服务子集）。
    - 熔断事件经 event_sink 外发（后续对接 ly_state_events 落账：重启不丢、绕过风暴）；
      sink 异常绝不阻断闸本身（best-effort，与 hooks 同纪律）。
    - 拒绝（denial）不是失败，不计数——只有真实激活异常才计。
    """

    def __init__(
        self,
        fiber_threshold: int = 3,
        domain_threshold: int = 4,
        global_threshold: int = 6,
        window_seconds: float = 60.0,
        cooldown_seconds: float = 30.0,
        whitelist: Optional[set] = None,
        event_sink: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self._fiber_threshold = fiber_threshold
        self._domain_threshold = domain_threshold
        self._global_threshold = global_threshold
        self._window = window_seconds
        self._cooldown = cooldown_seconds
        self._whitelist = set(whitelist) if whitelist else set()
        self._sink = event_sink
        self._lock = threading.Lock()
        # 滑动窗口：layer/target → 失败时间戳队列
        self._failures: dict[tuple, deque] = {
            (BreakerLayer.FIBER, None): deque(),
        }
        self._opened_at: dict[tuple, float] = {}   # 触发时刻（OPEN 起点即冷却计时起点）
        # half-open 探测在途：fiber_name → (探测目标层, 目标)。探测结束（成功/失败）即清除。
        self._probing: dict[str, tuple] = {}

    # ---- 窗口维护 ----

    def _prune(self, key: tuple, now: float) -> int:
        """清理窗口外时间戳，返回窗口内失败数。"""
        dq = self._failures.setdefault(key, deque())
        horizon = now - self._window
        while dq and dq[0] < horizon:
            dq.popleft()
        return len(dq)

    def _emit(self, event: str, layer: BreakerLayer, target: Optional[str],
              **detail: Any) -> None:
        """事件外发（best-effort：sink 异常只记日志，绝不阻断闸）。"""
        rec = {"event": event, "layer": layer.value, "target": target,
               "ts": time.time()}
        rec.update(detail)
        logger.warning("breaker: %s[%s]%s %s", event, layer.value,
                       f":{target}" if target else "", detail or "")
        if self._sink is not None:
            try:
                self._sink(rec)
            except Exception:  # noqa: BLE001
                logger.exception("breaker: event_sink 异常（忽略）")
    # ---- 记账 ----

    def record_success(self, fiber_name: str) -> None:
        """激活成功：清该 fiber 计数。

        half-open 语义（闸的恢复只认探测者的结果）：
        - 探测者自己成功 → 关闭它探测的层闸；
        - 非探测者成功 → 只清窗口计数，不动 OPEN 状态（别的 fiber 触发的闸
          不能被无关 fiber 的成功关闭——half-open 单探测纪律）。
        """
        with self._lock:
            domain = _domain_of(fiber_name)
            probe = self._probing.pop(fiber_name, None)
            if probe is not None:
                probe_layer, probe_target = probe
                dq = self._failures.get((probe_layer, probe_target))
                if dq:
                    dq.clear()
                if (probe_layer, probe_target) in self._opened_at:
                    del self._opened_at[(probe_layer, probe_target)]
                    self._emit("breaker_close", probe_layer, probe_target,
                               probed_by=fiber_name)
            else:
                # 无探测在途时（如 hot_swap 修复通道）：激活成功 = 该 fiber 工厂
                # 可用的直接证据 → 允许关闭它自己的 fiber 闸；域/全局聚合闸仍守
                # 探测纪律（单个幸运 fiber 的成功不能关闭聚合闸掩盖风暴）。
                fiber_key = (BreakerLayer.FIBER, fiber_name)
                if fiber_key in self._opened_at:
                    del self._opened_at[fiber_key]
                    self._emit("breaker_close", BreakerLayer.FIBER, fiber_name,
                               closed_by=fiber_name)
            # 非 OPEN 层的计数清理（正常成功即归零）
            for layer, target in ((BreakerLayer.FIBER, fiber_name),
                                  (BreakerLayer.DOMAIN, domain),
                                  (BreakerLayer.GLOBAL, None)):
                dq = self._failures.get((layer, target))
                if dq:
                    dq.clear()

    def record_failure(self, fiber_name: str, error: Optional[str] = None) -> None:
        """激活失败：三层滑动窗口各记一次；达阈值即熔断（OPEN，冷却起算）。

        half-open 探测失败 → 重开所探测层闸（OPEN 重新计时）并释放探测在途标记；
        未过阈值的普通失败只记窗口，不触发熔断。
        """
        with self._lock:
            now = time.time()
            domain = _domain_of(fiber_name)
            probe = self._probing.pop(fiber_name, None)
            for layer, target, threshold in (
                    (BreakerLayer.FIBER, fiber_name, self._fiber_threshold),
                    (BreakerLayer.DOMAIN, domain, self._domain_threshold),
                    (BreakerLayer.GLOBAL, None, self._global_threshold)):
                key = (layer, target)
                dq = self._failures.setdefault(key, deque())
                dq.append(now)
                self._prune(key, now)
                if probe is not None and (layer, target) == probe:
                    # 探测失败：重开闸（冷却从现在重新起算）
                    self._opened_at[key] = now
                    self._emit("probe_failed", layer, target,
                               fiber=fiber_name, error=error)
                    continue
                if len(dq) >= threshold and key not in self._opened_at:
                    self._opened_at[key] = now
                    self._emit("breaker_open", layer, target,
                               failures=len(dq), threshold=threshold,
                               fiber=fiber_name)

    # ---- 准入判定 ----

    def allow(self, fiber_name: str) -> tuple[bool, Optional[dict]]:
        """返回 (是否允许激活, 拒绝详情)。拒绝详情 None=允许。

        half-open 契约：本层 OPEN 且冷却期满 → 允许**单探测**（探测在途标记）；
        高层（域/全局）OPEN 时低层探测无效——低层不能豁免高层。
        白名单 fiber 绕过闸。
        """
        if fiber_name in self._whitelist:
            return True, None
        with self._lock:
            now = time.time()
            domain = _domain_of(fiber_name)
            # 自上而下检查：global → domain → fiber（先硬后软）
            for layer, target in ((BreakerLayer.GLOBAL, None),
                                  (BreakerLayer.DOMAIN, domain),
                                  (BreakerLayer.FIBER, fiber_name)):
                if (layer, target) not in self._opened_at:
                    continue
                opened_at = self._opened_at[(layer, target)]
                if now - opened_at < self._cooldown:
                    return False, {"layer": layer.value, "target": target,
                                   "state": "open",
                                   "cooldown_remaining": round(
                                       self._cooldown - (now - opened_at), 3)}
                # 冷却期满 → half-open：单探测（每个 OPEN 闸同时只允许一个探测者，
                # 无论探测者是否同一 fiber——防并发探测风暴）
                probe_holder = next(
                    (f for f, t in self._probing.items() if t == (layer, target)),
                    None)
                if probe_holder is not None:
                    return False, {"layer": layer.value, "target": target,
                                   "state": "half_open",
                                   "reason": "probe_in_flight",
                                   "probe_holder": probe_holder}
                self._probing[fiber_name] = (layer, target)
                self._emit("probe_allowed", layer, target, fiber=fiber_name)
                return True, None  # 探测放行（成败由 record_success/failure 定）
            return True, None

    def reset(self, fiber_name: Optional[str] = None) -> None:
        """运维手动复位：指定 fiber 清该 fiber 及其域计数；None=全清。"""
        with self._lock:
            if fiber_name is None:
                self._failures.clear()
                self._opened_at.clear()
                self._probing.clear()
                self._failures[(BreakerLayer.FIBER, None)] = deque()
                return
            domain = _domain_of(fiber_name)
            for key in ((BreakerLayer.FIBER, fiber_name),
                        (BreakerLayer.DOMAIN, domain),
                        (BreakerLayer.GLOBAL, None)):
                dq = self._failures.get(key)
                if dq:
                    dq.clear()
                self._opened_at.pop(key, None)
            self._probing.pop(fiber_name, None)

    def snapshot(self) -> dict[str, Any]:
        """只读投影（status/观测面板数据源）。"""
        with self._lock:
            now = time.time()
            fibers: dict[str, dict] = {}
            domains: dict[str, dict] = {}
            global_info = {"failures": self._prune((BreakerLayer.GLOBAL, None), now),
                           "open": False, "cooldown_remaining": 0.0}
            if (BreakerLayer.GLOBAL, None) in self._opened_at:
                opened_at = self._opened_at[(BreakerLayer.GLOBAL, None)]
                global_info["open"] = True
                global_info["cooldown_remaining"] = round(
                    max(0.0, self._cooldown - (now - opened_at)), 3)
            for (layer, target), dq in self._failures.items():
                if layer is BreakerLayer.FIBER and target is not None:
                    open_at = self._opened_at.get((layer, target))
                    fibers[target] = {
                        "failures": self._prune((layer, target), now),
                        "open": open_at is not None,
                        "cooldown_remaining": round(
                            max(0.0, self._cooldown - (now - open_at)), 3)
                            if open_at is not None else 0.0,
                    }
                elif layer is BreakerLayer.DOMAIN:
                    open_at = self._opened_at.get((layer, target))
                    domains[target] = {
                        "failures": self._prune((layer, target), now),
                        "open": open_at is not None,
                        "cooldown_remaining": round(
                            max(0.0, self._cooldown - (now - open_at)), 3)
                            if open_at is not None else 0.0,
                    }
            return {"global": global_info, "domains": domains,
                    "fibers": fibers,
                    "whitelist": sorted(self._whitelist)}


class PluginFiber:
    """单插件 fiber：状态 + 依赖指纹 + disposer 栈。

    不可跨线程并发激活（LifecycleManager 全局串行化 refresh）。
    """

    __slots__ = ("name", "factory", "inject", "state", "epoch", "instance",
                 "_disposers", "error", "activated_at")

    def __init__(
        self,
        name: str,
        factory: Callable[[], Any],
        inject: Iterable[tuple[SeamType, str]] = (),
    ) -> None:
        self.name = str(name)
        self.factory = factory                  # 零参可调用：返回插件实例（或 (instance, disposer) 二元组）
        self.inject: list[tuple[SeamType, str]] = [
            (SeamType(t), str(n)) for t, n in inject
        ]
        self.state = LifecycleState.PENDING
        self.epoch: Optional[str] = None        # 依赖指纹（'端点/模型标识:id:140...' 形式）
        self.instance: Any = None
        self._disposers: list[Callable[[], None]] = []
        self.error: Optional[str] = None
        self.activated_at: Optional[float] = None

    # ---- 指纹 ----

    def compute_epoch(self) -> Optional[str]:
        """当前依赖指纹；任一依赖缺失返回 None（= INACTIVE 判据）。"""
        parts = []
        for seam_type, name in self.inject:
            impl = SeamRegistry.get_optional(seam_type, name)
            if impl is None:
                return None
            parts.append(f"{seam_type.value}/{name}:{_epoch_of(impl)}")
        return "|".join(parts) if self.inject else ""

    def add_disposer(self, disposer: Callable[[], None]) -> None:
        self._disposers.append(disposer)

    def run_disposers_reversed(self) -> list[str]:
        """逆序执行 disposer（Cordis fiber.ts:68-100 语义）。返回失败 disposer 名单。"""
        failed = []
        while self._disposers:
            disposer = self._disposers.pop()
            try:
                disposer()
            except Exception as exc:  # noqa: BLE001 清理失败不阻断其余 disposer
                failed.append(getattr(disposer, "__name__", repr(disposer)))
                logger.exception("fiber[%s]: disposer failed: %s", self.name, exc)
        return failed


class LifecycleManager:
    """进程内插件生命周期管理器。

    用法（与 plugin_loader 协作）::

        lcm = LifecycleManager()
        lcm.attach("cap_infer", factory=build_cap_infer,
                   inject=[(SeamType.PROVIDER, "glm")])
        lcm.refresh_all()          # 启动期一次
        # ……缝 register/unregister 时 SeamRegistry 自动回调 lcm.on_seam_change
        lcm.detach("cap_infer")    # 显式卸载（dispose 逆序）

    线程模型：_lock 串行化全部状态转换；seam 事件回调在注册锁外执行
    （seam.py `_notify_change` 设计），本模块锁内不再回调 SeamRegistry
    写路径——无锁环。
    """

    def __init__(self, verify_timeout: float = 5.0, hooks: Any = None,
                 breaker: Optional["CircuitBreaker"] = None) -> None:
        self._fibers: dict[str, PluginFiber] = {}
        self._lock = threading.RLock()
        self._verify_timeout = verify_timeout
        self._hooks = hooks  # 可选：HookManager（触发 PRE/POST_HOT_SWAP）；None=静默
        self._subscribed = False
        # 插片风暴熔断闸（外部评审合议 #2）：默认构造即启用；测试可注入紧阈值实例
        self.breaker = breaker if breaker is not None else CircuitBreaker()
        # 状态不变量守卫（建闸期③接线，2026-09-25）：observe 模式挂每 fiber 的
        # 状态赋值点——三值语义（legal/illegal/unclassified），observe 只记录
        # 告警不阻断；strict 化（UndefinedTransitionError 拒绝转换）待观察期
        # 补表后另行裁决。fail-open：守卫自身故障不阻断生命周期主路径。
        self._invariant_guard = StateInvariantGuard(
            table=TransitionTable(edges=PLUGIN_LIFECYCLE_TABLE, name="plugin_lifecycle"),
            mode="observe",
        )

    def _fire_hook(self, hook_type_value: str, fiber_name: str,
                   **metadata: Any) -> None:
        """触发生命周期钩子（防御式：无 hooks/无该类型钩子/触发异常均静默）。

        hot_swap 是显式管控动作，PRE/POST_HOT_SWAP 给管控观测面板留观测点；
        钩子失败不阻断切换主路径（fail-open，与 seam 订阅者语义一致）。
        """
        if self._hooks is None:
            return
        try:
            from lingclaude.core.hooks import HookContext, HookType
            ctx = HookContext(
                hook_type=HookType(hook_type_value),
                session_id=fiber_name,
                metadata=metadata,
            )
            self._hooks.trigger(ctx)
        except Exception:  # noqa: BLE001
            logger.exception("lifecycle: %s hook failed (fiber=%s)",
                             hook_type_value, fiber_name)

    # ---- 订阅 ----

    def ensure_subscribed(self) -> None:
        """订阅缝变更（幂等）。依赖 seam.py 的 subscribe_change。"""
        with self._lock:
            if self._subscribed:
                return
            try:
                SeamRegistry.subscribe_change(self.on_seam_change)
                self._subscribed = True
            except AttributeError:
                logger.warning("SeamRegistry 无 subscribe_change（seam.py 未升级），"
                               "退化为手动 refresh_all 模式")

    # ---- attach / detach ----

    def attach(self, name: str, factory: Callable[[], Any],
               inject: Iterable[tuple[SeamType, str]] = ()) -> PluginFiber:
        """登记插件 fiber（不激活；激活靠 refresh）。同名 attach 覆盖旧 fiber（先卸旧）。"""
        name = str(name).strip()
        if not name:
            raise ValueError("fiber name must not be empty")
        with self._lock:
            if name in self._fibers:
                self._unload_fiber(self._fibers[name])
                del self._fibers[name]
            fiber = PluginFiber(name, factory, inject)
            self._fibers[name] = fiber
            self._observe_state(fiber, LifecycleState.PENDING, frm=None)
            logger.debug("lifecycle: attach %s (inject=%s)", name, fiber.inject)
            return fiber

    def detach(self, name: str) -> bool:
        """显式卸载并移除 fiber。缺名 no-op 返回 False。"""
        with self._lock:
            fiber = self._fibers.pop(name, None)
        if fiber is None:
            return False
        self._unload_fiber(fiber)
        return True

    _UNSET_FRM = object()

    def _observe_state(self, fiber: "PluginFiber", to: "LifecycleState",
                       frm: Any = _UNSET_FRM) -> None:
        """状态赋值点观察（建闸期③接线，2026-09-25）：每次赋值前向不变量
        守卫上报一次转换。观察粒度 = 赋值粒度（与 tests/test_invariants
        property 模型一致）。frm 显式传 None 表示出生转换（None→PENDING），
        缺省读 fiber.state 当前值。fail-open：守卫异常只告警，不阻断主路径。
        """
        try:
            self._invariant_guard.observe_transition(
                fiber.name,
                fiber.state.name if frm is self._UNSET_FRM else frm,
                to.name)
        except Exception:  # noqa: BLE001
            logger.warning("lifecycle: invariant guard 失联（fiber=%s）",
                           fiber.name, exc_info=True)

    # ---- 状态机核心 ----

    def _activate(self, fiber: PluginFiber) -> None:
        """PENDING/INACTIVE → LOADING → ACTIVE。factory 可返回实例或 (实例, disposer)。

        状态赋值点均先向不变量守卫上报（观察粒度=赋值粒度，建闸期③接线）。
        """
        self._observe_state(fiber, LifecycleState.LOADING)
        fiber.state = LifecycleState.LOADING
        fiber.error = None
        try:
            result = fiber.factory()
            if isinstance(result, tuple) and len(result) == 2:
                instance, disposer = result
                if disposer is not None:
                    fiber.add_disposer(disposer)
            else:
                instance = result
            fiber.instance = instance
            fiber.activated_at = time.time()
            self._observe_state(fiber, LifecycleState.ACTIVE)
            fiber.state = LifecycleState.ACTIVE
            fiber.epoch = fiber.compute_epoch()
            self.breaker.record_success(fiber.name)
            logger.info("lifecycle: %s ACTIVE (epoch=%s)", fiber.name, fiber.epoch)
        except Exception as exc:  # noqa: BLE001 激活失败 fail-loud 记录
            # 观察语义：try 窗口内赋 ACTIVE 后的 compute_epoch/record_success
            # 抛异常仍会走到此处——此时 frm=ACTIVE（表内合法边 ACTIVE→FAILED）；
            # 其余进入路径 frm=LOADING。三值守卫按实际 frm 判定，无需二分。
            self._observe_state(fiber, LifecycleState.FAILED)
            fiber.state = LifecycleState.FAILED
            fiber.error = f"{type(exc).__name__}: {exc}"
            fiber.instance = None
            self.breaker.record_failure(fiber.name, error=fiber.error)
            logger.exception("lifecycle: %s FAILED on activate", fiber.name)

    def _unload_fiber(self, fiber: PluginFiber) -> None:
        """任意态 → UNLOADING → INACTIVE。disposer 逆序；instance 释放。"""
        if fiber.state is LifecycleState.INACTIVE and fiber.instance is None:
            return
        prev = fiber.state
        self._observe_state(fiber, LifecycleState.UNLOADING)
        fiber.state = LifecycleState.UNLOADING
        try:
            # 声明式 disposer：实例自带 dispose/close/stop 时自动回收（鸭子类型）
            instance = fiber.instance
            if instance is not None:
                for meth in ("dispose", "close", "stop"):
                    fn = getattr(instance, meth, None)
                    if callable(fn):
                        try:
                            fn()
                        except Exception:  # noqa: BLE001
                            logger.exception("lifecycle: %s.%s() failed", fiber.name, meth)
                        break  # 只调第一个存在的
        finally:
            failed = fiber.run_disposers_reversed()
            fiber.instance = None
            fiber.epoch = None
            self._observe_state(fiber, LifecycleState.INACTIVE)
            fiber.state = LifecycleState.INACTIVE
            logger.info("lifecycle: %s UNLOADING→INACTIVE (from=%s failed_disposers=%s)",
                        fiber.name, prev.value, failed)

    # ---- refresh（事件驱动核心，对齐 Cordis _refresh/_setEpoch）----

    def refresh(self, name: str) -> LifecycleState:
        """按依赖指纹刷新单 fiber：无变化 no-op；缺依赖→INACTIVE；齐备→(re)activate。"""
        with self._lock:
            fiber = self._fibers.get(name)
            if fiber is None:
                raise KeyError(f"LifecycleManager: fiber {name!r} 未 attach")
            new_epoch = fiber.compute_epoch()
            if new_epoch is None:
                # 依赖缺失：若曾 ACTIVE 则卸载（依赖消失→自动 dispose）
                if fiber.state is LifecycleState.ACTIVE:
                    logger.warning("lifecycle: %s 依赖缺失，自动卸载", fiber.name)
                    self._unload_fiber(fiber)
                elif fiber.state is not LifecycleState.FAILED:
                    self._observe_state(fiber, LifecycleState.INACTIVE)
                    fiber.state = LifecycleState.INACTIVE
                return fiber.state
            # 依赖齐备：指纹未变且已 ACTIVE → no-op（epoch 语义：没变就什么都不做）
            if fiber.state is LifecycleState.ACTIVE and fiber.epoch == new_epoch:
                return fiber.state
            # 熔断准入（评审合议 #2）：闸关着就不动在服实例——卸载旧代之前先问闸，
            # 拒绝时旧代续服（蓝绿纪律）或保持 INACTIVE（等闸开后再重试）。
            allowed, denial = self.breaker.allow(fiber.name)
            if not allowed:
                if fiber.state is LifecycleState.ACTIVE:
                    logger.warning("lifecycle: %s 熔断拒绝，旧代续服（denial=%s）",
                                   fiber.name, denial)
                else:
                    self._observe_state(fiber, LifecycleState.INACTIVE)
                    fiber.state = LifecycleState.INACTIVE
                    fiber.error = f"circuit_open: {denial}"
                return fiber.state
            if fiber.instance is not None or fiber.state is LifecycleState.ACTIVE:
                self._unload_fiber(fiber)      # 实现被替换 → 先卸旧（自动 reload 前半）
            self._activate(fiber)              # → 依赖回归/替换 → 重载
            return fiber.state

    def refresh_all(self) -> dict[str, LifecycleState]:
        """刷新全部 fiber（启动期 / 手动兜底）。返回 名→态 投影。"""
        with self._lock:
            return {n: self.refresh(n) for n in list(self._fibers)}

    def on_seam_change(self, action: str, seam_type: SeamType, name: str) -> None:
        """SeamRegistry 变更回调（seam.py `_notify_change` 直通）。

        只 refresh 依赖该缝的 fiber——事件驱动，无轮询。
        register 与 unregister 都要刷：前者=依赖回归/替换，后者=依赖消失。
        """
        affected = []
        with self._lock:
            for fiber in self._fibers.values():
                if (seam_type, name) in fiber.inject:
                    affected.append(fiber.name)
        for fiber_name in affected:
            try:
                self.refresh(fiber_name)
            except Exception:  # noqa: BLE001
                logger.exception("lifecycle: refresh(%s) on %s/%s %s failed",
                                 fiber_name, seam_type.value, name, action)

    # ---- 蓝绿热替换（对齐 Pi chord：候选先验证，成功才切换，失败保留旧代）----

    def hot_swap(self, name: str, new_factory: Callable[[], Any]) -> bool:
        """蓝绿替换插件实现。

        流程：构建候选（不动旧代）→ 验证（verify() 钩子或鸭子类型可调用检查）
        → 原子换 factory → 强制重载 → 成功 True；
        任一步失败 → dispose 候选、旧代继续服务、返回 False（零不可用窗口）。

        语义注记：
        - 切换是**显式动作**，不走 refresh()——refresh 的 epoch 守卫在依赖
          指纹未变时会 no-op，会吞掉 factory 替换；此处直接卸旧+激活新。
        - factory 被再次调用一次（验证期构建的候选仅用于验证，激活期重新
          构建正式实例）——有副作用工厂应自行幂等。
        - 激活期异常（factory 二次调用抛错）→ 回滚旧代（旧代复活）。
        """
        with self._lock:
            fiber = self._fibers.get(name)
            if fiber is None:
                raise KeyError(f"LifecycleManager: fiber {name!r} 未 attach")
            old_factory = fiber.factory
        # 1) 构建候选（锁外——构建可耗时）
        try:
            result = new_factory()
        except Exception as exc:  # noqa: BLE001
            logger.error("hot_swap[%s]: 候选构建失败，保留旧代: %s", name, exc)
            return False
        if isinstance(result, tuple) and len(result) == 2:
            candidate, candidate_disposer = result
        else:
            candidate, candidate_disposer = result, None
        # 2) 验证候选：显式 verify() 钩子优先，缺省做存在性检查
        self._fire_hook("pre_hot_swap", name,
                        old_factory=getattr(old_factory, "__name__", str(old_factory)),
                        new_factory=getattr(new_factory, "__name__", str(new_factory)))
        try:
            verify = getattr(candidate, "verify", None)
            if callable(verify):
                verify()
            elif candidate is None:
                raise ValueError("候选实例为 None")
        except Exception as exc:  # noqa: BLE001
            logger.error("hot_swap[%s]: 候选验证失败，保留旧代: %s", name, exc)
            if candidate_disposer is not None:
                try:
                    candidate_disposer()
                except Exception:  # noqa: BLE001
                    logger.exception("hot_swap[%s]: 候选 disposer 清理失败", name)
            return False
        # 3) 强制重载（绕过 refresh 的 epoch no-op 守卫——显式切换不能被守卫吞掉）
        with self._lock:
            fiber.factory = new_factory
            self._unload_fiber(fiber)
            self._activate(fiber)
        if fiber.state is LifecycleState.ACTIVE:
            self._fire_hook("post_hot_swap", name, result="success")
            logger.info("hot_swap[%s]: 蓝绿切换完成", name)
            return True
        # 4) 新代未达 ACTIVE（激活期抛错→FAILED）→ 回滚旧代（蓝绿语义：失败才退役新代）
        logger.error("hot_swap[%s]: 切换后 state=%s，回滚旧代", name, fiber.state.value)
        with self._lock:
            fiber.factory = old_factory
            self._unload_fiber(fiber)
            self._activate(fiber)
        self._fire_hook("post_hot_swap", name, result="rollback",
                        state=fiber.state.value)
        logger.warning("hot_swap[%s]: 回滚完成 state=%s", name, fiber.state.value)
        return False

    # ---- 只读投影（供 lc_plugins_inspect / 管控观测面板）----

    def status(self) -> dict[str, dict[str, Any]]:
        """全 fiber 状态投影（只读，inspect 数据源）。"""
        with self._lock:
            out = {}
            for n, f in self._fibers.items():
                out[n] = {
                    "state": f.state.value,
                    "epoch": f.epoch,
                    "inject": [[t.value, dep] for t, dep in f.inject],
                    "has_instance": f.instance is not None,
                    "error": f.error,
                    "pending_disposers": len(f._disposers),
                }
            out["_breaker"] = self.breaker.snapshot()
            return out

    def get(self, name: str) -> Any:
        """取活跃实例；未 ACTIVE 抛 KeyError（fail fast，消费方不拿半成品）。"""
        with self._lock:
            fiber = self._fibers.get(name)
        if fiber is None or fiber.state is not LifecycleState.ACTIVE:
            raise KeyError(f"LifecycleManager: {name!r} 非 ACTIVE"
                           f"（state={fiber.state.value if fiber else 'absent'}）")
        return fiber.instance


# 模块级单例（进程内唯一生命周期管理器；测试用 _reset）
_default_manager: Optional[LifecycleManager] = None


def get_lifecycle_manager() -> LifecycleManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = LifecycleManager()
        _default_manager.ensure_subscribed()
    return _default_manager


def _reset_lifecycle_manager() -> None:
    """仅测试用。"""
    global _default_manager
    if _default_manager is not None:
        for name in list(_default_manager._fibers):
            _default_manager.detach(name)
    _default_manager = None
