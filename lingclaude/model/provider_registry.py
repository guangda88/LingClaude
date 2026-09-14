"""灵元 R2: provider 注册表 —— 策略是 data，注册即生效。

P1-3 (2026-09-14, 以灵元 1.0 为尺):
  - factory.py 的 if/elif 硬编码链收敛为注册表查表
  - 加新 provider = 注册表加一行，不动 factory 代码
  - 构造器统一接收 config: ModelConfig，适配不同 provider 的签名差异

与 factory.create_provider 的契约:
  - create_provider(config, provider_name=None) 保持原签名，零破坏
  - 未知 provider 返回 Result.fail（保留原 fail 语义）
  - 注册表支持运行时注册（hot register），配合灵元热重载

参考: docs/theory/LINGYUAN_AUDIT_LINGCLAUDE_v2.md 阶段0
      docs/LINGYUAN_1.0_ANALYSIS_AND_ROADMAP.md §四（策略是 data，不是结构）
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from lingclaude.core.seam import SeamRegistry, SeamType
from lingclaude.core.types import Result
from lingclaude.model.types import ModelConfig, ModelProvider

logger = logging.getLogger(__name__)

# 构造器类型: 统一接收 config，返回 ModelProvider 实例
ProviderFactory = Callable[[ModelConfig], ModelProvider]


def _default_provider_factory(cls: type[ModelProvider]) -> ProviderFactory:
    """默认构造器: 直接 cls(config)。覆盖标准签名 provider。"""

    def _factory(config: ModelConfig) -> ModelProvider:
        return cls(config)

    return _factory


class ProviderRegistry:
    """provider 注册表（灵元 R2: 策略是 data）。

    用法:
        ProviderRegistry.register("openai", OpenAIProvider)   # 模块加载时
        provider = ProviderRegistry.create("openai", cfg)     # 运行时创建
        ProviderRegistry.create("unknown", cfg)               # → Result.fail

    热重载语义:
        运行时 register 覆盖同名 provider，已在运行的实例不受影响，
        新 create 使用新构造器（只 reload data，不 reload module）。
    """

    _registry: dict[str, ProviderFactory] = {}
    _registry_classes: dict[str, type[ModelProvider]] = {}

    @classmethod
    def register(
        cls,
        name: str,
        provider_cls: type[ModelProvider],
        factory: ProviderFactory | None = None,
    ) -> None:
        """注册一个 provider。

        Args:
            name: provider 名（与 config 的 model.provider 字段对齐）
            provider_cls: provider 类（须继承 ModelProvider）
            factory: 可选自定义构造器；缺省用 cls(config) 标准签名
        """
        name = name.strip().lower()
        if not name:
            raise ValueError("provider name must not be empty")
        cls._registry[name] = factory or _default_provider_factory(provider_cls)
        cls._registry_classes[name] = provider_cls
        # P5: 同步到进程内 SeamRegistry（统一查询视图 —— SeamRegistry.get(PROVIDER, name)）
        # 覆盖语义一致：ProviderRegistry 同名覆盖，SeamRegistry 同名覆盖（热更语义）。
        SeamRegistry.register(SeamType.PROVIDER, name, provider_cls)
        logger.debug("ProviderRegistry: registered '%s' -> %s", name, provider_cls.__name__)

    @classmethod
    def create(cls, name: str, config: ModelConfig) -> Result[ModelProvider]:
        """按名创建 provider 实例（Result 语义，保留 factory 原 fail 行为）。

        P13 (S2): 消费面优先查进程内 SeamRegistry（热拔插生效）——
        若外部 register(PROVIDER, name, instance) 注册了**已构造实例**，
        则直接返回该实例（注册表换血 = 下个 create 用新实例，无需重启）。
        否则回退内部注册表按构造器创建。
        """
        name = (name or "").strip().lower()
        # S2: 先查进程内 SeamRegistry —— 已注册实例优先（热拔插语义）
        seam_inst = SeamRegistry.get_optional(SeamType.PROVIDER, name)
        if seam_inst is not None and not isinstance(seam_inst, type):
            # 已构造实例（非类）→ 直接返回（热拔插：外部可注入已就绪实例）
            return Result.ok(seam_inst)
        factory = cls._registry.get(name)
        if factory is None:
            supported = ", ".join(sorted(cls._registry.keys())) or "(空)"
            return Result.fail(
                f"未知的模型提供商: '{name}'。支持的提供商: {supported}。"
            )
        try:
            provider = factory(config)
            return Result.ok(provider)
        except Exception as exc:  # noqa: BLE001 — 构造失败转 Result.fail（graceful degrade）
            logger.exception("ProviderRegistry: create '%s' failed", name)
            return Result.fail(f"创建 provider '{name}' 失败: {exc}")

    @classmethod
    def get_factory(cls, name: str) -> ProviderFactory | None:
        """取已注册的构造器（供测试/工具内省）。"""
        return cls._registry.get((name or "").strip().lower())

    @classmethod
    def registered_names(cls) -> tuple[str, ...]:
        """已注册 provider 名（供 /model 清单与诊断输出）。"""
        return tuple(sorted(cls._registry.keys()))

    @classmethod
    def reset(cls) -> None:
        """清空注册表并恢复内置 provider（仅测试用）。

        语义：回到"模块加载完的初始状态"——先清空全部（含 SeamRegistry PROVIDER 槽位），
        再重新注册 builtins（openai/anthropic/local）。
        这样后续依赖内置 provider 的测试不会被"清空"抹掉（修复跨测试状态污染：
        test_p4_p5 的 reset 曾导致 test_s2_s3 的 openai 创建失败）。
        """
        # P5: 同步清空 SeamRegistry 的 PROVIDER 槽位（reset 成对出现，防跨测试泄漏）
        for name in list(cls._registry.keys()):
            SeamRegistry.unregister(SeamType.PROVIDER, name)
        cls._registry.clear()
        cls._registry_classes.clear()
        _register_builtins()


def _register_builtins() -> None:
    """注册内置 provider（模块加载时调用一次）。"""
    # openai / anthropic: 标准签名 cls(config)
    try:
        from lingclaude.model.openai_provider import OpenAIProvider

        ProviderRegistry.register("openai", OpenAIProvider)
    except ImportError:  # pragma: no cover — 依赖缺失时跳过，不阻断导入
        logger.warning("ProviderRegistry: openai_provider 导入失败，跳过注册")

    try:
        from lingclaude.model.anthropic_provider import AnthropicProvider

        ProviderRegistry.register("anthropic", AnthropicProvider)
    except ImportError:  # pragma: no cover
        logger.warning("ProviderRegistry: anthropic_provider 导入失败，跳过注册")

    # local: 签名不同（model_path），用构造器适配
    try:
        from lingclaude.model.local_provider import LocalModelProvider

        def _local_factory(config: ModelConfig) -> ModelProvider:
            return LocalModelProvider(model_path=config.model)

        ProviderRegistry.register("local", LocalModelProvider, factory=_local_factory)
    except ImportError:  # pragma: no cover
        logger.warning("ProviderRegistry: local_provider 导入失败，跳过注册")


_register_builtins()
