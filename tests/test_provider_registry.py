"""P1-3 验证: provider 注册表替换 if/elif 硬编码链（灵元 R2）。

覆盖:
  - openai / anthropic 成功路径（注册表创建）
  - 未知 provider fail 路径（原 factory 语义保留）
  - 运行时热注册（register 即生效，新 create 用新构造器）
  - local provider 构造器适配（签名不同）
"""
from __future__ import annotations

import pytest

from lingclaude.model.factory import create_provider
from lingclaude.model.provider_registry import ProviderRegistry
from lingclaude.model.types import ModelConfig, ModelProvider


def _cfg(model: str, base_url: str) -> ModelConfig:
    return ModelConfig(model=model, api_key="test-key", base_url=base_url)


class _FakeProvider(ModelProvider):
    def __init__(self, config: ModelConfig | None = None) -> None:
        self._config = config
        self.name = "fake"

    def complete(self, *args, **kwargs):  # pragma: no cover — 测试桩
        raise NotImplementedError

    async def acomplete(self, *args, **kwargs):  # pragma: no cover — 测试桩
        raise NotImplementedError

    def count_tokens(self, *args, **kwargs):  # pragma: no cover — 测试桩
        return 0


@pytest.fixture(autouse=True)
def _isolate_registry():
    """每个用例独立注册表，避免热注册污染。"""
    ProviderRegistry.reset()
    # 重建内置（与模块加载一致）
    from lingclaude.model.provider_registry import _register_builtins
    _register_builtins()
    yield
    ProviderRegistry.reset()
    _register_builtins()  # 恢复内置，避免污染后续测试文件


def test_openai_via_registry():
    r = create_provider(_cfg("gpt-4o", "https://api.openai.com/v1"))
    assert r.is_ok
    assert type(r.data).__name__ == "OpenAIProvider"


def test_anthropic_via_registry():
    r = create_provider(_cfg("claude-3-5-sonnet", "https://api.anthropic.com"))
    assert r.is_ok
    assert type(r.data).__name__ == "AnthropicProvider"


def test_unknown_provider_fail_message():
    r = create_provider(_cfg("x", "https://x"), provider_name="nosuch")
    assert r.is_error
    assert "openai" in r.error and "anthropic" in r.error
    assert "nosuch" in r.error


def test_hot_register_takes_effect():
    # 运行时注册新 provider，无需改 factory
    ProviderRegistry.register("fake", _FakeProvider)
    r = create_provider(_cfg("f", "https://f"), provider_name="fake")
    assert r.is_ok
    assert type(r.data).__name__ == "_FakeProvider"


def test_local_provider_adapter():
    # local 构造签名不同（model_path），注册表构造器适配
    r = create_provider(_cfg("some-model", ""), provider_name="local")
    assert r.is_ok
    assert type(r.data).__name__ == "LocalModelProvider"


def test_detect_provider_default_openai():
    # 未指定 provider_name 时按模型名探测 → openai（原 _detect_provider 行为）
    r = create_provider(_cfg("gpt-4o", "https://api.openai.com/v1"))
    assert r.is_ok
    assert type(r.data).__name__ == "OpenAIProvider"


def test_glm_provider_registered_and_created():
    """启动默认 glm-5.3-flash@glm: glm provider 必须在注册表且可创建。

    J 任务: glm-5.3-flash@glm 为启动时默认值 — provider 'glm' 复用
    OpenAIProvider + 智谱 Coding 套餐端点（与 task_router 同源）。
    """
    assert "glm" in ProviderRegistry.registered_names()

    r = create_provider(_cfg("glm-5.3-flash", ""), provider_name="glm")
    assert r.is_ok
    assert type(r.data).__name__ == "OpenAIProvider"
    # 未显式 base_url 时，glm 工厂补齐智谱 Coding 端点
    coding_url = "https://open.bigmodel.cn/api/coding/paas/v4"
    assert r.data._config.base_url == coding_url


def test_glm_provider_respects_explicit_base_url():
    """显式传入 base_url 时，glm 工厂不覆盖（透传自定义端点）。"""
    r = create_provider(_cfg("glm-5.3-flash", "https://custom.example.com/v1"), provider_name="glm")
    assert r.is_ok
    assert r.data._config.base_url == "https://custom.example.com/v1"


def test_glm_provider_keeps_model_and_key():
    """glm provider 透传 model 与 api_key，不吞字段。"""
    coding_url = "https://open.bigmodel.cn/api/coding/paas/v4"
    r = create_provider(_cfg("glm-5.3-flash", coding_url), provider_name="glm")
    assert r.is_ok
    assert r.data._config.model == "glm-5.3-flash"
    assert r.data._config.api_key == "test-key"
