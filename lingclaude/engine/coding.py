from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from lingclaude.core.config import LingClaudeConfig
from lingclaude.core.permissions import PermissionContext
from lingclaude.core.query_engine import QueryEngine, TurnResult
from lingclaude.engine.bash import BashExecutor
from lingclaude.engine.file_ops import FileOps
from lingclaude.engine.tools import ToolDefinition, ToolRegistry
from lingclaude.self_optimizer import (
    OptimizationAdvisor,
    OptimizationTrigger,
    SynchronousOptimizer,
    StructureEvaluator,
)


class CodingRuntime:
    def __init__(self, config: LingClaudeConfig | None = None) -> None:
        self.config = config or LingClaudeConfig()
        self._setup_tools()

    def _setup_tools(self) -> None:
        self.bash = BashExecutor(timeout=self.config.engine.max_budget_tokens // 10000 + 30)
        self.file_ops = FileOps()
        self.registry = ToolRegistry()
        self.permissions = PermissionContext.from_config(
            deny_tools=self.config.permissions.deny_tools,
            deny_prefixes=self.config.permissions.deny_prefixes,
        )
        self.evaluator = StructureEvaluator()
        self.optimizer = SynchronousOptimizer()
        self.advisor = OptimizationAdvisor()

        self.registry.register(
            ToolDefinition(
                name="bash",
                description="Execute bash commands",
                parameters={"command": {"type": "string"}},
                handler=self._bash_handler,
            )
        )
        self.registry.register(
            ToolDefinition(
                name="read",
                description="Read file contents",
                parameters={"path": {"type": "string"}},
                handler=self._read_handler,
            )
        )
        self.registry.register(
            ToolDefinition(
                name="write",
                description="Write file contents",
                parameters={
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                handler=self._write_handler,
            )
        )
        self.registry.register(
            ToolDefinition(
                name="edit",
                description="Edit file by replacing text",
                parameters={
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                handler=self._edit_handler,
            )
        )
        self.registry.register(
            ToolDefinition(
                name="glob",
                description="Find files by pattern",
                parameters={"pattern": {"type": "string"}},
                handler=self._glob_handler,
            )
        )
        self.registry.register(
            ToolDefinition(
                name="grep",
                description="Search file contents",
                parameters={"pattern": {"type": "string"}},
                handler=self._grep_handler,
            )
        )

    def _bash_handler(self, command: str, **_kwargs: Any) -> dict[str, Any]:
        result = self.bash.run(command)
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration": result.duration,
        }

    def _read_handler(self, path: str, **_kwargs: Any) -> dict[str, Any]:
        result = self.file_ops.read(path)
        if result.is_error:
            return {"error": result.error}
        return {"content": result.data.content, "size": result.data.size}

    def _write_handler(
        self, path: str, content: str, **_kwargs: Any
    ) -> dict[str, Any]:
        result = self.file_ops.write(path, content)
        if result.is_error:
            return {"error": result.error}
        return {"path": result.data}

    def _edit_handler(
        self,
        path: str,
        old_text: str,
        new_text: str,
        replace_all: bool = False,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        result = self.file_ops.edit(path, old_text, new_text, replace_all)
        if result.is_error:
            return {"error": result.error}
        return {"path": result.data}

    def _glob_handler(self, pattern: str, **_kwargs: Any) -> dict[str, Any]:
        result = self.file_ops.glob(pattern)
        if result.is_error:
            return {"error": result.error}
        return {"files": result.data}

    def _grep_handler(self, pattern: str, **_kwargs: Any) -> dict[str, Any]:
        result = self.file_ops.grep(pattern)
        if result.is_error:
            return {"error": result.error}
        return {"matches": result.data}

    def execute_tool(self, name: str, **kwargs: Any) -> dict[str, Any]:
        if self.permissions.blocks(name):
            return {"error": f"Tool blocked by permissions: {name}"}
        try:
            result = self.registry.execute(name, **kwargs)
            return result if isinstance(result, dict) else {"result": result}
        except Exception as e:
            return {"error": str(e)}

    def analyze(self, target: str = ".") -> dict[str, Any]:
        self.evaluator = StructureEvaluator(target)
        return self.evaluator.get_current_metrics()

    def optimize(
        self, target: str = ".", goal: str = "structure", max_trials: int = 20
    ) -> dict[str, Any]:
        from lingclaude.self_optimizer.optimizer import OptimizationRequest

        request = OptimizationRequest(
            target=target,
            goal=goal,
            params={},
            config={"max_experiments": max_trials},
        )
        result = self.optimizer.optimize(request)

        if result.success:
            metrics = self.analyze(target)
            report = self.advisor.generate_report(
                goal=goal,
                target=target,
                current_metrics=metrics,
                optimization_result=result,
            )
            return {
                "success": True,
                "best_params": result.best_params,
                "best_score": result.best_score,
                "experiments": result.experiments,
                "duration": result.duration,
                "report": report,
            }
        return {
            "success": False,
            "error": result.error,
        }

    def check_and_optimize(
        self, context: dict[str, Any], target: str = ".", goal: str = "structure"
    ) -> dict[str, Any]:
        trigger = OptimizationTrigger()
        should_trigger, trigger_info = trigger.check_all_conditions(context)

        if not should_trigger:
            return {"triggered": False, "reason": "No trigger conditions met"}

        return {
            "triggered": True,
            "trigger_info": {
                "type": trigger_info.type,
                "reason": trigger_info.reason,
                "priority": trigger_info.priority,
            },
            "optimization": self.optimize(target, goal),
        }
