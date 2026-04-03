from __future__ import annotations

import json
import os
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from lingclaude.core.types import Result
from lingclaude.core.config import LingClaudeConfig, load_config
from lingclaude.core.query_engine import QueryEngine, QueryEngineConfig, StopReason
from lingclaude.model.types import (
    MessageRole,
    ModelConfig,
    ModelMessage,
    ModelResponse,
    ModelUsage,
)
from lingclaude.model.factory import create_provider
from lingclaude.model.openai_provider import OpenAIProvider
from lingclaude.model.anthropic_provider import AnthropicProvider


# ---------------------------------------------------------------------------
# Config edge cases
# ---------------------------------------------------------------------------

class TestConfigEdgeCases:
    def test_empty_yaml_file(self, tmp_path: object) -> None:
        cfg_path = pathlib.Path(str(tmp_path)) / "empty.yaml"
        cfg_path.write_text("")
        cfg = load_config(cfg_path)
        assert cfg.engine.max_turns == 8

    def test_yaml_with_only_comments(self, tmp_path: object) -> None:
        cfg_path = pathlib.Path(str(tmp_path)) / "comments.yaml"
        cfg_path.write_text("# just a comment\n")
        cfg = load_config(cfg_path)
        assert cfg.model.provider == "openai"

    def test_partial_config(self, tmp_path: object) -> None:
        cfg_path = pathlib.Path(str(tmp_path)) / "partial.yaml"
        cfg_path.write_text("engine:\n  max_turns: 3\n")
        cfg = load_config(cfg_path)
        assert cfg.engine.max_turns == 3
        assert cfg.model.model == "gpt-4o"

    def test_model_config_from_yaml(self, tmp_path: object) -> None:
        cfg_path = pathlib.Path(str(tmp_path)) / "model.yaml"
        cfg_path.write_text(
            "model:\n  provider: anthropic\n  model: claude-sonnet-4-20250514\n  api_key: sk-test\n"
        )
        cfg = load_config(cfg_path)
        assert cfg.model.provider == "anthropic"
        assert cfg.model.model == "claude-sonnet-4-20250514"

    def test_from_dict_with_empty_model(self) -> None:
        cfg = LingClaudeConfig.from_dict({})
        assert cfg.model.provider == "openai"
        assert cfg.model.api_key == ""


# ---------------------------------------------------------------------------
# QueryEngine edge cases
# ---------------------------------------------------------------------------

class TestQueryEngineEdgeCases:
    def test_max_turns_reached(self) -> None:
        engine = QueryEngine(QueryEngineConfig(max_turns=1))
        result1 = engine.submit("hello")
        assert result1.stop_reason == StopReason.COMPLETED

        result2 = engine.submit("second")
        assert result2.stop_reason == StopReason.MAX_TURNS_REACHED
        assert "最大轮次" in result2.output

    def test_budget_exceeded(self) -> None:
        engine = QueryEngine(QueryEngineConfig(max_budget_tokens=1))
        result = engine.submit("a fairly long prompt that should exceed tiny budget")
        assert result.stop_reason == StopReason.MAX_BUDGET_REACHED

    def test_compact_messages(self) -> None:
        engine = QueryEngine(QueryEngineConfig(compact_after_turns=2))
        for i in range(5):
            engine.submit(f"msg {i}")
        assert len(engine._messages) <= 2

    def test_reset_clears_state(self) -> None:
        engine = QueryEngine()
        engine.submit("test")
        old_id = engine.session_id
        engine.reset()
        assert engine.session_id != old_id
        assert engine.turn_count == 0

    def test_stream_submit(self) -> None:
        engine = QueryEngine()
        events = list(engine.stream_submit("hello"))
        types = [e["type"] for e in events]
        assert "message_start" in types
        assert "message_delta" in types
        assert "message_stop" in types

    def test_persist_and_load_session(self, tmp_path: object) -> None:
        from lingclaude.core.session import SessionManager

        save_dir = pathlib.Path(str(tmp_path)) / "sessions"
        mgr = SessionManager(save_dir)
        engine = QueryEngine(session_manager=mgr)
        engine.submit("test message")

        path = engine.persist_session()
        assert pathlib.Path(path).exists()

        engine2 = QueryEngine(session_manager=mgr)
        loaded = engine2.load_session(engine.session_id)
        assert loaded is True

    def test_get_stats(self) -> None:
        engine = QueryEngine()
        engine.submit("hello")
        stats = engine.get_stats()
        assert "session_id" in stats
        assert stats["turns"] == 1
        assert "usage" in stats


# ---------------------------------------------------------------------------
# Model provider edge cases
# ---------------------------------------------------------------------------

