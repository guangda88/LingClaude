"""子代理工具 handler 插片 — 从 coding.py 拆分（灵元：工具是插片）。

SubagentToolsMixin: sub_agent / list_agents / interrupt_agent。
依赖 self._model_provider / self._subagent_manager（惰性初始化）。
"""

from __future__ import annotations

from typing import Any


class SubagentToolsMixin:
    """子代理工具 handler。"""

    def _sub_agent_handler(
        self,
        task: str,
        context: str = "",
        max_rounds: int = 5,
        provider: str | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        from lingclaude.engine.subagent import (
            SubagentContext,
            SubagentManager,
            SubagentRequest,
        )

        ctx = SubagentContext(
            runtime=self,
            model_provider=self._model_provider,
        )
        request = SubagentRequest(
            task=task,
            context=context,
            max_rounds=max_rounds,
            provider=provider,
        )
        manager = getattr(self, "_subagent_manager", None)
        if manager is None:
            manager = SubagentManager()
            self._subagent_manager = manager
        result = manager.run(request, ctx)
        return {
            "agent_id": result.agent_id,
            "output": result.output,
            "tools_used": list(result.tools_used),
            "success": result.success,
            "error": result.error,
            "rounds": result.rounds,
            "provider": result.provider,
        }

    def _list_agents_handler(self, **_kwargs: Any) -> dict[str, Any]:
        """T1-6: 列出所有子代理及其状态."""
        manager = getattr(self, "_subagent_manager", None)
        if manager is None:
            return {"agents": [], "backends": []}
        backends = manager.list_backends()
        # 列出各后端的运行中 agent（通过 _running dict）
        agents = []
        for backend_name in backends:
            backend = manager.get_backend(backend_name)
            if hasattr(backend, '_running'):
                for agent_id, info in backend._running.items():
                    status = backend.status(agent_id)
                    agents.append({
                        "agent_id": agent_id,
                        "backend": backend_name,
                        "status": status.value,
                        "task": info.get("result", {}).get("task", "") if isinstance(info.get("result"), dict) else "",
                    })
        return {"agents": agents, "backends": backends}

    def _interrupt_agent_handler(self, agent_id: str, **_kwargs: Any) -> dict[str, Any]:
        """T1-6: 中止指定子代理."""
        manager = getattr(self, "_subagent_manager", None)
        if manager is None:
            return {"success": False, "error": "No subagent manager"}
        # 尝试所有后端
        for backend_name in manager.list_backends():
            backend = manager.get_backend(backend_name)
            if hasattr(backend, 'abort'):
                if backend.abort(agent_id):
                    return {"success": True, "agent_id": agent_id, "message": "Agent interrupted"}
        return {"success": False, "agent_id": agent_id, "error": "Agent not found or backend doesn't support abort"}
