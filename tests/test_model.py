from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any
from unittest.mock import MagicMock, patch
from dataclasses import FrozenInstanceError

import pytest

from lingclaude.core.types import Result
from lingclaude.model.types import (
    MessageRole,
    ModelConfig,
    ModelMessage,
    ModelProvider,
    ModelResponse,
    ModelUsage,
)
from lingclaude.model.openai_provider import OpenAIProvider
from lingclaude.model.anthropic_provider import AnthropicProvider
from lingclaude.model.factory import create_provider, _detect_provider, _get_env_key
from lingclaude.model.retry import (
    GLM_MODELS,
    GlmRetryPolicy,
    is_rate_limit_error,
    handle_429,
    DEFAULT_PRIMARY_RETRY_LIMIT,
    DEFAULT_DEGRADED_CALL_THRESHOLD,
    DEFAULT_BACKOFF_BASE,
    DEFAULT_BACKOFF_MAX,
    DEFAULT_MAX_TOTAL_RETRIES,
)


# ---------------------------------------------------------------------------
# types.py
# ---------------------------------------------------------------------------

class TestMessageRole:
    def test_values(self) -> None:
        assert MessageRole.SYSTEM.value == "system"
        assert MessageRole.USER.value == "user"
        assert MessageRole.ASSISTANT.value == "assistant"
        assert MessageRole.TOOL.value == "tool"

    def test_string_comparison(self) -> None:
        assert MessageRole.USER == "user"
        assert MessageRole.SYSTEM == "system"


class TestModelMessage:
    def test_basic_creation(self) -> None:
        msg = ModelMessage(role=MessageRole.USER, content="hello")
        assert msg.role == MessageRole.USER
        assert msg.content == "hello"
        assert msg.name is None
        assert msg.tool_call_id is None

    def test_with_optional_fields(self) -> None:
        msg = ModelMessage(
            role=MessageRole.TOOL,
            content="result",
            name="bash",
            tool_call_id="call_123",
        )
        assert msg.name == "bash"
        assert msg.tool_call_id == "call_123"

    def test_to_dict_minimal(self) -> None:
        msg = ModelMessage(role=MessageRole.ASSISTANT, content="hi")
        d = msg.to_dict()
        assert d == {"role": "assistant", "content": "hi"}

    def test_to_dict_full(self) -> None:
        msg = ModelMessage(
            role=MessageRole.TOOL,
            content="ok",
            name="tool_a",
            tool_call_id="id_1",
        )
        d = msg.to_dict()
        assert d == {
            "role": "tool",
            "content": "ok",
            "name": "tool_a",
            "tool_call_id": "id_1",
        }

    def test_frozen(self) -> None:
        msg = ModelMessage(role=MessageRole.USER, content="x")
        with pytest.raises(FrozenInstanceError):
            msg.content = "y"  # type: ignore[misc]


class TestModelUsage:
    def test_defaults(self) -> None:
        u = ModelUsage()
        assert u.input_tokens == 0
        assert u.output_tokens == 0

    def test_to_dict(self) -> None:
        u = ModelUsage(input_tokens=100, output_tokens=50)
        assert u.to_dict() == {"input_tokens": 100, "output_tokens": 50}

    def test_frozen(self) -> None:
        u = ModelUsage()
        with pytest.raises(FrozenInstanceError):
            u.input_tokens = 10  # type: ignore[misc]


class TestModelResponse:
    def test_creation(self) -> None:
        r = ModelResponse(
            content="answer",
            model="gpt-4o",
            usage=ModelUsage(input_tokens=10, output_tokens=20),
        )
        assert r.content == "answer"
        assert r.model == "gpt-4o"
        assert r.finish_reason == "stop"
        assert r.raw is None

    def test_frozen(self) -> None:
        r = ModelResponse(content="a", model="m", usage=ModelUsage())
        with pytest.raises(FrozenInstanceError):
            r.content = "b"  # type: ignore[misc]


class TestModelConfig:
    def test_defaults(self) -> None:
        cfg = ModelConfig()
        assert cfg.model == "gpt-4o"
        assert cfg.api_key == ""
        assert cfg.max_tokens == 4096
        assert cfg.temperature == 0.7

    def test_from_dict_full(self) -> None:
        raw = {
            "model": "claude-sonnet-4-20250514",
            "api_key": "sk-test",
            "base_url": "https://custom.api.com",
            "max_tokens": 8192,
            "temperature": 0.3,
            "system_prompt": "custom prompt",
        }
        cfg = ModelConfig.from_dict(raw)
        assert cfg.model == "claude-sonnet-4-20250514"
        assert cfg.api_key == "sk-test"
        assert cfg.base_url == "https://custom.api.com"
        assert cfg.max_tokens == 8192
        assert cfg.temperature == 0.3
        assert cfg.system_prompt == "custom prompt"

    def test_from_dict_defaults(self) -> None:
        cfg = ModelConfig.from_dict({})
        assert cfg.model == "gpt-4o"
        assert cfg.api_key == ""

    def test_frozen(self) -> None:
        cfg = ModelConfig()
        with pytest.raises(FrozenInstanceError):
            cfg.api_key = "new"  # type: ignore[misc]