class TestModelProviderEdgeCases:
    def test_openai_url_error(self) -> None:
        import urllib.error

        provider = OpenAIProvider(ModelConfig(api_key="sk-test"))
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            result = provider.complete(
                (ModelMessage(role=MessageRole.USER, content="hi"),)
            )
        assert result.is_error
        assert "网络错误" in result.error

    def test_openai_custom_base_url(self) -> None:
        provider = OpenAIProvider(ModelConfig(api_key="sk-test", base_url="https://custom.api.com/v1"))
        body = provider._build_request_body(
            (ModelMessage(role=MessageRole.USER, content="test"),),
            ModelConfig(api_key="sk-test", base_url="https://custom.api.com/v1"),
        )
        assert body["model"] == "gpt-4o"

    def test_anthropic_url_construction(self) -> None:
        provider = AnthropicProvider(
            ModelConfig(model="claude-sonnet-4-20250514", api_key="sk-ant", base_url="https://custom.anthropic.com")
        )
        _, msg_dicts = provider._build_request_body(
            (ModelMessage(role=MessageRole.USER, content="hi"),),
            ModelConfig(api_key="sk-ant"),
        )
        assert len(msg_dicts) == 1

    def test_anthropic_non_text_blocks_skipped(self) -> None:
        provider = AnthropicProvider()
        raw_data = {
            "content": [
                {"type": "image", "data": "base64..."},
                {"type": "text", "text": "only this counts"},
            ],
            "model": "claude-sonnet-4-20250514",
            "usage": {"input_tokens": 5, "output_tokens": 3},
        }
        result = provider._parse_response(raw_data, "claude-sonnet-4-20250514")
        assert result.is_ok
        assert result.data.content == "only this counts"

    def test_create_provider_with_no_api_key_no_env(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = create_provider(provider_name="openai")
            assert result.is_ok
            provider = result.data
            call_result = provider.complete(
                (ModelMessage(role=MessageRole.USER, content="hi"),)
            )
            assert call_result.is_error

    def test_model_message_with_tool_role(self) -> None:
        msg = ModelMessage(role=MessageRole.TOOL, content="output", tool_call_id="tc_1")
        d = msg.to_dict()
        assert d["role"] == "tool"
        assert d["tool_call_id"] == "tc_1"

    def test_model_response_with_raw(self) -> None:
        resp = ModelResponse(
            content="hi",
            model="gpt-4o",
            usage=ModelUsage(input_tokens=5, output_tokens=3),
            raw={"id": "chatcmpl-123"},
        )
        assert resp.raw["id"] == "chatcmpl-123"


# ---------------------------------------------------------------------------
# FileOps edge cases
# ---------------------------------------------------------------------------

class TestFileOpsEdgeCases:
    def test_path_prefix_bypass_blocked(self) -> None:
        from lingclaude.engine.file_ops import FileOps

        ops = FileOps(base_dir="/tmp/test_app", allow_escape=False)
        result = ops._resolve("../../tmp/application/evil")
        assert result.is_error

    def test_symlink_within_project(self) -> None:
        from lingclaude.engine.file_ops import FileOps

        ops = FileOps(base_dir=".")
        result = ops._resolve("lingclaude/__init__.py")
        assert result.is_ok

    def test_read_nonexistent_file(self) -> None:
        from lingclaude.engine.file_ops import FileOps

        ops = FileOps(base_dir=".")
        result = ops.read("nonexistent_file_xyz.py")
        assert result.is_error
        assert "not found" in result.error.lower() or "File not found" in result.error

    def test_edit_no_match(self, tmp_path: object) -> None:
        from lingclaude.engine.file_ops import FileOps

        base = pathlib.Path(str(tmp_path))
        (base / "test.txt").write_text("hello world")
        ops = FileOps(base_dir=str(base))
        result = ops.edit("test.txt", "not_in_file", "replacement")
        assert result.is_error

    def test_edit_multiple_without_replace_all(self, tmp_path: object) -> None:
        from lingclaude.engine.file_ops import FileOps

        base = pathlib.Path(str(tmp_path))
        (base / "test.txt").write_text("aaa bbb aaa")
        ops = FileOps(base_dir=str(base))
        result = ops.edit("test.txt", "aaa", "ccc")
        assert result.is_error
        assert "Multiple matches" in result.error


# ---------------------------------------------------------------------------
# Optimizer edge case (frozen dataclass fix verification)
# ---------------------------------------------------------------------------

class TestOptimizerEdgeCases:
    def test_grid_search_returns_valid_result(self) -> None:
        from lingclaude.self_optimizer.optimizer import (
            SynchronousOptimizer,
            OptimizationRequest,
        )
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "test_pkg"
            target.mkdir()
            (target / "sample.py").write_text("def hello():\n    print('hi')\n")

            optimizer = SynchronousOptimizer()
            request = OptimizationRequest(
                target=str(target),
                goal="structure",
                params={},
                config={"max_experiments": 2},
            )
            result = optimizer.optimize(request)
            assert result.success
            assert result.duration > 0
            assert result.experiments == 2

    def test_optimization_result_is_frozen(self) -> None:
        from dataclasses import FrozenInstanceError
        from lingclaude.self_optimizer.optimizer import OptimizationResult

        result = OptimizationResult(
            success=True,
            best_params={},
            best_score=1.0,
            experiments=1,
            duration=0.1,
        )
        with pytest.raises(FrozenInstanceError):
            result.duration = 99.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Session edge cases
# ---------------------------------------------------------------------------

class TestSessionEdgeCases:
    def test_load_nonexistent_session(self) -> None:
        from lingclaude.core.session import SessionManager

        with __import__("tempfile").TemporaryDirectory() as tmp:
            mgr = SessionManager(pathlib.Path(tmp))
            session = mgr.load("nonexistent_id")
            assert session is None

    def test_delete_nonexistent_session(self) -> None:
        from lingclaude.core.session import SessionManager

        with __import__("tempfile").TemporaryDirectory() as tmp:
            mgr = SessionManager(pathlib.Path(tmp))
            result = mgr.delete("nonexistent_id")
            assert result is False
