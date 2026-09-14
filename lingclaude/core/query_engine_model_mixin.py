"""Q4 (2026-09-14): QueryEngine 模型切换/钉定职责拆分（行为零变化）。

从 lingclaude/core/query_engine.py 机械搬迁（AST 提取原方法体，仅缩进调整）。
消费方（QueryEngine）以多继承组合本 mixin；对外 API 面不变。
"""
from __future__ import annotations

import logging
import time
from typing import Any

from lingclaude.core.types import Result
from lingclaude.model.types import ModelConfig

logger = logging.getLogger(__name__)

class QueryEngineModelMixin:
        def switch_model(self, model_name: str) -> Result[str]:
            """P1-4: 会话中途切换模型（保留上下文，重建 provider）。

            Args:
                model_name: 目标模型名（如 gpt-4o / claude-3-5-sonnet / deepseek-chat）。

            Returns:
                Result.ok(新模型名)；失败返回错误。
            """
            from lingclaude.model.factory import create_provider
            from lingclaude.model.types import ModelConfig

            if not model_name or not model_name.strip():
                return Result.fail("model name is required", code="BAD_MODEL_NAME")

            target = model_name.strip()
            base = self._model_config or ModelConfig()
            # F12h+selector:优先显式 provider@model / provider/model 选择器（对齐
            # opencode/crush/atomcode）；无显式 provider 时按模型名反查（保留旧行为）。
            _pname, _pinfo = None, None
            _sel_err = None
            if "/" in target or "@" in target:
                _pname, _pinfo, _sel_err = self._task_router.resolve_selector(target)
                if _sel_err is not None:
                    return Result.fail(_sel_err, code="BAD_MODEL_SELECTOR")
                if _pinfo is not None:
                    target = self._task_router.parse_selector(target)[1]
            if _pinfo is None:
                _pname, _pinfo = self._task_router.find_provider_by_model(target)
            if _pinfo is not None:
                new_cfg = ModelConfig(
                    model=target,
                    api_key=_pinfo.api_key,
                    base_url=_pinfo.base_url,
                    max_tokens=base.max_tokens,
                    temperature=base.temperature,
                    system_prompt=base.system_prompt,
                )
            else:
                new_cfg = ModelConfig(
                    model=target,
                    api_key=base.api_key,
                    base_url=base.base_url,
                    max_tokens=base.max_tokens,
                    temperature=base.temperature,
                    system_prompt=base.system_prompt,
                )
            provider_result = create_provider(config=new_cfg)
            if provider_result.is_error:
                return provider_result
            self._provider = provider_result.data
            self._model_config = new_cfg
            # 同步 config.model（供 /model 显示与 tool_executor 读取）
            try:
                if hasattr(self.config, "model"):
                    self.config = self.config.__class__(
                        **{**self.config.__dict__, "model": new_cfg.model}
                    )
            except Exception:  # noqa: BLE001 — config 不可变时仅更新 _model_config
                pass
            logger.info("Switched model to %s", new_cfg.model)
            return Result.ok(new_cfg.model)

        def pin_model(self, model_name: str, ttl_seconds: int = 0) -> Result[str]:
            """Pin a model to bypass TaskRouter for subsequent requests.

            Args:
                model_name: 目标模型名（如 deepseek-v4-flash / ark-code-latest）
                ttl_seconds: 存活秒数，0 或负数表示会话级永久钉住

            Returns:
                Result.ok(钉住的模型名)；失败返回错误。
            """
            import time
            from lingclaude.model.factory import create_provider
            from lingclaude.model.types import ModelConfig

            if not model_name or not model_name.strip():
                return Result.fail("model name is required", code="BAD_MODEL_NAME")

            target = model_name.strip()
            base = self._model_config or ModelConfig()
            # selector 支持：显式 provider@model / provider/model 优先（对齐三工具）
            _pname, _pinfo = None, None
            _sel_err = None
            if "/" in target or "@" in target:
                _pname, _pinfo, _sel_err = self._task_router.resolve_selector(target)
                if _sel_err is not None:
                    return Result.fail(_sel_err, code="BAD_MODEL_SELECTOR")
                if _pinfo is not None:
                    target = self._task_router.parse_selector(target)[1]
            if _pinfo is None:
                _pname, _pinfo = self._task_router.find_provider_by_model(target)
            if _pinfo is not None:
                pinned_cfg = ModelConfig(
                    model=target,
                    api_key=_pinfo.api_key,
                    base_url=_pinfo.base_url,
                    max_tokens=base.max_tokens,
                    temperature=base.temperature,
                    system_prompt=base.system_prompt,
                )
            else:
                # 允许钉住未知模型（直接用当前配置的 key/url），由后续调用验证
                pinned_cfg = ModelConfig(
                    model=target,
                    api_key=base.api_key,
                    base_url=base.base_url,
                    max_tokens=base.max_tokens,
                    temperature=base.temperature,
                    system_prompt=base.system_prompt,
                )

            # 验证 provider 可用性
            provider_result = create_provider(config=pinned_cfg)
            if provider_result.is_error:
                return Result.fail(f"provider creation failed: {provider_result.error}", code="PROVIDER_CREATE_FAILED")

            self._pinned_model_config = pinned_cfg
            self._pinned_model_expires = time.time() + ttl_seconds if ttl_seconds > 0 else float('inf')
            logger.info("Pinned model to %s (ttl=%ss)", pinned_cfg.model, ttl_seconds if ttl_seconds > 0 else "session")
            return Result.ok(pinned_cfg.model)

        def unpin_model(self) -> Result[str]:
            """Remove pinned model, restore TaskRouter-based resolution."""
            if self._pinned_model_config is None:
                return Result.fail("no model pinned", code="NOT_PINNED")
            old = self._pinned_model_config.model
            self._pinned_model_config = None
            self._pinned_model_expires = 0.0
            logger.info("Unpinned model (was %s)", old)
            return Result.ok(old)

        def is_model_pinned(self) -> bool:
            import time
            if self._pinned_model_config is None:
                return False
            if time.time() > self._pinned_model_expires:
                # TTL 过期自动解除
                self._pinned_model_config = None
                self._pinned_model_expires = 0.0
                return False
            return True

        def get_pinned_model_name(self) -> str | None:
            if self.is_model_pinned():
                return self._pinned_model_config.model
            return None

        def _resolve_model_config(self, prompt: str) -> tuple[ModelConfig | None, Any]:
            return self._tool_executor._resolve_model_config(prompt)

        def _build_adaptive_system_prompt(self) -> str:
            from lingclaude.core.system_prompt_builder import build_adaptive_system_prompt
            return build_adaptive_system_prompt(
                behavior=self._behavior,
                layered_memory=self._layered_memory,
                meta_cognition=self._meta_cognition,
                messages=self._messages,
                session_cache_hits=self._session_cache_hits,
                dementia_detector=self._dementia_detector,
                project_index=self._project_index,
                tool_call_count=self._tool_call_count,  # R8: 触发 sub_agent 推荐提示
            )
