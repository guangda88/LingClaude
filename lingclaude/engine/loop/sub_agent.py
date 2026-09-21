from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import uuid4


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
        tr = self._execute_tool_typed(name, arguments_json)
        return json.dumps(tr.to_dict(), ensure_ascii=False, default=str)

    def _execute_tool_typed(self, name: str, arguments_json: str) -> "ToolResult[Any]":
        """强类型版本：sub-agent 工具执行（错误语义用 error.code）。

        2026-09-17: 注解加引号（惰性求值）— ToolResult 仅函数内局部导入，
        裸注解在 get_type_hints() 时 F821。
        """
        from lingclaude.core.types import ToolErrorCode, ToolResult, parse_tool_result

        try:
            kwargs = json.loads(arguments_json)
        except json.JSONDecodeError:
            return ToolResult.err(
                f"Invalid JSON: {arguments_json}",
                code=ToolErrorCode.INVALID_ARGS,
                tool_name=name,
            )

        # P1-2: 敏感路径隔离（对标 AtomCode task.rs hard deny）
        from lingclaude.engine.sensitive_path_gate import check_sensitive_path
        for key, value in kwargs.items():
            if isinstance(value, str) and check_sensitive_path(value)[0]:
                return ToolResult.err(
                    f"Tool '{name}' rejected: argument '{key}' references sensitive path",
                    code=ToolErrorCode.GUARD_DENIED,
                    tool_name=name,
                )
            elif isinstance(value, dict):
                for v in value.values():
                    if isinstance(v, str) and check_sensitive_path(v)[0]:
                        return ToolResult.err(
                            f"Tool '{name}' rejected: nested argument references sensitive path",
                            code=ToolErrorCode.GUARD_DENIED,
                            tool_name=name,
                        )

        try:
            result = self._runtime.execute_tool(name, **kwargs)
            return parse_tool_result(result, tool_name=name)
        except Exception as e:
            return ToolResult.err(str(e), code=ToolErrorCode.EXECUTION_ERROR, tool_name=name)

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
