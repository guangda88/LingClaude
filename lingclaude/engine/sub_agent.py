from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from lingclaude.core.types import Result

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubAgentResult:
    agent_id: str
    task: str
    output: str
    tools_used: tuple[str, ...] = ()
    success: bool = True
    error: str | None = None
    rounds: int = 0


@dataclass
class SubAgentConfig:
    max_rounds: int = 5
    max_tools_per_round: int = 3
    allowed_tools: tuple[str, ...] = (
        "read", "glob", "grep", "bash",
        "git_status", "git_diff", "git_log", "git_blame",
        "index_project", "list_functions",
    )
    system_prompt: str = "You are a focused sub-agent. Complete the given task using available tools. Be concise."


class SubAgent:
    def __init__(
        self,
        config: SubAgentConfig | None = None,
        runtime: Any | None = None,
        provider: Any | None = None,
    ) -> None:
        self.config = config or SubAgentConfig()
        self._runtime = runtime
        self._provider = provider

    def run(self, task: str, context: str = "") -> SubAgentResult:
        agent_id = uuid4().hex[:8]
        logger.info("SubAgent[%s] starting task: %s", agent_id, task[:80])

        if self._runtime is None:
            return SubAgentResult(
                agent_id=agent_id, task=task,
                output="", success=False,
                error="No runtime available",
            )

        messages: list[dict[str, str]] = []
        system = self.config.system_prompt
        if context:
            system += f"\n\nContext:\n{context}"
        messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": task})

        all_tools_used: list[str] = []
        for round_idx in range(self.config.max_rounds):
            if self._provider is None:
                return SubAgentResult(
                    agent_id=agent_id, task=task,
                    output="", success=False,
                    error="No model provider available",
                )

            tools = self._build_tools_spec()
            try:
                result = self._provider.complete(
                    tuple(messages), tools=tools,
                )
            except Exception as e:
                return SubAgentResult(
                    agent_id=agent_id, task=task,
                    output="", success=False,
                    error=f"Model call failed: {e}",
                    rounds=round_idx + 1,
                )

            if result.is_error:
                return SubAgentResult(
                    agent_id=agent_id, task=task,
                    output="", success=False,
                    error=result.error,
                    rounds=round_idx + 1,
                )

            response = result.data
            if not response.tool_calls:
                return SubAgentResult(
                    agent_id=agent_id, task=task,
                    output=response.content or "",
                    success=True,
                    tools_used=tuple(all_tools_used),
                    rounds=round_idx + 1,
                )

            messages.append({
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in response.tool_calls
                ],
            })

            for tc in response.tool_calls[: self.config.max_tools_per_round]:
                if tc.name not in self.config.allowed_tools:
                    tool_output = json.dumps({"error": f"Tool '{tc.name}' not allowed for sub-agent"})
                else:
                    tool_output = self._execute_tool(tc.name, tc.arguments)
                    all_tools_used.append(tc.name)

                messages.append({
                    "role": "tool",
                    "name": tc.name,
                    "content": tool_output,
                    "tool_call_id": tc.id,
                })

        return SubAgentResult(
            agent_id=agent_id, task=task,
            output="[SubAgent max rounds reached]",
            success=False,
            tools_used=tuple(all_tools_used),
            rounds=self.config.max_rounds,
        )

    def _execute_tool(self, name: str, arguments_json: str) -> str:
        try:
            kwargs = json.loads(arguments_json)
        except json.JSONDecodeError:
            return json.dumps({"error": f"Invalid JSON: {arguments_json}"}, ensure_ascii=False)

        try:
            result = self._runtime.execute_tool(name, **kwargs)
            if isinstance(result, dict):
                return json.dumps(result, ensure_ascii=False, default=str)
            return json.dumps({"result": result}, ensure_ascii=False, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _build_tools_spec(self) -> list[dict[str, Any]]:
        if self._runtime is None:
            return []
        tools: list[dict[str, Any]] = []
        all_defs = self._runtime.registry.get_all_definitions()
        for td in all_defs:
            if td["name"] in self.config.allowed_tools:
                tools.append({
                    "type": "function",
                    "function": {
                        "name": td["name"],
                        "description": td["description"],
                        "parameters": {
                            "type": "object",
                            "properties": td["parameters"],
                            "required": list(td["parameters"].keys()),
                        },
                    },
                })
        return tools
