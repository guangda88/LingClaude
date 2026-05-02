from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from lingclaude.engine.sub_agent import SubAgent, SubAgentConfig, SubAgentResult


class TestSubAgentResult:
    def test_defaults(self) -> None:
        r = SubAgentResult(agent_id="abc", task="t", output="ok")
        assert r.agent_id == "abc"
        assert r.success is True
        assert r.error is None
        assert r.tools_used == ()
        assert r.rounds == 0

    def test_failure(self) -> None:
        r = SubAgentResult(agent_id="x", task="t", output="", success=False, error="fail")
        assert r.success is False
        assert r.error == "fail"


class TestSubAgentConfig:
    def test_defaults(self) -> None:
        c = SubAgentConfig()
        assert c.max_rounds == 5
        assert c.max_tools_per_round == 3
        assert "read" in c.allowed_tools
        assert "write" not in c.allowed_tools
        assert "edit" not in c.allowed_tools

    def test_custom(self) -> None:
        c = SubAgentConfig(max_rounds=10, allowed_tools=("read",))
        assert c.max_rounds == 10
        assert c.allowed_tools == ("read",)


class TestSubAgentRun:
    def test_no_runtime(self) -> None:
        agent = SubAgent()
        result = agent.run("do something")
        assert result.success is False
        assert "No runtime" in result.error

    def test_no_provider(self) -> None:
        runtime = MagicMock()
        agent = SubAgent(runtime=runtime)
        result = agent.run("do something")
        assert result.success is False
        assert "No model provider" in result.error

    def test_successful_run(self) -> None:
        runtime = MagicMock()
        runtime.registry.get_all_definitions.return_value = (
            {"name": "read", "description": "Read file", "parameters": {"path": {"type": "string"}}},
        )
        runtime.execute_tool.return_value = {"content": "file contents"}

        provider = MagicMock()
        response = MagicMock()
        response.is_error = False
        response.content = "Analysis complete"
        response.tool_calls = None
        provider.complete.return_value = Result.ok_helper(response)

        agent = SubAgent(runtime=runtime, provider=provider)
        result = agent.run("read test.py")
        assert result.success is True
        assert result.output == "Analysis complete"

    def test_tool_execution_and_stop(self) -> None:
        runtime = MagicMock()
        runtime.registry.get_all_definitions.return_value = (
            {"name": "read", "description": "Read", "parameters": {"path": {"type": "string"}}},
        )
        runtime.execute_tool.return_value = {"content": "data"}

        provider = MagicMock()
        tc = MagicMock()
        tc.id = "call_1"
        tc.name = "read"
        tc.arguments = '{"path": "/tmp/x"}'

        round1 = MagicMock()
        round1.is_error = False
        round1.content = ""
        round1.tool_calls = [tc]

        round2 = MagicMock()
        round2.is_error = False
        round2.content = "Done after reading"
        round2.tool_calls = None

        provider.complete.side_effect = [Result.ok_helper(round1), Result.ok_helper(round2)]

        agent = SubAgent(runtime=runtime, provider=provider)
        result = agent.run("analyze file")
        assert result.success is True
        assert "read" in result.tools_used
        assert result.rounds == 2

    def test_max_rounds_reached(self) -> None:
        runtime = MagicMock()
        runtime.registry.get_all_definitions.return_value = (
            {"name": "read", "description": "Read", "parameters": {"path": {"type": "string"}}},
        )
        runtime.execute_tool.return_value = {"content": "data"}

        provider = MagicMock()
        tc = MagicMock()
        tc.id = "call_1"
        tc.name = "read"
        tc.arguments = '{"path": "/tmp/x"}'

        looping = MagicMock()
        looping.is_error = False
        looping.content = ""
        looping.tool_calls = [tc]

        provider.complete.return_value = Result.ok_helper(looping)

        config = SubAgentConfig(max_rounds=3)
        agent = SubAgent(config=config, runtime=runtime, provider=provider)
        result = agent.run("never stops")
        assert result.success is False
        assert result.rounds == 3

    def test_disallowed_tool_rejected(self) -> None:
        runtime = MagicMock()
        runtime.registry.get_all_definitions.return_value = ()

        provider = MagicMock()
        tc = MagicMock()
        tc.id = "call_1"
        tc.name = "write"
        tc.arguments = '{"path": "/tmp/x", "content": "evil"}'

        round1 = MagicMock()
        round1.is_error = False
        round1.content = ""
        round1.tool_calls = [tc]

        round2 = MagicMock()
        round2.is_error = False
        round2.content = "tried to write"
        round2.tool_calls = None

        provider.complete.side_effect = [Result.ok_helper(round1), Result.ok_helper(round2)]

        config = SubAgentConfig(allowed_tools=("read",))
        agent = SubAgent(config=config, runtime=runtime, provider=provider)
        result = agent.run("try write")
        assert "write" not in result.tools_used

    def test_provider_error(self) -> None:
        runtime = MagicMock()
        runtime.registry.get_all_definitions.return_value = ()

        provider = MagicMock()
        provider.complete.return_value = Result.fail("API error", code="ERR")

        agent = SubAgent(runtime=runtime, provider=provider)
        result = agent.run("task")
        assert result.success is False
        assert "API error" in result.error

    def test_model_call_exception(self) -> None:
        runtime = MagicMock()
        runtime.registry.get_all_definitions.return_value = ()

        provider = MagicMock()
        provider.complete.side_effect = Exception("Connection refused")

        agent = SubAgent(runtime=runtime, provider=provider)
        result = agent.run("task")
        assert result.success is False
        assert "Connection refused" in result.error


class TestSubAgentExecuteTool:
    def test_invalid_json(self) -> None:
        runtime = MagicMock()
        agent = SubAgent(runtime=runtime)
        output = agent._execute_tool("read", "not json")
        assert "error" in json.loads(output)

    def test_valid_execution(self) -> None:
        runtime = MagicMock()
        runtime.execute_tool.return_value = {"content": "data"}
        agent = SubAgent(runtime=runtime)
        output = agent._execute_tool("read", '{"path": "/tmp/x"}')
        parsed = json.loads(output)
        assert "content" in parsed

    def test_tool_exception(self) -> None:
        runtime = MagicMock()
        runtime.execute_tool.side_effect = Exception("boom")
        agent = SubAgent(runtime=runtime)
        output = agent._execute_tool("read", '{"path": "/tmp/x"}')
        assert "error" in json.loads(output)


class TestSubAgentBuildToolsSpec:
    def test_filters_to_allowed(self) -> None:
        runtime = MagicMock()
        runtime.registry.get_all_definitions.return_value = (
            {"name": "read", "description": "Read", "parameters": {"path": {"type": "string"}}},
            {"name": "write", "description": "Write", "parameters": {"path": {"type": "string"}, "content": {"type": "string"}}},
        )
        config = SubAgentConfig(allowed_tools=("read",))
        agent = SubAgent(config=config, runtime=runtime)
        specs = agent._build_tools_spec()
        assert len(specs) == 1
        assert specs[0]["function"]["name"] == "read"

    def test_no_runtime(self) -> None:
        agent = SubAgent()
        assert agent._build_tools_spec() == []


class Result:
    """Minimal Result monad for test helpers."""

    def __init__(self, data: object = None, error: str | None = None) -> None:
        self.data = data
        self.error = error
        self.is_error = error is not None

    @classmethod
    def ok_helper(cls, data: object) -> Result:
        return cls(data=data)

    @classmethod
    def fail(cls, error: str, code: str = "") -> Result:
        return cls(error=error)
