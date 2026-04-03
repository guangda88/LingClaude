from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
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
            assert _get_env_key("openai") == ""


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
        assert "API 不可用" in result.output

    def test_engine_without_provider_fallback(self) -> None:
        from lingclaude.core.query_engine import QueryEngine

        engine = QueryEngine()
        result = engine.submit("test prompt")

        assert "test prompt" in result.output
        assert result.stop_reason.value == "completed"

    def test_from_config_file_no_provider(self) -> None:
        from lingclaude.core.query_engine import QueryEngine

        engine = QueryEngine.from_config_file()
        result = engine.submit("hello")
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

        engine = QueryEngine.from_config_file(str(cfg_path))
        assert engine.config.max_turns == 4
