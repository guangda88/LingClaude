from __future__ import annotations

from pathlib import Path
from typing import Any

from lingclaude.core.config import LingClaudeConfig
from lingclaude.core.permissions import PermissionContext
from lingclaude.core.query_engine import QueryEngine, TurnResult
from lingclaude.engine.bash import BashExecutor
from lingclaude.engine.file_ops import FileOps
from lingclaude.engine.file_edit import FileEditTool
from lingclaude.engine.file_read import FileReadTool
from lingclaude.engine.grep import GrepTool
from lingclaude.engine.tools import ToolDefinition, ToolRegistry
from lingclaude.self_optimizer import (
    OptimizationAdvisor,
    OptimizationTrigger,
    SynchronousOptimizer,
    StructureEvaluator,
)
from lingclaude.engine.git import git_blame, git_diff, git_log, git_status
from lingclaude.engine.indexer import index_project
from lingclaude.engine.stt import STTEngine
from lingclaude.self_optimizer.learner.patterns import PatternRecognizer


class CodingRuntime:
    def __init__(self, config: LingClaudeConfig | None = None) -> None:
        self.config = config or LingClaudeConfig()
        self._pattern_recognizer = PatternRecognizer()
        self._setup_tools()

    def _setup_tools(self) -> None:
        self.bash = BashExecutor(timeout=self.config.optimizer.timeout_seconds)
        self.file_ops = FileOps()
        self.file_edit = FileEditTool()
        self.file_read = FileReadTool()
        self.grep_tool = GrepTool()
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
                security_scope="execute",
            )
        )
        self.registry.register(
            ToolDefinition(
                name="read",
                description="Read file contents with line numbers, offset/limit support",
                parameters={
                    "path": {"type": "string"},
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                    "line_numbers": {"type": "boolean"},
                },
                handler=self._read_handler,
                security_scope="read",
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
                security_scope="write",
            )
        )
        self.registry.register(
            ToolDefinition(
                name="edit",
                description="Edit file by replacing text (with backup/rollback)",
                parameters={
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                handler=self._edit_handler,
                security_scope="write",
            )
        )
        self.registry.register(
            ToolDefinition(
                name="file_create",
                description="Create a new file with content",
                parameters={
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                handler=self._file_create_handler,
                security_scope="write",
            )
        )
        self.registry.register(
            ToolDefinition(
                name="file_insert",
                description="Insert text at a specific line number",
                parameters={
                    "path": {"type": "string"},
                    "line": {"type": "integer"},
                    "text": {"type": "string"},
                },
                handler=self._file_insert_handler,
                security_scope="write",
            )
        )
        self.registry.register(
            ToolDefinition(
                name="file_delete_lines",
                description="Delete a range of lines from a file",
                parameters={
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                handler=self._file_delete_lines_handler,
                security_scope="write",
            )
        )
        self.registry.register(
            ToolDefinition(
                name="file_undo",
                description="Undo last edit by restoring backup",
                parameters={"path": {"type": "string"}},
                handler=self._file_undo_handler,
                security_scope="write",
            )
        )
        self.registry.register(
            ToolDefinition(
                name="glob",
                description="Find files by pattern",
                parameters={"pattern": {"type": "string"}},
                handler=self._glob_handler,
                security_scope="read",
            )
        )
        self.registry.register(
            ToolDefinition(
                name="grep",
                description="Search file contents with regex, literal, and case options",
                parameters={
                    "pattern": {"type": "string"},
                    "include": {"type": "string"},
                    "literal": {"type": "boolean"},
                    "case_sensitive": {"type": "boolean"},
                },
                handler=self._grep_handler,
                security_scope="read",
            )
        )
        self.stt = STTEngine()
        self.registry.register(
            ToolDefinition(
                name="stt",
                description="Record audio and transcribe to text",
                parameters={
                    "duration": {"type": "integer", "description": "Recording duration in seconds"},
                    "file": {"type": "string", "description": "Audio file path to transcribe (optional)"},
                    "backend": {"type": "string", "description": "STT backend: whisper or sherpa_onnx"},
                },
                handler=self._stt_handler,
                security_scope="execute",
            )
        )
        self.registry.register(
            ToolDefinition(
                name="git_status",
                description="Show working tree status (short/porcelain format)",
                parameters={
                    "path": {"type": "string", "description": "Repository path (default: .)"},
                },
                handler=self._git_status_handler,
                security_scope="read",
            )
        )
        self.registry.register(
            ToolDefinition(
                name="git_diff",
                description="Show staged or unstaged changes",
                parameters={
                    "path": {"type": "string", "description": "Repository path"},
                    "target": {"type": "string", "description": "Specific file or directory to diff"},
                    "staged": {"type": "boolean", "description": "Show staged changes (--staged)"},
                    "stat": {"type": "boolean", "description": "Show diffstat summary"},
                },
                handler=self._git_diff_handler,
                security_scope="read",
            )
        )
        self.registry.register(
            ToolDefinition(
                name="git_log",
                description="Show commit history",
                parameters={
                    "path": {"type": "string", "description": "Repository path"},
                    "count": {"type": "integer", "description": "Number of commits (default 10)"},
                    "follow": {"type": "string", "description": "Follow file renames for a specific file"},
                },
                handler=self._git_log_handler,
                security_scope="read",
            )
        )
        self.registry.register(
            ToolDefinition(
                name="git_blame",
                description="Show line-level authorship for a file",
                parameters={
                    "file_path": {"type": "string", "description": "File to blame"},
                    "cwd": {"type": "string", "description": "Repository root"},
                    "start_line": {"type": "integer", "description": "Start line (optional)"},
                    "end_line": {"type": "integer", "description": "End line (optional)"},
                },
                handler=self._git_blame_handler,
                security_scope="read",
            )
        )
        self.registry.register(
            ToolDefinition(
                name="index_project",
                description="Scan Python project and build symbol table (classes, functions, imports)",
                parameters={
                    "path": {"type": "string", "description": "Project root directory"},
                    "max_files": {"type": "integer", "description": "Max files to scan (default 200)"},
                },
                handler=self._index_project_handler,
                security_scope="read",
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

    def _read_handler(
        self,
        path: str,
        offset: int = 0,
        limit: int | None = None,
        line_numbers: bool = True,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        result = self.file_read.read(path, offset=offset, limit=limit, line_numbers=line_numbers)
        if result.is_error:
            return {"error": result.error}
        return result.data.to_dict()

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
        result = self.file_edit.replace(path, old_text, new_text, replace_all)
        if result.is_error:
            return {"error": result.error}
        return result.data.to_dict()

    def _file_create_handler(
        self, path: str, content: str, **_kwargs: Any
    ) -> dict[str, Any]:
        result = self.file_edit.create(path, content)
        if result.is_error:
            return {"error": result.error}
        return result.data.to_dict()

    def _file_insert_handler(
        self, path: str, line: int, text: str, **_kwargs: Any
    ) -> dict[str, Any]:
        result = self.file_edit.insert(path, line, text)
        if result.is_error:
            return {"error": result.error}
        return result.data.to_dict()

    def _file_delete_lines_handler(
        self, path: str, start_line: int, end_line: int, **_kwargs: Any
    ) -> dict[str, Any]:
        result = self.file_edit.delete_lines(path, start_line, end_line)
        if result.is_error:
            return {"error": result.error}
        return result.data.to_dict()

    def _file_undo_handler(self, path: str, **_kwargs: Any) -> dict[str, Any]:
        result = self.file_edit.undo(path)
        if result.is_error:
            return {"error": result.error}
        return {"result": result.data}

    def _glob_handler(self, pattern: str, **_kwargs: Any) -> dict[str, Any]:
        result = self.file_ops.glob(pattern)
        if result.is_error:
            return {"error": result.error}
        return {"files": result.data}

    def _grep_handler(
        self,
        pattern: str,
        include: str | None = None,
        literal: bool = False,
        case_sensitive: bool = True,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        result = self.grep_tool.search(pattern, include=include, literal=literal, case_sensitive=case_sensitive)
        if result.is_error:
            return {"error": result.error}
        return result.data.to_dict()

    def _stt_handler(
        self,
        duration: int = 5,
        file: str | None = None,
        backend: str | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not self.stt.is_available():
            return {"error": "无可用的 STT 后端（需安装 openai-whisper 或 sherpa-onnx）"}
        if file:
            stt_result = self.stt.transcribe(file, backend=backend)
        else:
            stt_result = self.stt.record_and_transcribe(duration=duration, backend=backend)
        if not stt_result.available:
            return {"error": stt_result.error}
        return {
            "text": stt_result.text,
            "backend": stt_result.backend,
            "duration": stt_result.duration,
            "language": stt_result.language,
        }

    def _git_status_handler(self, path: str = ".", **_kwargs: Any) -> dict[str, Any]:
        result = git_status(path)
        if result.is_error:
            return {"error": result.error}
        return result.data

    def _git_diff_handler(
        self,
        path: str = ".",
        target: str = "",
        staged: bool = False,
        stat: bool = False,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        result = git_diff(path, target=target, staged=staged, stat=stat)
        if result.is_error:
            return {"error": result.error}
        return result.data

    def _git_log_handler(
        self,
        path: str = ".",
        count: int = 10,
        follow: str | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        result = git_log(path, count=count, follow=follow)
        if result.is_error:
            return {"error": result.error}
        return result.data

    def _git_blame_handler(
        self,
        file_path: str,
        cwd: str = ".",
        start_line: int | None = None,
        end_line: int | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        result = git_blame(file_path, cwd=cwd, start_line=start_line, end_line=end_line)
        if result.is_error:
            return {"error": result.error}
        return result.data

    def _index_project_handler(
        self,
        path: str = ".",
        max_files: int = 200,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        result = index_project(path, max_files=max_files)
        if result.is_error:
            return {"error": result.error}
        return result.data.to_dict()

    def execute_tool(self, name: str, **kwargs: Any) -> dict[str, Any]:
        if self.permissions.blocks(name):
            return {"error": f"Tool blocked by permissions: {name}"}
        result = self.registry.execute(name, **kwargs)
        if result.is_error:
            return {"error": result.error}
        data = result.data
        return data if isinstance(data, dict) else {"result": data}

    def analyze(self, target: str = ".") -> dict[str, Any]:
        self.evaluator = StructureEvaluator(target)
        metrics = self.evaluator.get_current_metrics()

        findings = self._pattern_recognizer.recognize_from_file(target) if Path(target).is_file() else ()
        if not findings and Path(target).is_dir():
            all_findings: list[dict[str, Any]] = []
            for py_file in Path(target).rglob("*.py"):
                file_findings = self._pattern_recognizer.recognize_from_file(str(py_file))
                all_findings.extend(file_findings)
            findings = tuple(all_findings)

        metrics["pattern_findings"] = len(findings)
        metrics["findings"] = [
            {
                "file": f.get("file", ""),
                "line": f.get("line", 0),
                "name": f.get("name", ""),
                "severity": f.get("severity", ""),
                "message": f.get("message", ""),
            }
            for f in findings
        ]
        metrics["detectors"] = self._pattern_recognizer.get_statistics()
        return metrics

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
