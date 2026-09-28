"""模型上下文窗口查表（2026-09-28 新增）。

解决 toolbar ctx% 分母硬编码 128_000 与真实模型窗口脱节的问题：
- glm-5.3-flash 官方窗口 1M（docs.bigmodel.cn），此前 config.yaml 写 128000 → 7.8× 虚高告警。
- 切模型后分母不跟随（F12f 降级 / /model 切换）→ 窗口失真。

优先级：config.context_window_tokens（显式配置）> 模型查表 > provider 前缀兜底 > 128K。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 已知模型精确窗口（官方文档口径）。
# 键 = 模型名小写；值 = 窗口 tokens。
_KNOWN_WINDOWS: dict[str, int] = {
    # GLM 系列（docs.bigmodel.cn）
    "glm-5.3-flash": 1_000_000,
    "glm-5.3": 1_000_000,
    "glm-5": 1_000_000,
    "glm-4.5-flash": 1_000_000,
    "glm-4.5": 1_000_000,
    "glm-4-flash": 1_000_000,
    "glm-4": 1_000_000,
    # MiniMax 系列（platform.minimaxi.com/docs）
    "minimax-m3.1-flash-preview": 1_000_000,
    "minimax-m3": 1_000_000,
    "minimax-m2.7": 204_800,
    "minimax-m2.7-highspeed": 204_800,
    "minimax-m2.5": 204_800,
    "minimax-m2.5-highspeed": 204_800,
    "minimax-m2.1": 204_800,
    "minimax-m2.1-highspeed": 204_800,
    "minimax-m2": 204_800,
    "m2-her": 65_536,
    # OpenAI 系列（保守 128K，GPT-4o/4.1 均为 128K）
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4.1": 128_000,
    "gpt-4.1-mini": 128_000,
    "gpt-4.1-nano": 128_000,
    "o4-mini": 200_000,
    "o3": 200_000,
    "o3-mini": 200_000,
    # Claude（Anthropic）
    "claude-opus-4": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-haiku-4": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3-5-haiku": 200_000,
    "claude-3-opus": 200_000,
    # DeepSeek
    "deepseek-v4-flash": 128_000,
    "deepseek-v3": 128_000,
    "deepseek-chat": 128_000,
    "deepseek-reasoner": 128_000,
    # Qwen
    "qwen3.5-35b-a3b": 128_000,
    "qwen3-235b-a22b": 128_000,
    "qwen2.5-72b": 128_000,
    "qwen2.5-32b": 128_000,
    "qwen2.5-14b": 128_000,
    "qwen2.5-7b": 128_000,
}

# provider 前缀 → 窗口兜底（模型名未命中时按 provider 猜）。
_PROVIDER_FALLBACKS: dict[str, int] = {
    "glm": 1_000_000,      # 智谱全系 1M
    "zhipu": 1_000_000,
    "minimax": 1_000_000,  # M3.1 起 1M；M2.7 204.8K 但 M3 已 1M
    "openai": 128_000,
    "anthropic": 200_000,
    "claude": 200_000,
    "deepseek": 128_000,
    "qwen": 128_000,
    "dashscope": 128_000,
}

# 最终兜底（模型名+provider 均未命中）。
_DEFAULT_FALLBACK = 128_000


def _lookup_model_window(model_name: str) -> int | None:
    """精确匹配已知模型窗口。"""
    if not model_name:
        return None
    return _KNOWN_WINDOWS.get(model_name.lower().strip())


def _lookup_provider_window(provider: str) -> int | None:
    """按 provider 前缀兜底。"""
    if not provider:
        return None
    return _PROVIDER_FALLBACKS.get(provider.lower().strip())


def resolve_context_window(engine: Any) -> int:
    """解析当前引擎的上下文窗口（tokens）。

    优先级：
    1. 模型名精确查表（engine._current_model_name → provider._config.model → cfg.model.model）
       —— 当前生效模型的真实窗口是 toolbar 语义权威（F12f 降级后跟随）
    2. engine.config.context_window_tokens（显式配置，查表未命中时的兜底）
    3. provider 前缀兜底
    4. 128K 全局兜底

    Args:
        engine: 引擎实例，需有 .config（含 context_window_tokens / model）
                及可选 ._current_model_name / ._provider。

    Returns:
        窗口 tokens 数。
    """
    # 1. 模型名查表（当前生效模型的真实窗口是权威）
    model_name = _extract_model_name(engine)
    win = _lookup_model_window(model_name)
    if win:
        return win

    # 2. 显式配置（查表未命中时的兜底）
    cfg_val = getattr(getattr(engine, "config", None), "context_window_tokens", None)
    if cfg_val and isinstance(cfg_val, (int, float)) and cfg_val > 0:
        return int(cfg_val)

    # 3. provider 前缀兜底
    provider = _extract_provider(engine)
    win = _lookup_provider_window(provider)
    if win:
        return win

    # 4. 全局兜底
    logger.debug("context_window: 未命中模型=%r provider=%r，回退 %d", model_name, provider, _DEFAULT_FALLBACK)
    return _DEFAULT_FALLBACK


def _extract_model_name(engine: Any) -> str:
    """从引擎提取当前生效模型名（降级后同步优先）。"""
    # engine._current_model_name（loop_body 降级同步，2026-09-28 方案 B）
    name = str(getattr(engine, "_current_model_name", "") or "")
    if name:
        return name
    # provider._config.model
    prov = getattr(engine, "_provider", None)
    if prov:
        prov_cfg = getattr(prov, "_config", None)
        name = str(getattr(prov_cfg, "model", "") or "")
        if name:
            return name
    # config.model.model
    cfg = getattr(engine, "config", None)
    model_cfg = getattr(cfg, "model", None)
    name = str(getattr(model_cfg, "model", "") or "")
    return name


def _extract_provider(engine: Any) -> str:
    """从引擎提取 provider 名。"""
    prov = getattr(engine, "_provider", None)
    if prov:
        prov_cfg = getattr(prov, "_config", None)
        name = str(getattr(prov_cfg, "provider", "") or "")
        if name:
            return name
    cfg = getattr(engine, "config", None)
    model_cfg = getattr(cfg, "model", None)
    return str(getattr(model_cfg, "provider", "") or "")


def known_window(model_name: str) -> int | None:
    """公开查询接口：返回已知模型窗口，未知返回 None。"""
    return _lookup_model_window(model_name)


def register_window(model_name: str, window: int) -> None:
    """注册新模型窗口（供插件/配置扩展）。"""
    _KNOWN_WINDOWS[model_name.lower().strip()] = int(window)