class TestModelProviderABC:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            ModelProvider()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# openai_provider.py
# ---------------------------------------------------------------------------

class TestOpenAIProvider:
    def test_build_request_body(self) -> None:
        provider = OpenAIProvider(ModelConfig(api_key="sk-test"))
        messages = (
            ModelMessage(role=MessageRole.USER, content="hello"),
            ModelMessage(role=MessageRole.ASSISTANT, content="hi there"),
        )
        body = provider._build_request_body(messages, ModelConfig(api_key="sk-test"))

        assert body["model"] == "gpt-4o"
        assert len(body["messages"]) == 3  # system + 2 user msgs
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][1]["role"] == "user"
        assert body["messages"][1]["content"] == "hello"
        assert body["max_tokens"] == 4096
        assert body["temperature"] == 0.7

    def test_build_request_body_no_system_prompt(self) -> None:
        cfg = ModelConfig(system_prompt="")
        provider = OpenAIProvider(cfg)
        messages = (ModelMessage(role=MessageRole.USER, content="test"),)
        body = provider._build_request_body(messages, cfg)
        assert body["messages"][0]["role"] == "user"

    def test_parse_response(self) -> None:
        provider = OpenAIProvider()
        raw_data = {
            "choices": [
                {
                    "message": {"content": "Hello! How can I help?"},
                    "finish_reason": "stop",
                }
            ],
            "model": "gpt-4o-2024-08-06",
            "usage": {"prompt_tokens": 15, "completion_tokens": 8},
        }
        result = provider._parse_response(raw_data, "gpt-4o")

        assert result.is_ok
        resp = result.data
        assert resp.content == "Hello! How can I help?"
        assert resp.model == "gpt-4o-2024-08-06"
        assert resp.usage.input_tokens == 15
        assert resp.usage.output_tokens == 8
        assert resp.finish_reason == "stop"

    def test_parse_response_empty_choices(self) -> None:
        provider = OpenAIProvider()
        result = provider._parse_response({"choices": []}, "gpt-4o")
        assert result.is_error
        assert "空 choices" in result.error

    def test_complete_no_api_key(self) -> None:
        provider = OpenAIProvider(ModelConfig(api_key=""))
        result = provider.complete(
            (ModelMessage(role=MessageRole.USER, content="hi"),)
        )
        assert result.is_error
        assert "API key" in result.error

    def test_complete_mock_success(self) -> None:
        provider = OpenAIProvider(ModelConfig(api_key="sk-test"))
        mock_response_data = {
            "choices": [{"message": {"content": "test reply"}, "finish_reason": "stop"}],
            "model": "gpt-4o",
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_response_data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = provider.complete(
                (ModelMessage(role=MessageRole.USER, content="hello"),)
            )

        assert result.is_ok
        assert result.data.content == "test reply"

    def test_complete_mock_http_error(self) -> None:
        provider = OpenAIProvider(ModelConfig(api_key="sk-test"))
        error_resp = MagicMock()
        error_resp.read.return_value = b'{"error": "bad"}'

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                url="http://test",
                code=401,
                msg="Unauthorized",
                hdrs=None,
                fp=error_resp,
            ),
        ):
            result = provider.complete(
                (ModelMessage(role=MessageRole.USER, content="hi"),)
            )

        assert result.is_error
        assert "401" in result.error

    def test_count_tokens(self) -> None:
        provider = OpenAIProvider(ModelConfig(api_key="sk-test"))
        text = "hello world"
        count = provider.count_tokens(text)
        assert isinstance(count, int)
        assert count > 0


# ---------------------------------------------------------------------------
# anthropic_provider.py
# ---------------------------------------------------------------------------

