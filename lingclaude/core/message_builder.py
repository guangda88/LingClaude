"""message_builder.py — MessageBuilder 外观层（委托 system_prompt_builder 单实现）

灵元 1.0 尺子再照（2026-09-15 E1）：
  原实现是 system_prompt_builder.build_adaptive_system_prompt 的逐字重复（227 行），
  生产代码零引用（query_engine_model_mixin 已直接用 system_prompt_builder），
  仅 tests/test_message_builder.py 引用。按路线图 P0「合并 message_builder →
  system_prompt_builder facade」收敛：

  维护点 2 → 1：行为逻辑只在 system_prompt_builder.py 一处，本文件只做外观转发。
  保留 MessageBuilder 类与 assemble_system_prompt 函数签名（兼容旧引用），
  全部委托 system_prompt_builder.build_adaptive_system_prompt 同一实现。

  灵元判据：同一概念一处实现；行为差异测试仍指向本 facade（委托同实现，行为等价）。
"""

from __future__ import annotations

from typing import Any

from lingclaude.core.system_prompt_builder import (
    _BASE_PROMPT as _SYSTEM_BASE_PROMPT,
)
from lingclaude.core.system_prompt_builder import build_adaptive_system_prompt as _build

# 兼容别名：旧引用 MessageBuilder.BASE_PROMPT 仍可用
BASE_PROMPT = _SYSTEM_BASE_PROMPT


class MessageBuilder:
    """System prompt 装配外观 — 委托 system_prompt_builder 单实现。

    接收 behavior/memory/meta_cognition/dementia_detector 依赖，
    输出完整 system prompt string（含 SESSION_CONTEXT，见 system_prompt_builder）。
    """

    BASE_PROMPT = _SYSTEM_BASE_PROMPT

    def __init__(
        self,
        *,
        behavior: Any,
        layered_memory: Any,
        meta_cognition: Any,
        dementia_detector: Any,
    ) -> None:
        self._behavior = behavior
        self._layered_memory = layered_memory
        self._meta_cognition = meta_cognition
        self._dementia_detector = dementia_detector

    def build_adaptive_system_prompt(
        self,
        messages: list[str],
        session_cache_hits: int = 0,
        project_index: dict[str, list[str]] | None = None,
        tool_call_count: int = 0,  # R8：触发 sub_agent 推荐提示
    ) -> str:
        """委托 system_prompt_builder.build_adaptive_system_prompt（单实现）。"""
        return _build(
            behavior=self._behavior,
            layered_memory=self._layered_memory,
            meta_cognition=self._meta_cognition,
            messages=messages,
            session_cache_hits=session_cache_hits,
            dementia_detector=self._dementia_detector,
            project_index=project_index,
            tool_call_count=tool_call_count,
        )


def assemble_system_prompt(
    *,
    behavior: Any,
    layered_memory: Any,
    meta_cognition: Any,
    dementia_detector: Any,
    messages: list[str],
    session_cache_hits: int = 0,
    project_index: dict[str, list[str]] | None = None,
) -> str:
    """便捷函数: 单次调用返回完整 system prompt（委托单实现）。"""
    mb = MessageBuilder(
        behavior=behavior,
        layered_memory=layered_memory,
        meta_cognition=meta_cognition,
        dementia_detector=dementia_detector,
    )
    return mb.build_adaptive_system_prompt(
        messages=messages,
        session_cache_hits=session_cache_hits,
        project_index=project_index,
    )
