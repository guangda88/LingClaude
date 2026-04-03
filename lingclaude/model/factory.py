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

    if name == "openai":
        from lingclaude.model.openai_provider import OpenAIProvider
        return Result.ok(OpenAIProvider(cfg))
    elif name == "anthropic":
        from lingclaude.model.anthropic_provider import AnthropicProvider
        return Result.ok(AnthropicProvider(cfg))

    return Result.fail(
        f"未知的模型提供商: '{name}'。"
        f"支持的提供商: openai, anthropic。"
        f"可通过 provider_name 参数指定，或在 config.yaml 的 model.provider 中设置"
    )


def _detect_provider(cfg: ModelConfig) -> str:
    model = cfg.model.lower()
    if "gpt" in model or model.startswith("o1") or model.startswith("o3") or model.startswith("o4"):
        return "openai"
    if "claude" in model:
        return "anthropic"
    return "openai"


def _get_env_key(provider: str) -> str:
    if provider == "openai":
        return os.environ.get("OPENAI_API_KEY", "")
    if provider == "anthropic":
        return os.environ.get("ANTHROPIC_API_KEY", "")
    return ""