class TestAnthropicProvider:
    def test_build_request_body(self) -> None:
        provider = AnthropicProvider(
            ModelConfig(
                model="claude-sonnet-4-20250514",
                api_key="sk-ant-test",
                system_prompt="You are helpful.",
            )
        )
        messages = (
            ModelMessage(role=MessageRole.USER, content="hello"),
        )
        system_prompt, msg_dicts = provider._build_request_body(
            messages, ModelConfig(
                model="claude-sonnet-4-20250514",
                api_key="sk-ant-test",
                system_prompt="You are helpful.",
            )
        )

        assert system_prompt == "You are helpful."
        assert len(msg_dicts) == 1
        assert msg_dicts[0]["role"] == "user"

    def test_build_request_body_system_in_messages(self) -> None:
        provider = AnthropicProvider()
        messages = (
            ModelMessage(role=MessageRole.SYSTEM, content="sys"),
            ModelMessage(role=MessageRole.USER, content="hi"),
        )
        system_prompt, msg_dicts = provider._build_request_body(
            messages, ModelConfig(system_prompt="")
        )
        assert system_prompt == "sys"
        assert len(msg_dicts) == 1
        assert msg_dicts[0]["role"] == "user"

    def test_parse_response(self) -> None:
        provider = AnthropicProvider()
        raw_data = {
            "content": [{"type": "text", "text": "Hello from Claude!"}],
            "model": "claude-sonnet-4-20250514",
            "usage": {"input_tokens": 20, "output_tokens": 10},
            "stop_reason": "end_turn",
        }
        result = provider._parse_response(raw_data, "claude-sonnet-4-20250514")

        assert result.is_ok
        assert result.data.content == "Hello from Claude!"
        assert result.data.usage.input_tokens == 20
        assert result.data.finish_reason == "end_turn"

    def test_parse_response_multiple_blocks(self) -> None:
        provider = AnthropicProvider()
        raw_data = {
            "content": [
                {"type": "text", "text": "Part 1 "},
                {"type": "text", "text": "Part 2"},
            ],
            "model": "claude-sonnet-4-20250514",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        result = provider._parse_response(raw_data, "claude-sonnet-4-20250514")
        assert result.is_ok
        assert result.data.content == "Part 1 Part 2"

    def test_parse_response_empty_content(self) -> None:
        provider = AnthropicProvider()
        result = provider._parse_response({"content": []}, "claude-sonnet-4-20250514")
        assert result.is_error
        assert "空 content" in result.error

    def test_complete_no_api_key(self) -> None:
        provider = AnthropicProvider(ModelConfig(api_key=""))
        result = provider.complete(
            (ModelMessage(role=MessageRole.USER, content="hi"),)
        )
        assert result.is_error
        assert "API key" in result.error

    def test_complete_mock_success(self) -> None:
        provider = AnthropicProvider(
            ModelConfig(model="claude-sonnet-4-20250514", api_key="sk-ant-test")
        )
        mock_response_data = {
            "content": [{"type": "text", "text": "Claude reply"}],
            "model": "claude-sonnet-4-20250514",
            "usage": {"input_tokens": 8, "output_tokens": 4},
            "stop_reason": "end_turn",
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_response_data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = provider.complete(
                (ModelMessage(role=MessageRole.USER, content="hello"),)
            )

        assert result.is_ok
        assert result.data.content == "Claude reply"

    def test_count_tokens(self) -> None:
        provider = AnthropicProvider()
        assert provider.count_tokens("12345678") == 2  # 8 // 4


# ---------------------------------------------------------------------------
# factory.py
# ---------------------------------------------------------------------------

class TestDetectProvider:
    def test_gpt_models(self) -> None:
        assert _detect_provider(ModelConfig(model="gpt-4o")) == "openai"
        assert _detect_provider(ModelConfig(model="gpt-3.5-turbo")) == "openai"

    def test_o_series(self) -> None:
        assert _detect_provider(ModelConfig(model="o1-preview")) == "openai"
        assert _detect_provider(ModelConfig(model="o3-mini")) == "openai"
        assert _detect_provider(ModelConfig(model="o4-mini")) == "openai"

    def test_claude_models(self) -> None:
        assert _detect_provider(ModelConfig(model="claude-sonnet-4-20250514")) == "anthropic"
        assert _detect_provider(ModelConfig(model="claude-3-5-sonnet")) == "anthropic"

    def test_unknown_defaults_openai(self) -> None:
        assert _detect_provider(ModelConfig(model="llama-3")) == "openai"


class TestGetEnvKey:
    def test_openai(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-from-env"}):
            assert _get_env_key("openai") == "sk-from-env"

    def test_anthropic(self) -> None:
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-env"}):
            assert _get_env_key("anthropic") == "sk-ant-env"

    def test_unknown(self) -> None:
        assert _get_env_key("unknown") == ""

    def test_missing_env(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = _get_env_key("openai")
            assert result == "" or result.startswith("sk-")


class TestCreateProvider:
    def test_openai_by_name(self) -> None:
        result = create_provider(
            config={"api_key": "sk-test"},
            provider_name="openai",
        )
        assert result.is_ok
        assert isinstance(result.data, OpenAIProvider)

    def test_anthropic_by_name(self) -> None:
        result = create_provider(
            config={"model": "claude-sonnet-4-20250514", "api_key": "sk-ant"},
            provider_name="anthropic",
        )
        assert result.is_ok
        assert isinstance(result.data, AnthropicProvider)

    def test_auto_detect_gpt(self) -> None:
        result = create_provider(
            config={"model": "gpt-4o", "api_key": "sk-test"},
        )
        assert result.is_ok
        assert isinstance(result.data, OpenAIProvider)

    def test_auto_detect_claude(self) -> None:
        result = create_provider(
            config={"model": "claude-sonnet-4-20250514", "api_key": "sk-ant"},
        )
        assert result.is_ok
        assert isinstance(result.data, AnthropicProvider)

    def test_env_key_fallback(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-env"}):
            result = create_provider(provider_name="openai")
            assert result.is_ok
            assert isinstance(result.data, OpenAIProvider)

    def test_unknown_provider(self) -> None:
        result = create_provider(provider_name="ollama")
        assert result.is_error
        assert "未知" in result.error

    def test_dict_config(self) -> None:
        result = create_provider(
            config={"model": "gpt-4o", "api_key": "sk-123"},
        )
        assert result.is_ok

    def test_model_config_object(self) -> None:
        result = create_provider(
            config=ModelConfig(model="gpt-4o", api_key="sk-obj"),
        )
        assert result.is_ok

    def test_none_config(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-env"}):
            result = create_provider(provider_name="openai")
            assert result.is_ok


# ---------------------------------------------------------------------------
# retry.py
# ---------------------------------------------------------------------------


class TestGlmRetryPolicy:
    def test_default_initialization(self) -> None:
        policy = GlmRetryPolicy()
        assert policy.current_model == "glm-5.1"
        assert policy.is_primary is True
        assert policy.is_degraded is False
        assert policy.primary_retry_limit == DEFAULT_PRIMARY_RETRY_LIMIT
        assert policy.degraded_call_threshold == DEFAULT_DEGRADED_CALL_THRESHOLD

    def test_custom_initialization(self) -> None:
        custom_models = ["model-a", "model-b"]
        policy = GlmRetryPolicy(
            models=custom_models,
            primary_retry_limit=5,
            degraded_call_threshold=20,
            backoff_base=10.0,
            backoff_max=60.0,
        )
        assert policy.current_model == "model-a"
        assert policy.primary_retry_limit == 5
        assert policy.degraded_call_threshold == 20
        assert policy.backoff_base == 10.0
        assert policy.backoff_max == 60.0

    def test_get_next_model(self) -> None:
        policy = GlmRetryPolicy()
        assert policy.get_next_model() == "glm-5-turbo"
        policy._model_index = len(policy.models) - 1
        assert policy.get_next_model() is None

    def test_record_success_primary(self) -> None:
        policy = GlmRetryPolicy()
        policy._primary_retry_count = 2
        policy.record_success()
        assert policy._primary_retry_count == 0

    def test_record_success_degraded(self) -> None:
        policy = GlmRetryPolicy()
        policy._model_index = 2
        assert policy.is_degraded
        policy.record_success()
        # Should not reset counters when degraded
        assert policy._model_index == 2

    def test_record_failure_primary(self) -> None:
        policy = GlmRetryPolicy()
        policy.record_failure()
        assert policy._primary_retry_count == 1
        assert policy._primary_last_retry > 0

    def test_record_failure_degraded(self) -> None:
        policy = GlmRetryPolicy()
        policy._model_index = 1
        initial_count = policy._degraded_call_count
        policy.record_failure()
        assert policy._degraded_call_count == initial_count + 1

    def test_should_degrade(self) -> None:
        policy = GlmRetryPolicy(primary_retry_limit=3)
        assert policy.should_degrade() is False
        policy._primary_retry_count = 3
        assert policy.should_degrade() is True

    def test_degrade(self) -> None:
        policy = GlmRetryPolicy()
        new_model = policy.degrade()
        assert new_model == "glm-5-turbo"
        assert policy.is_degraded is True
        assert policy.current_model == "glm-5-turbo"

    def test_degrade_last_model(self) -> None:
        policy = GlmRetryPolicy()
        policy._model_index = len(policy.models) - 1
        new_model = policy.degrade()
        assert new_model is None

    def test_should_retry_primary_count_threshold(self) -> None:
        policy = GlmRetryPolicy(degraded_call_threshold=10)
        policy._model_index = 1
        assert policy.should_retry_primary() is False
        policy._degraded_call_count = 10
        assert policy.should_retry_primary() is True

    def test_should_retry_primary_time_threshold(self) -> None:
        policy = GlmRetryPolicy(degraded_time_threshold=0.1)
        policy._model_index = 1
        policy._degraded_since = time.time() - 0.2
        assert policy.should_retry_primary() is True

    def test_reset_to_primary(self) -> None:
        policy = GlmRetryPolicy()
        policy._model_index = 3
        policy._primary_retry_count = 5
        policy._degraded_call_count = 10
        policy._degraded_since = time.time()

        new_model = policy.reset_to_primary()
        assert new_model == "glm-5.1"
        assert policy.is_primary is True
        assert policy._primary_retry_count == 0
        assert policy._degraded_call_count == 0
        assert policy._degraded_since == 0.0

    def test_get_backoff(self) -> None:
        policy = GlmRetryPolicy(backoff_base=5.0, backoff_max=30.0)
        assert policy.get_backoff(1) == 5.0
        assert policy.get_backoff(2) == 10.0
        assert policy.get_backoff(5) == 25.0
        assert policy.get_backoff(10) == 30.0  # Capped at max

    def test_get_snapshot(self) -> None:
        policy = GlmRetryPolicy()
        snapshot = policy.get_snapshot()
        assert snapshot.current_model == "glm-5.1"
        assert snapshot.model_index == 0
        assert snapshot.primary_retry_count == 0
        assert snapshot.is_degraded is False
        assert snapshot.degraded_since is None

    def test_get_snapshot_degraded(self) -> None:
        policy = GlmRetryPolicy()
        policy._model_index = 2
        policy._degraded_since = time.time()
        snapshot = policy.get_snapshot()
        assert snapshot.is_degraded is True
        assert snapshot.model_index == 2
        assert snapshot.degraded_since is not None

    def test_reset(self) -> None:
        policy = GlmRetryPolicy()
        policy._model_index = 3
        policy._primary_retry_count = 5
        policy._degraded_call_count = 10
        policy._degraded_since = time.time()

        policy.reset()
        assert policy.is_primary is True
        assert policy._primary_retry_count == 0
        assert policy._degraded_call_count == 0
        assert policy._degraded_since == 0.0

    def test_configure_primary_glm_model(self) -> None:
        policy = GlmRetryPolicy()
        assert policy.current_model == "glm-5.1"
        policy.configure_primary("glm-4.7")
        assert policy.current_model == "glm-4.7"
        assert policy.models[0] == "glm-4.7"
        assert "glm-4.7" not in policy.models[1:]

    def test_configure_primary_non_glm_ignored(self) -> None:
        policy = GlmRetryPolicy()
        policy.configure_primary("gpt-4o")
        assert policy.current_model == "glm-5.1"

    def test_configure_primary_empty_ignored(self) -> None:
        policy = GlmRetryPolicy()
        policy.configure_primary("")
        assert policy.current_model == "glm-5.1"

    def test_configure_primary_same_model_noop(self) -> None:
        policy = GlmRetryPolicy()
        original_models = policy.models.copy()
        policy.configure_primary("glm-5.1")
        assert policy.models == original_models

    def test_configure_primary_new_glm_model_inserted(self) -> None:
        policy = GlmRetryPolicy()
        policy.configure_primary("glm-future-99")
        assert policy.current_model == "glm-future-99"
        assert policy.models[0] == "glm-future-99"
        assert "glm-5.1" in policy.models

    def test_openai_provider_configures_primary(self) -> None:
        provider = OpenAIProvider(ModelConfig(model="glm-4.7", api_key="sk-test"))
        assert provider._retry_policy.current_model == "glm-4.7"

    def test_openai_provider_non_glm_keeps_default(self) -> None:
        provider = OpenAIProvider(ModelConfig(model="gpt-4o", api_key="sk-test"))
        assert provider._retry_policy.current_model == "glm-5.1"


class TestIsRateLimitError:
    def test_429_code(self) -> None:
        assert is_rate_limit_error("HTTP 429 Too Many Requests") is True
        assert is_rate_limit_error("Error 429 rate limit exceeded") is True

    def test_rate_limit_text(self) -> None:
        assert is_rate_limit_error("rate_limit exceeded") is True
        assert is_rate_limit_error("Rate limit error") is True
        assert is_rate_limit_error("RATE_LIMIT_ERROR") is True

    def test_too_many_requests(self) -> None:
        assert is_rate_limit_error("Too Many Requests") is True
        assert is_rate_limit_error("too many requests") is True

    def test_chinese_markers(self) -> None:
        assert is_rate_limit_error("模型访问量过大") is True
        assert is_rate_limit_error("服务繁忙") is True
        assert is_rate_limit_error("模型正在忙") is True

    def test_rpm_limit(self) -> None:
        assert is_rate_limit_error("requests per minute exceeded") is True
        assert is_rate_limit_error("RPM limit reached") is True

    def test_non_rate_limit_error(self) -> None:
        assert is_rate_limit_error("HTTP 500 Internal Server Error") is False
        assert is_rate_limit_error("Authentication failed") is False
        assert is_rate_limit_error("Invalid API key") is False


class TestHandle429:
    def test_primary_no_degrade(self) -> None:
        policy = GlmRetryPolicy(primary_retry_limit=3)
        policy._primary_retry_count = 1  # Less than limit
        new_model = handle_429(policy, 1)
        # After record_failure, count becomes 2, still not at limit
        assert new_model == "glm-5.1"
        assert policy.is_primary is True

    def test_primary_degrade(self) -> None:
        policy = GlmRetryPolicy(primary_retry_limit=3)
        policy._primary_retry_count = 3
        new_model = handle_429(policy, 1)
        assert new_model == "glm-5-turbo"
        assert policy.is_degraded is True

    def test_degraded_retry_primary(self) -> None:
        policy = GlmRetryPolicy(degraded_call_threshold=5)
        policy._model_index = 1
        policy._degraded_call_count = 5
        new_model = handle_429(policy, 1)
        assert new_model == "glm-5.1"
        assert policy.is_primary is True

    def test_degraded_stay_degraded(self) -> None:
        policy = GlmRetryPolicy(degraded_call_threshold=10)
        policy._model_index = 1
        policy._degraded_call_count = 3
        new_model = handle_429(policy, 1)
        assert new_model == "glm-5-turbo"
        assert policy.is_degraded is True


class TestOpenAIProvider429Retry:
    def test_429_retry_with_backoff(self) -> None:
        provider = OpenAIProvider(ModelConfig(model="glm-5.1", api_key="sk-test"))

        error_resp = MagicMock()
        error_resp.read.return_value = b'{"error": "HTTP 429 rate limit exceeded"}'

        # First 2 calls fail with 429, third succeeds
        call_count = [0]

        def mock_urlopen_side_effect(*args: Any, **kwargs: Any) -> Any:
            call_count[0] += 1
            if call_count[0] <= 2:
                raise urllib.error.HTTPError(
                    url="http://test",
                    code=429,
                    msg="Too Many Requests",
                    hdrs=None,
                    fp=error_resp,
                )
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({
                "choices": [{"message": {"content": "success"}, "finish_reason": "stop"}],
                "model": "glm-5.1",
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            }).encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        with patch("lingclaude.model.openai_provider.time.sleep"), \
             patch("urllib.request.urlopen", side_effect=mock_urlopen_side_effect):
            result = provider.complete(
                (ModelMessage(role=MessageRole.USER, content="hello"),)
            )

        assert result.is_ok
        assert result.data.content == "success"
        assert call_count[0] == 3

    def test_429_triggers_degradation(self) -> None:
        provider = OpenAIProvider(ModelConfig(model="glm-5.1", api_key="sk-test"))

        error_resp = MagicMock()
        error_resp.read.return_value = b'{"error": "HTTP 429 Too Many Requests"}'

        call_count = [0]

        def mock_urlopen_side_effect(*args: Any, **kwargs: Any) -> Any:
            call_count[0] += 1
            if call_count[0] <= DEFAULT_PRIMARY_RETRY_LIMIT:
                raise urllib.error.HTTPError(
                    url="http://test",
                    code=429,
                    msg="Too Many Requests",
                    hdrs=None,
                    fp=error_resp,
                )
            # After degradation, succeed
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({
                "choices": [{"message": {"content": "degraded success"}, "finish_reason": "stop"}],
                "model": "glm-5-turbo",
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            }).encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        with patch("lingclaude.model.openai_provider.time.sleep"), \
             patch("urllib.request.urlopen", side_effect=mock_urlopen_side_effect):
            result = provider.complete(
                (ModelMessage(role=MessageRole.USER, content="hello"),)
            )

        assert result.is_ok
        assert result.data.content == "degraded success"
        assert provider._retry_policy.is_degraded is True

    def test_non_429_error_no_retry(self) -> None:
        provider = OpenAIProvider(ModelConfig(model="glm-5.1", api_key="sk-test"))

        error_resp = MagicMock()
        error_resp.read.return_value = b'{"error": "Unauthorized"}'

        call_count = [0]

        def mock_urlopen_side_effect(*args: Any, **kwargs: Any) -> Any:
            call_count[0] += 1
            raise urllib.error.HTTPError(
                url="http://test",
                code=401,
                msg="Unauthorized",
                hdrs=None,
                fp=error_resp,
            )

        with patch("urllib.request.urlopen", side_effect=mock_urlopen_side_effect):
            result = provider.complete(
                (ModelMessage(role=MessageRole.USER, content="hello"),)
            )

        assert result.is_error
        assert "401" in result.error
        assert call_count[0] == 1  # Should not retry

    def test_max_retries_exceeded(self) -> None:
        provider = OpenAIProvider(ModelConfig(model="glm-5.1", api_key="sk-test"))

        error_resp = MagicMock()
        error_resp.read.return_value = b'{"error": "HTTP 429 rate limit"}'

        def mock_urlopen_side_effect(*args: Any, **kwargs: Any) -> Any:
            raise urllib.error.HTTPError(
                url="http://test",
                code=429,
                msg="Too Many Requests",
                hdrs=None,
                fp=error_resp,
            )

        with patch("lingclaude.model.openai_provider.time.sleep"), \
             patch("urllib.request.urlopen", side_effect=mock_urlopen_side_effect):
            result = provider.complete(
                (ModelMessage(role=MessageRole.USER, content="hello"),)
            )

        assert result.is_error
        assert "已重试" in result.error

    def test_non_glm_model_unchanged(self) -> None:
        provider = OpenAIProvider(ModelConfig(model="gpt-4o", api_key="sk-test"))

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": "response"}, "finish_reason": "stop"}],
            "model": "gpt-4o",
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = provider.complete(
                (ModelMessage(role=MessageRole.USER, content="hello"),)
            )

        assert result.is_ok
        assert provider._retry_policy.current_model == "glm-5.1"
        assert provider._retry_policy.models[0] == "glm-5.1"

    def test_success_resets_primary_counter(self) -> None:
        provider = OpenAIProvider(ModelConfig(model="glm-5.1", api_key="sk-test"))

        # First call fails once, then succeeds
        call_count = [0]

        def mock_urlopen_side_effect(*args: Any, **kwargs: Any) -> Any:
            call_count[0] += 1
            if call_count[0] == 1:
                error_resp = MagicMock()
                error_resp.read.return_value = b'{"error": "HTTP 429"}'
                raise urllib.error.HTTPError(
                    url="http://test",
                    code=429,
                    msg="Too Many Requests",
                    hdrs=None,
                    fp=error_resp,
                )
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({
                "choices": [{"message": {"content": "success"}, "finish_reason": "stop"}],
                "model": "glm-5.1",
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            }).encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        with patch("lingclaude.model.openai_provider.time.sleep"), \
             patch("urllib.request.urlopen", side_effect=mock_urlopen_side_effect):
            result = provider.complete(
                (ModelMessage(role=MessageRole.USER, content="hello"),)
            )

        assert result.is_ok
        assert provider._retry_policy._primary_retry_count == 0


# ---------------------------------------------------------------------------
# Integration: QueryEngine with model provider
# ---------------------------------------------------------------------------

class TestQueryEngineWithProvider:
    def test_engine_calls_provider(self) -> None:
        from lingclaude.core.query_engine import QueryEngine

        mock_provider = MagicMock()
        mock_provider.complete.return_value = Result.ok(
            ModelResponse(
                content="AI response",
                model="gpt-4o",
                usage=ModelUsage(input_tokens=10, output_tokens=5),
            )
        )

        engine = QueryEngine(model_provider=mock_provider)
        result = engine.submit("hello")

        assert result.output == "AI response"
        assert mock_provider.complete.called

    def test_engine_handles_provider_error(self) -> None:
        from lingclaude.core.query_engine import QueryEngine

        mock_provider = MagicMock()
        mock_provider.complete.return_value = Result.fail("API 不可用")

        engine = QueryEngine(model_provider=mock_provider)
        result = engine.submit("hello")

        assert "模型调用失败" in result.output
        assert result.stop_reason.value == "consecutive_failure"

    def test_engine_without_provider_fallback(self) -> None:
        from lingclaude.core.query_engine import QueryEngine

        engine = QueryEngine()
        result = engine.submit("test prompt")

        assert "test prompt" in result.output
        assert result.stop_reason.value == "completed"

    def test_from_config_file_no_provider(self) -> None:
        from lingclaude.core.query_engine import QueryEngine

        engine_result = QueryEngine.from_config_file()
        assert engine_result.is_ok
        result = engine_result.data.submit("hello")
        assert result.stop_reason.value == "completed"

    def test_from_config_file_with_model_config(self, tmp_path: object) -> None:
        import pathlib

        from lingclaude.core.query_engine import QueryEngine

        config_content = """
engine:
  max_turns: 4
model:
  provider: openai
  model: gpt-4o
  api_key: sk-test-key
"""
        cfg_path = pathlib.Path(str(tmp_path)) / "config.yaml"
        cfg_path.write_text(config_content)

        engine_result = QueryEngine.from_config_file(str(cfg_path))
        assert engine_result.is_ok
        assert engine_result.data.config.max_turns == 4


class TestAnthropicStreamComplete:
    """Anthropic stream_complete with mocked SSE"""

    def _make_sse_response(
        self, chunks: list[dict[str, Any]], status: int = 200,
    ) -> MagicMock:
        lines: list[bytes] = []
        for chunk in chunks:
            lines.append(f"data: {json.dumps(chunk)}".encode("utf-8"))
            lines.append(b"\n")
        lines.append(b"\n")
        lines.append(b"")

        def _readline() -> bytes:
            if lines:
                return lines.pop(0)
            raise StopIteration

        mock_resp = MagicMock()
        mock_resp.status = status
        mock_resp.readline = MagicMock(side_effect=_readline)
        mock_resp.read = MagicMock(return_value=b"server error")
        return mock_resp

    @patch("lingclaude.model.anthropic_provider.http.client.HTTPSConnection")
    def test_stream_text_delta(self, mock_conn_cls: MagicMock) -> None:
        provider = AnthropicProvider(ModelConfig(
            model="claude-sonnet-4-20250514", api_key="sk-test",
        ))
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn

        sse_events = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 10, "output_tokens": 0}}},
            {"type": "content_block_start", "content_block": {"type": "text"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " world"}},
            {"type": "content_block_stop"},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 5}},
            {"type": "message_stop"},
        ]
        mock_conn.getresponse.return_value = self._make_sse_response(sse_events)

        events = list(provider.stream_complete(
            (ModelMessage(role=MessageRole.USER, content="hi"),),
        ))

        text_deltas = [e for e in events if e["type"] == "text_delta"]
        assert len(text_deltas) == 2
        assert text_deltas[0]["text"] == "Hello"
        assert text_deltas[1]["text"] == " world"

        finish_events = [e for e in events if e["type"] == "finish"]
        assert len(finish_events) == 1
        assert finish_events[0]["reason"] == "end_turn"

    @patch("lingclaude.model.anthropic_provider.http.client.HTTPSConnection")
    def test_stream_tool_calls(self, mock_conn_cls: MagicMock) -> None:
        provider = AnthropicProvider(ModelConfig(
            model="claude-sonnet-4-20250514", api_key="sk-test",
        ))
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn

        sse_events = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 10, "output_tokens": 0}}},
            {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "tu1", "name": "read_file"}},
            {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": '{"path": '}},
            {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": '"test.py"}'}},
            {"type": "content_block_stop"},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 20}},
            {"type": "message_stop"},
        ]
        mock_conn.getresponse.return_value = self._make_sse_response(sse_events)

        events = list(provider.stream_complete(
            (ModelMessage(role=MessageRole.USER, content="read test.py"),),
            tools=({"name": "read_file", "description": "Read a file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}},),
        ))

        tc_events = [e for e in events if e["type"] == "tool_call_complete"]
        assert len(tc_events) == 1
        assert tc_events[0]["name"] == "read_file"
        assert tc_events[0]["arguments"] == '{"path": "test.py"}'

        finish_events = [e for e in events if e["type"] == "finish"]
        assert finish_events[0]["reason"] == "tool_calls"

    def test_stream_no_api_key(self) -> None:
        provider = AnthropicProvider(ModelConfig(model="claude-sonnet-4-20250514"))
        events = list(provider.stream_complete(
            (ModelMessage(role=MessageRole.USER, content="hi"),),
        ))
        assert len(events) == 1
        assert events[0]["type"] == "error"

    @patch("lingclaude.model.anthropic_provider.http.client.HTTPSConnection")
    def test_stream_http_error(self, mock_conn_cls: MagicMock) -> None:
        provider = AnthropicProvider(ModelConfig(
            model="claude-sonnet-4-20250514", api_key="sk-test",
        ))
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn

        mock_conn.getresponse.return_value = self._make_sse_response([], status=429)
        events = list(provider.stream_complete(
            (ModelMessage(role=MessageRole.USER, content="hi"),),
        ))
        assert any(e["type"] == "error" and "429" in e["error"] for e in events)
