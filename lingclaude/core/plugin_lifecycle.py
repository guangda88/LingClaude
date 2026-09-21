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
from enum import Enum
from typing import Any, Callable, Iterable, Optional

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

    def __init__(self, verify_timeout: float = 5.0, hooks: Any = None) -> None:
        self._fibers: dict[str, PluginFiber] = {}
        self._lock = threading.RLock()
        self._verify_timeout = verify_timeout
        self._hooks = hooks  # 可选：HookManager（触发 PRE/POST_HOT_SWAP）；None=静默
        self._subscribed = False

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

    # ---- 状态机核心 ----

    def _activate(self, fiber: PluginFiber) -> None:
        """PENDING/INACTIVE → LOADING → ACTIVE。factory 可返回实例或 (实例, disposer)。"""
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
            fiber.state = LifecycleState.ACTIVE
            fiber.epoch = fiber.compute_epoch()
            logger.info("lifecycle: %s ACTIVE (epoch=%s)", fiber.name, fiber.epoch)
        except Exception as exc:  # noqa: BLE001 激活失败 fail-loud 记录
            fiber.state = LifecycleState.FAILED
            fiber.error = f"{type(exc).__name__}: {exc}"
            fiber.instance = None
            logger.exception("lifecycle: %s FAILED on activate", fiber.name)

    def _unload_fiber(self, fiber: PluginFiber) -> None:
        """任意态 → UNLOADING → INACTIVE。disposer 逆序；instance 释放。"""
        if fiber.state is LifecycleState.INACTIVE and fiber.instance is None:
            return
        prev = fiber.state
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
                    fiber.state = LifecycleState.INACTIVE
                return fiber.state
            # 依赖齐备：指纹未变且已 ACTIVE → no-op（epoch 语义：没变就什么都不做）
            if fiber.state is LifecycleState.ACTIVE and fiber.epoch == new_epoch:
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
