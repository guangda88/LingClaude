"""灵元 R2: SeamRegistry —— 插片 = type（register/unregister 热拔插）。

P3-1 (2026-09-14, 以灵元 1.0 为尺):
  - provider/tool/sandbox/transport 全是 type，注册即生效，主干零 diff
  - register(\"llm\", \"openai_compatible\", instance) / unregister(\"tool\", \"name\")
  - 热拔插语义：unregister 只影响后续 get，已持引用不受影响

与 lacp/capability_seam.py 的分工:
  - LACP CapabilitySeam: 跨进程能力缝（双签 + 审计 + 传输），用于 webui 等外部宿主
  - 本 SeamRegistry: 进程内插片注册表（灵元"插片 = type"），轻量、无签名开销

用法:
    from lingclaude.core.seam import SeamRegistry, SeamType
    SeamRegistry.register(SeamType.PROVIDER, "openai_compatible", instance)
    inst = SeamRegistry.get(SeamType.PROVIDER, "openai_compatible")
    SeamRegistry.unregister(SeamType.PROVIDER, "openai_compatible")

设计纪律（灵元 §七 关键约束）:
  - 只注册"变化"的插片，绝不注册主干（主干只有 records+events，不需要热重载）
  - register 同名覆盖（热更）；unregister 缺名 no-op（幂等）
  - 实例必须满足对应 SeamType 的 Protocol（结构性检查，不强制继承）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, TypeVar, runtime_checkable

logger = logging.getLogger(__name__)

T = TypeVar("T")


class SeamType(str, Enum):
    """插片类型（灵元：变化的全是 type）。"""

    PROVIDER = "provider"        # 模型 provider（openai/anthropic/local/...）
    TOOL = "tool"                # 工具（bash/read/write/...）
    SANDBOX = "sandbox"          # 沙箱后端（bwrap/noop/firejail/...）
    TRANSPORT = "transport"      # 传输（LACP/mcp/...）
    MEMORY = "memory"            # 记忆策略层
    GOVERNANCE = "governance"    # 治理插片
    SELF_OPT = "self_opt"        # 自优化插片
    AGENT = "agent"              # 外部 Agent 插片（灵研/灵知/灵信/灵极优/灵通问道…，对标 Ekko Studio 挂载）
    MULTIMODAL = "multimodal"    # 多模态任务插片（灵通问道 analyze_emotion/synthesize_speech/…）
    ORCHESTRATOR = "orchestrator"  # 跨 Agent 编排插片（CrewOrchestrator，对标 Multi-Agent Crews）
    RESOURCE = "resource"          # 资源探针插片（GPU/CPU/内存/磁盘，第四层预研，铁律 8 隔离故障域+N4 缺席查硬件实例）


# N3 命名空间互证（铁律 7）：跨物理层插片缝 key 必带域前缀 {ns}/{seam}。
# 域 ∈ {core, agent, cap, os, hw}（四层宏图）。core 域类型保持原字面量（存量豁免）。
DOMAIN_NAMESPACES = ("core", "agent", "cap", "os", "hw")
# 跨物理层类型（N3 强制域前缀）；core 域类型（provider/tool/sandbox/transport/
# memory/governance/self_opt）保持裸 key 存量豁免（铁律 7 §2）。
CROSS_PHYSICAL_TYPES = frozenset({
    SeamType.AGENT, SeamType.MULTIMODAL, SeamType.ORCHESTRATOR, SeamType.RESOURCE,
})


def validate_seam_namespace(seam_type: SeamType, name: str) -> str:
    """N3 命名空间校验：跨物理层类型强制 name 带域前缀，域前缀 ∈ 五域。

    返回规范化 name（strip 后原样）；非法抛 ValueError（fail fast，注册前拦截）。
    core 域类型跳过（存量豁免：provider/tool/sandbox/... 保持裸 key）。
    """
    name = str(name).strip()
    if not name:
        raise ValueError("seam name must not be empty")
    if seam_type in CROSS_PHYSICAL_TYPES:
        if "/" not in name:
            raise ValueError(
                f"N3: 跨物理层插片 {seam_type.value} 缝 key 必带域前缀 "
                f"{{ns}}/{{seam}}（ns ∈ {DOMAIN_NAMESPACES}），收到裸 key {name!r}"
            )
        ns = name.split("/", 1)[0]
        if ns not in DOMAIN_NAMESPACES:
            raise ValueError(
                f"N3: 域前缀 {ns!r} 不在 {DOMAIN_NAMESPACES} 五域内（name={name!r}）"
            )
    return name


# 拔插等级声明（M5 前置义务，铁律 §二「拔插等级声明义务」，2026-09-17 整改补齐）：
#   L1 替换：A 拔下 B 插上，接口一致即不崩（J2 现行测的等级）；
#   L2 缺席降级：拔掉后走降级路径——降级实现本身必须是插片（Noop 范式），禁止主干 if-else 降级；
#   L3 缺席裸奔：拔掉后功能消失但系统不崩，主干零依赖具体功能。
# 等级未声明默认按 L3（最严）检验（M5 条文）。每类声明其承诺兑现的最高等级；
# 更低等级自动被更高等级蕴含（L2 ⊃ L1，L3 ⊃ L1）。
PLUG_LEVELS: dict[SeamType, str] = {
    SeamType.PROVIDER: "L1",      # provider 可替换（openai/anthropic/local 互换，接口一致）
    SeamType.TOOL: "L3",          # 工具缺席 = 少一个功能，主干不崩
    SeamType.SANDBOX: "L2",       # NoopSandboxProvider 缺席降级范式（J2 实测在册）
    SeamType.TRANSPORT: "L1",     # 传输实现可互换（LACP/mcp）
    SeamType.MEMORY: "L1",        # 记忆策略可替换
    SeamType.GOVERNANCE: "L1",    # 治理插片可替换
    SeamType.SELF_OPT: "L1",      # 自优化插片可替换
    SeamType.AGENT: "L3",         # 外部 Agent 缺席 = 功能消失不崩
    SeamType.MULTIMODAL: "L3",    # 多模态任务缺席 = 功能消失不崩
    SeamType.ORCHESTRATOR: "L3",  # 编排缺席 = 功能消失不崩
    SeamType.RESOURCE: "L3",       # 资源探针缺席 = 该探针 absent（铁律 8 隔离故障域，单探针圈死），主干不崩
}


@runtime_checkable
class ProviderPlugin(Protocol):
    """provider 插片协议：可调用（构造器）或提供 create()。"""

    name: str

    def create(self, config: Any = None) -> Any:
        ...


@runtime_checkable
class ToolPlugin(Protocol):
    """tool 插片协议：可调用（handler）或提供 execute()。"""

    name: str

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        ...


@runtime_checkable
class SandboxPlugin(Protocol):
    """sandbox 插片协议：提供 available() + wrap()。"""

    name: str

    def available(self) -> bool:
        ...

    def wrap(self, command: str, **kwargs: Any) -> str:
        ...


# ── Agent 插片协议（Phase 2：对标 Ekko Studio 多 Agent 挂载调度）──
@dataclass(frozen=True)
class AgentCapability:
    """Agent 插片能力元数据（挂载时注册进 SeamRegistry）。"""

    name: str
    description: str = ""
    version: str = "0.1.0"
    kind: str = "agent"
    methods: tuple[str, ...] = field(default_factory=tuple)
    transport: str = "in-process"


@runtime_checkable
class AgentSeam(Protocol):
    """外部 Agent 插片协议（对齐 SubagentBackend，可互操作调度）。"""

    name: str

    def run(self, *args: Any, **kwargs: Any) -> Any: ...
    def abort(self, agent_id: str) -> bool: ...
    def status(self, agent_id: str) -> str: ...


@runtime_checkable
class MultimodalTask(Protocol):
    """多模态任务插片协议（灵通问道 analyze_emotion/synthesize_speech/...）。"""

    name: str

    def task_type(self) -> str: ...
    def input_schema(self) -> dict[str, Any]: ...
    def execute(self, **kwargs: Any) -> Any: ...


@runtime_checkable
class CrewOrchestrator(Protocol):
    """跨 Agent 编排插片协议（对标 Multi-Agent Crews）。"""

    name: str

    def create_crew(self, members: list[str], **kwargs: Any) -> str: ...
    def dispatch(self, crew_id: str, task: str, mode: str = "sequential") -> Any: ...
    def status(self, crew_id: str) -> dict[str, Any]: ...


@runtime_checkable
class ResourceProbe(Protocol):
    """资源探针插片协议（第四层 OS/硬件预研，铁律 8 隔离故障域+N4 缺席查硬件实例）。

    探针=单数据源观测器（GPU/CPU/内存/磁盘各为一个插片实例，域前缀 os/）：
    - probe()：采一次数据源 → 数据 dict（无数据=absent，不假活）；
    - status()：健康态（数据源消失 → absent，故障圈死在单探针，不扩散到域外）。
    T3 只观测：OS 黑盒，只记 resource_probe record，不判"OS 违规"。
    """

    name: str

    def probe(self) -> dict[str, Any] | None: ...
    def status(self) -> dict[str, Any]: ...


# SeamType → 期望的 Protocol（结构性检查用；None=不检查）
_EXPECTED_PROTOCOL: dict[SeamType, type[Protocol] | None] = {
    SeamType.PROVIDER: ProviderPlugin,
    SeamType.TOOL: ToolPlugin,
    SeamType.SANDBOX: SandboxPlugin,
    SeamType.TRANSPORT: None,
    SeamType.MEMORY: None,
    SeamType.GOVERNANCE: None,
    SeamType.SELF_OPT: None,
    SeamType.AGENT: AgentSeam,
    SeamType.MULTIMODAL: MultimodalTask,
    SeamType.ORCHESTRATOR: CrewOrchestrator,
    SeamType.RESOURCE: ResourceProbe,
}


class SeamRegistry:
    """进程内插片注册表（灵元：插片 = type，注册即生效）。

    线程安全（注册/查询互斥）；注册不校验实例有效性（鸭子类型，
    消费方按协议调用，缺方法即时报错——fail fast 优于静默降级）。
    """

    _registry: dict[SeamType, dict[str, Any]] = {}
    _lock = None  # 惰性初始化（避免 import 期锁开销）
    _subscribers: list = []  # P0#2: 缝变更订阅（回调签名 fn(action: str, seam_type: SeamType, name: str)）

    @classmethod
    def _get_lock(cls):
        if cls._lock is None:
            import threading

            cls._lock = threading.RLock()
        return cls._lock

    @classmethod
    def register(cls, seam_type: SeamType, name: str, instance: Any,
                 validate_namespace: bool = True) -> None:
        """注册插片实例。同名覆盖（热更语义：新注册替换旧实例，已持引用不受影响）。

        validate_namespace=False 用于测试 mock / 进程内探针（非跨物理层插片，
        显式声明豁免 N3 域前缀校验——J5：逃逸必须显式，不静默）。
        """
        seam_type = SeamType(seam_type)
        if validate_namespace:
            name = validate_seam_namespace(seam_type, name)
        else:
            name = str(name).strip()
            if not name:
                raise ValueError("seam name must not be empty")
        with cls._get_lock():
            cls._registry.setdefault(seam_type, {})[name] = instance
        logger.debug("SeamRegistry: register %s/%s -> %r", seam_type.value, name, instance)
        cls._notify_change("register", seam_type, name)

    @classmethod
    def get(cls, seam_type: SeamType, name: str) -> Any:
        """取插片实例；不存在抛 KeyError（fail fast）。"""
        seam_type = SeamType(seam_type)
        name = str(name)
        with cls._get_lock():
            try:
                return cls._registry[seam_type][name]
            except KeyError:
                raise KeyError(
                    f"SeamRegistry: {seam_type.value}/{name} 未注册。"
                    f"已注册: {sorted(cls._registry.get(seam_type, {}).keys())}"
                ) from None

    @classmethod
    def get_optional(cls, seam_type: SeamType, name: str) -> Any | None:
        """取插片实例；不存在返回 None（graceful degrade）。"""
        try:
            return cls.get(seam_type, name)
        except KeyError:
            return None

    @classmethod
    def unregister(cls, seam_type: SeamType, name: str) -> bool:
        """注销插片（热拔插）。缺名 no-op 返回 False；成功注销返回 True。"""
        seam_type = SeamType(seam_type)
        name = str(name)
        with cls._get_lock():
            bucket = cls._registry.get(seam_type)
            if bucket is None or name not in bucket:
                return False
            del bucket[name]
            if not bucket:
                cls._registry.pop(seam_type, None)
        logger.info("SeamRegistry: unregister %s/%s", seam_type.value, name)
        cls._notify_change("unregister", seam_type, name)
        return True

    @classmethod
    def list_names(cls, seam_type: SeamType) -> list[str]:
        """列出某类型全部已注册名。"""
        seam_type = SeamType(seam_type)
        with cls._get_lock():
            return sorted(cls._registry.get(seam_type, {}).keys())

    @classmethod
    def has(cls, seam_type: SeamType, name: str) -> bool:
        """是否已注册。"""
        return name in cls.list_names(seam_type)

    @classmethod
    def get_all(cls, seam_type: SeamType) -> dict[str, Any]:
        """取某类型全部插片（名 → 实例）的**副本**。

        P11: 消费侧统一查询视图 —— 热拔插状态可观测。
        - 返回 dict 副本，外部增删不影响注册表（防御性拷贝）。
        - 实例本身按引用共享（消费方可直接调用 execute/create）。
        """
        seam_type = SeamType(seam_type)
        with cls._get_lock():
            return dict(cls._registry.get(seam_type, {}))

    @classmethod
    def snapshot(cls) -> dict[str, list[str]]:
        """全量快照：SeamType → 已注册名列表（只读观测，供诊断/测试）。

        P11: 统一查询视图 —— 一次调用看全所有插片类型的热拔插状态。
        """
        with cls._get_lock():
            return {
                stype.value: sorted(names.keys())
                for stype, names in cls._registry.items()
            }

    @classmethod
    def reset(cls) -> None:
        """清空注册表（仅测试用）。"""
        with cls._get_lock():
            cls._registry.clear()
            cls._subscribers.clear()

    @classmethod
    def subscribe_change(cls, callback) -> None:
        """订阅缝变更（P0#2：plugin_lifecycle 依赖图刷新钩子）。

        callback(action, seam_type, name)：action ∈ {"register", "unregister"}。
        回调在注册锁外执行；单个订阅者异常不中断广播（fail-open）。
        """
        with cls._get_lock():
            if callback not in cls._subscribers:
                cls._subscribers.append(callback)

    @classmethod
    def unsubscribe_change(cls, callback) -> None:  # noqa: C901
        """取消订阅（幂等：未订阅则 no-op）。"""
        with cls._get_lock():
            if callback in cls._subscribers:
                cls._subscribers.remove(callback)

    @classmethod
    def _notify_change(cls, action: str, seam_type: SeamType, name: str) -> None:
        """向全部订阅者广播缝变更（锁外快照订阅者列表再逐个调用）。"""
        with cls._get_lock():
            subscribers = list(cls._subscribers)
        for callback in subscribers:
            try:
                callback(action, seam_type, name)
            except Exception:  # noqa: BLE001 订阅者异常只记日志，不反噬注册方
                logger.exception(
                    "SeamRegistry subscriber error (action=%s seam=%s/%s)",
                    action, seam_type.value, name,
                )

    @classmethod
    def check_protocol(cls, seam_type: SeamType, instance: Any) -> list[str]:
        """结构性协议检查：返回缺失的成员名列表（空 = 满足）。

        不强制：注册不调用，供工具/诊断用。None 协议 = 不检查返回空。
        """
        seam_type = SeamType(seam_type)
        protocol = _EXPECTED_PROTOCOL.get(seam_type)
        if protocol is None:
            return []
        missing = []
        for attr in getattr(protocol, "__protocol_attrs__", ()):
            if not hasattr(instance, attr):
                missing.append(attr)
        return missing
