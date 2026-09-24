"""子代理工具 handler 插片 — 从 coding.py 拆分（灵元：工具是插片）。

SubagentToolsMixin: sub_agent / list_agents / interrupt_agent。
依赖 self._model_provider / self._subagent_manager（惰性初始化）。
"""

from __future__ import annotations

from typing import Any

from lingclaude.core.types import ToolResult


class SubagentToolsMixin:
    """子代理工具 handler。"""

    def _sub_agent_handler(
        self,
        task: str,
        context: str = "",
        max_rounds: int = 5,
        provider: str | None = None,
        **_kwargs: Any,
    ) -> ToolResult[dict[str, Any]]:
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
        # TUI 过程可见性：子代理启动/结束状态行（best-effort，渲染失败不阻断）
        try:
            from lingclaude.cli.render_facade import render
            render("print_info", f"⏳ sub_agent 启动: {task[:60]} (max_rounds={max_rounds})")
        except Exception:  # noqa: BLE001
            pass
        manager = getattr(self, "_subagent_manager", None)
        if manager is None:
            manager = SubagentManager()
            self._subagent_manager = manager
        result = manager.run(request, ctx)

        # TUI 过程可见性：结束状态行（成功✅/失败❌ + 轮次 + 耗时摘要）
        try:
            from lingclaude.cli.render_facade import render
            mark = "✅" if result.success else "❌"
            preview = (result.output or result.error or "")[:80].replace("\n", " ")
            render("print_info",
                   f"{mark} sub_agent 结束: rounds={result.rounds} "
                   f"provider={result.provider or 'inprocess'} | {preview}")
        except Exception:  # noqa: BLE001
            pass

        # R8：把每次 sub_agent 调用喂给 DataFlywheel,让"子代理节流效果"可查
        # （docs/SYSTEMS_THEORY_SYNTHESIS §一.4 token 战的治本项）
        try:
            if hasattr(self, "_session_runtime"):
                self._session_runtime.log_to_flywheel(
                    pattern_type="sub_agent_call",
                    error_message=(
                        f"success={result.success} rounds={result.rounds} "
                        f"provider={result.provider or 'inprocess'} "
                        f"output_len={len(result.output or '')}"
                    ),
                    tool_name="sub_agent",
                    file_path="",
                    context=(getattr(self, "session_id", "") or "")[:80],
                )
        except Exception:  # noqa: BLE001 — 飞轮失败不阻塞 sub_agent 返回
            pass

        return ToolResult.ok(
            {
                "agent_id": result.agent_id,
                "output": result.output,
                "tools_used": list(result.tools_used),
                "success": result.success,
                "error": result.error,
                "rounds": result.rounds,
                "provider": result.provider,
            }
        )

    def _list_agents_handler(self, **_kwargs: Any) -> ToolResult[dict[str, Any]]:
        """T1-6: 列出所有子代理及其状态."""
        manager = getattr(self, "_subagent_manager", None)
        if manager is None:
            return ToolResult.ok({"agents": [], "backends": []})
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
        return ToolResult.ok({"agents": agents, "backends": backends})

    def _interrupt_agent_handler(self, agent_id: str, **_kwargs: Any) -> ToolResult[dict[str, Any]]:
        """T1-6: 中止指定子代理."""
        manager = getattr(self, "_subagent_manager", None)
        if manager is None:
            return ToolResult.err("No subagent manager", tool_name="interrupt_agent")
        # 尝试所有后端
        for backend_name in manager.list_backends():
            backend = manager.get_backend(backend_name)
            if hasattr(backend, 'abort'):
                if backend.abort(agent_id):
                    return ToolResult.ok({"success": True, "agent_id": agent_id, "message": "Agent interrupted"})
        return ToolResult.err(
            "Agent not found or backend doesn't support abort",
            tool_name="interrupt_agent",
        )
