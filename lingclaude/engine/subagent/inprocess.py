"""In-process subagent backend (P1-2): 复用现有 SubAgent 单后端逻辑.

通过 import 旧版 lingclaude.engine.sub_agent.SubAgent 保持行为兼容,
但把 provider 解析从 hardcode 提升为可配置 request.provider.
"""
from __future__ import annotations

from lingclaude.engine.subagent.base import (
    SubagentBackend,
    SubagentContext,
    SubagentRequest,
    SubagentResult,
)


class InProcessSubagentBackend(SubagentBackend):
    """Same-process execution using the agent runtime + model provider."""

    name = "inprocess"

    def run(self, request: SubagentRequest, ctx: SubagentContext) -> SubagentResult:
        from lingclaude.engine.sub_agent import SubAgent, SubAgentConfig

        if ctx.runtime is None:
            return SubagentResult(
                agent_id="",
                task=request.task,
                output="",
                success=False,
                error="No runtime",
            )
        if ctx.model_provider is None:
            return SubagentResult(
                agent_id="",
                task=request.task,
                output="",
                success=False,
                error="No model provider",
            )
        config = SubAgentConfig(
            max_rounds=request.max_rounds,
            allowed_tools=ctx.allowed_tools,
        )
        agent = SubAgent(config=config, runtime=ctx.runtime, provider=ctx.model_provider)
        result = agent.run(request.task, request.context)
        return SubagentResult(
            agent_id=result.agent_id,
            task=request.task,
            output=result.output,
            success=result.success,
            error=result.error,
            tools_used=tuple(result.tools_used),
            rounds=result.rounds,
            provider=self.name,
        )
