"""SubagentManager: backend registry + factory + orchestrator (P1-2).

管理多后端 subagent:
- register(name, backend): 注册后端(实例或类), 支持别名(provider 名/后端名/别名)
- get_backend(provider): 按 provider 名查找后端, 失败回退默认后端
- run(request, ctx): 编排执行, 后端异常统一收敛为 SubagentResult
"""
from __future__ import annotations

from typing import Any

from lingclaude.engine.subagent.base import (
    SubagentBackend,
    SubagentContext,
    SubagentRequest,
    SubagentResult,
)
from lingclaude.engine.subagent.inprocess import InProcessSubagentBackend
from lingclaude.engine.subagent.acp import AcpSubagentBackend


class SubagentManager:
    """Registry + factory + orchestrator for subagent backends."""

    def __init__(self, default: str = "inprocess") -> None:
        self._backends: dict[str, SubagentBackend] = {}
        self._aliases: dict[str, str] = {}
        self._default = default
        self.register(InProcessSubagentBackend)
        self.register(AcpSubagentBackend)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register(
        self,
        backend: type[SubagentBackend] | SubagentBackend,
        names: tuple[str, ...] | None = None,
    ) -> None:
        """Register a backend class or instance.

        names: 额外别名(如 provider 名). 若为 None, 用 backend.name.
        同实例注册多个名字时共享单例.
        """
        instance = backend() if isinstance(backend, type) else backend
        names = names or (getattr(instance, "name", None) or instance.__class__.__name__.lower(),)
        primary = names[0]
        self._backends[primary] = instance
        for alias in names[1:]:
            self._aliases[alias] = primary
        for alias in names:
            self._aliases[alias] = primary

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    def get_backend(self, provider: str | None) -> SubagentBackend:
        """Resolve a backend by provider/backend/alias name.

        未命中: 注册了默认名则回退默认; 否则报 KeyError.
        """
        if not provider:
            return self._backends[self._default]
        if provider in self._backends:
            return self._backends[provider]
        primary = self._aliases.get(provider)
        if primary:
            return self._backends[primary]
        if self._default in self._backends:
            return self._backends[self._default]
        raise KeyError(f"No subagent backend registered for provider '{provider}'")

    def has_backend(self, provider: str | None) -> bool:
        try:
            self.get_backend(provider)
            return True
        except KeyError:
            return False

    def list_backends(self) -> list[str]:
        return sorted(set(self._backends) | set(self._aliases))

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------
    def run(self, request: SubagentRequest, ctx: SubagentContext) -> SubagentResult:
        """Execute a request via the resolved backend.

        任何后端抛出的异常都会被收敛为 SubagentResult(success=False).
        """
        backend = self.get_backend(request.provider)
        try:
            result = backend.run(request, ctx)
        except Exception as exc:  # noqa: BLE001 - backend boundary
            return SubagentResult(
                agent_id="",
                task=request.task,
                output="",
                success=False,
                error=f"SubagentManager.run: {exc}",
                provider=request.provider,
            )
        if result.provider is None:
            return SubagentResult(
                agent_id=result.agent_id,
                task=result.task,
                output=result.output,
                success=result.success,
                error=result.error,
                tools_used=result.tools_used,
                rounds=result.rounds,
                provider=getattr(backend, "name", request.provider),
            )
        return result
