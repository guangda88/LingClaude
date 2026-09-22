from __future__ import annotations

import os
from typing import Any

from lingclaude.core.types import Result
from lingclaude.model.types import ModelConfig, ModelProvider


def create_provider(
    config: ModelConfig | dict[str, Any] | None = None,
    provider_name: str | None = None,
) -> Result[ModelProvider]:
    if isinstance(config, dict):
        config = ModelConfig.from_dict(config)

    cfg = config or ModelConfig()

    name = provider_name or _detect_provider(cfg)

    if not cfg.api_key:
        env_key = _get_env_key(name)
        if env_key:
            cfg = ModelConfig(
                model=cfg.model,
                api_key=env_key,
                base_url=cfg.base_url,
                max_tokens=cfg.max_tokens,
                temperature=cfg.temperature,
                system_prompt=cfg.system_prompt,
            )

    # P1-3 (灵元 R2): if/elif 硬编码链 → 注册表查表
    # 加新 provider = provider_registry.register() 一行，不动本函数
    from lingclaude.model.provider_registry import ProviderRegistry

    result = ProviderRegistry.create(name, cfg)
    if result.is_ok:
        return result

    # 保留原 fail 文案（含可用 provider 清单 + 使用提示）
    supported = ", ".join(ProviderRegistry.registered_names())
    return Result.fail(
        f"未知的模型提供商: '{name}'。"
        f"支持的提供商: {supported}。"
        f"可通过 provider_name 参数指定，或在 config.yaml 的 model.provider 中设置"
    )


def _detect_provider(cfg: ModelConfig) -> str:
    model = cfg.model.lower()
    if "gpt" in model or model.startswith("o1") or model.startswith("o3") or model.startswith("o4"):
        return "openai"
    if "claude" in model:
        return "anthropic"
    if "glm" in model or "deepseek" in model or "qwen" in model:
        return "openai"
    return "openai"


def _get_env_key(provider: str) -> str:
    # P1-7 接线（2026-09-22）：credential_pool 优先（env 门禁，默认关）。
    # env LINGCLAUDE_CREDENTIAL_POOL=1 时先从池取 key（LRU 轮转，跳过熔断账号）；
    # 池空/未配置该 provider → 落回原 env/key_store 链（行为零分叉）。
    if os.environ.get("LINGCLAUDE_CREDENTIAL_POOL", "") in ("1", "true", "TRUE"):
        pool_key = _pool_get_key(provider)
        if pool_key:
            return pool_key
    if provider == "openai":
        return (
            os.environ.get("OPENAI_API_KEY", "")
            or os.environ.get("DEEPSEEK_API_KEY", "")
            or os.environ.get("ZHIPU_API_KEY", "")
            or _key_store_get("OPENAI_API_KEY")
            or _key_store_get("DEEPSEEK_API_KEY")
            or _key_store_get("ZHIPU_API_KEY")
        )
    if provider == "anthropic":
        return (
            os.environ.get("ANTHROPIC_API_KEY", "")
            or os.environ.get("ZHIPU_API_KEY", "")
            or _key_store_get("ANTHROPIC_API_KEY")
            or _key_store_get("ZHIPU_API_KEY")
        )
    return ""


def _key_store_get(key_name: str) -> str:
    try:
        # 跨仓契约：显式声明依赖 ling_lib 共享工具目录（env 可覆盖 LING_LIB_PATH），
        # 替代硬编码 ~/.ling_lib sys.path.insert（灵元「跨仓 = 显式插片契约」）。
        from lingclaude.lacp.cross_repo_seam import ensure_import_path

        ensure_import_path("ling_lib")
        from ling_key_store import get_key
        return get_key(key_name) or ""
    except (ImportError, ModuleNotFoundError, AttributeError):
        return ""


# P1-7 接线（2026-09-22）：credential_pool 进程级单例与取 key 助手。
_CREDENTIAL_POOL: Any | None = None


def _pool_get_key(provider: str) -> str:
    """从凭据池取该 provider 的下一个可用 key（LRU，跳过熔断账号）。

    池未建/该 provider 无账号/全部熔断 → 空串（调用方落回 env/key_store 链）。
    """
    global _CREDENTIAL_POOL
    try:
        from lingclaude.model.credential_pool import CredentialPool
        if _CREDENTIAL_POOL is None:
            _CREDENTIAL_POOL = CredentialPool.from_env()
        if _CREDENTIAL_POOL is None:
            return ""
        return _CREDENTIAL_POOL.next_key(provider) or ""
    except Exception:  # noqa: BLE001 — 池不可用 → 空 key 落回原链
        return ""


def _pool_record_exhausted(provider: str, api_key: str) -> None:
    """配额耗尽上报（供 provider 调用失败侧调用；池未启用时静默）。"""
    if _CREDENTIAL_POOL is None:
        return
    try:
        _CREDENTIAL_POOL.record_exhausted(provider, api_key)
    except Exception:  # noqa: BLE001 — 上报失败不影响主流程
        pass
