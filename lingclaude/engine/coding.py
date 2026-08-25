from __future__ import annotations

from pathlib import Path
from typing import Any

from lingclaude.core.config import lingclaudeConfig
from lingclaude.core.permissions import PermissionContext
from lingclaude.engine.bash import BashExecutor
from lingclaude.engine.bash_lingxi import BashlingxiExecutor
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
from lingclaude.engine.ast_edit import list_functions, replace_function_body
from lingclaude.engine.stt import STTEngine
from lingclaude.engine.verification_gate import VerificationGate, WRITE_SCOPED_TOOLS, CRITICAL_TOOLS
from lingclaude.engine.tool_pipeline import ToolPipeline
from lingclaude.self_optimizer.learner.patterns import PatternRecognizer
from lingclaude.engine.todo import TodoStore, make_handlers as _make_todo_handlers
from lingclaude.engine.lsp_provider import StdioLspProvider, _path_to_uri


class CodingRuntime:
    def __init__(self, config: lingclaudeConfig | None = None, model_provider: Any | None = None) -> None:
        self.config = config or lingclaudeConfig()
        self._model_provider = model_provider
        self._pattern_recognizer = PatternRecognizer()
        self.verification_gate = VerificationGate()
        self._setup_tools()
        # P0-1: todo store (session-scoped SQLite)
        data_dir = Path("/home/ai/lingclaude/data")
        data_dir.mkdir(exist_ok=True)
        session_id = getattr(self.config, "session_id", "default")
        self._todo_store = TodoStore(data_dir / "todos.db", session_id=session_id)
        self._todo_handlers = _make_todo_handlers(self._todo_store)
        # P1-1: LSP provider (lazy init on first use)
        self._lsp_provider: StdioLspProvider | None = None
        self._lsp_workspace_root: Path | None = None

    def _setup_tools(self) -> None:
        # T0-10: 沙箱策略 — LINGCLAUDE_SANDBOX_MODE=strict/paranoid 时 bwrap 不可用即 fail-closed
        import os

        sandbox_mode = os.environ.get("LINGCLAUDE_SANDBOX_MODE")
        sandbox_policy = None
        if sandbox_mode:
            from lingclaude.lacp.sandbox_policy import SandboxMode, SandboxPolicy

            try:
                sandbox_policy = SandboxPolicy(mode=SandboxMode(sandbox_mode.lower()))
            except ValueError:
                sandbox_policy = None
        self.bash = BashExecutor(
            timeout=self.config.optimizer.timeout_seconds,
            sandbox_policy=sandbox_policy,
        )
        # Initialize BashlingxiExecutor with no restrictions (allow all commands)
        self.bash_lingxi = BashlingxiExecutor(
            timeout=self.config.optimizer.timeout_seconds,
            blocked_commands=[],
            allowed_commands=None,  # None means no whitelist restriction
        )
        self.file_ops = FileOps()
        self.file_edit = FileEditTool()
        self.file_read = FileReadTool()
        self.grep_tool = GrepTool()
        self.registry = ToolRegistry()
        self.permissions = PermissionContext.from_config(
            deny_tools=self.config.permissions.deny_tools,
            deny_prefixes=self.config.permissions.deny_prefixes,
            mode=getattr(self.config.permissions, "mode", "ask"),
        )
        self.evaluator = StructureEvaluator()
        self.optimizer = SynchronousOptimizer()
        self.advisor = OptimizationAdvisor()

        # Register bash tool (native)
        self.registry.register(
            ToolDefinition(
                name="bash",
                description="Execute bash commands (native subprocess)",
                parameters={"command": {"type": "string"}},
                handler=self._bash_handler,
                security_scope="execute",
            )
        )

        # Register bash_lingxi tool (MCP server)
        self.registry.register(
            ToolDefinition(
                name="bash_lingxi",
                description="Execute bash commands via lingxi MCP server (secure, monitored)",
                parameters={"command": {"type": "string"}},
                handler=self._bash_lingxi_handler,
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
                is_concurrency_safe=True,  # T1-3: 只读工具可并行
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
                parameters={
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "Root directory (default: cwd)"},
                },
                handler=self._glob_handler,
                security_scope="read",
                is_concurrency_safe=True,  # T1-3: 只读工具可并行
            )
        )
        self.registry.register(
            ToolDefinition(
                name="grep",
                description="Search file contents with regex (defaults to all file types)",
                parameters={
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "Search root directory (default: cwd)"},
                    "include": {"type": "string", "description": "File glob filter (default: * = all files)"},
                    "literal": {"type": "boolean"},
                    "case_sensitive": {"type": "boolean"},
                    "before": {"type": "integer", "description": "Context lines before match (grep -B)"},
                    "after": {"type": "integer", "description": "Context lines after match (grep -A)"},
                },
                handler=self._grep_handler,
                security_scope="read",
                is_concurrency_safe=True,  # T1-3: 只读工具可并行
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
        self.registry.register(
            ToolDefinition(
                name="ast_replace",
                description="Replace function/method body at AST level (no text matching needed)",
                parameters={
                    "file_path": {"type": "string", "description": "Python file path"},
                    "function_name": {"type": "string", "description": "Function or method name"},
                    "new_body": {"type": "string", "description": "New function body (without def line)"},
                    "class_name": {"type": "string", "description": "Class name (for methods)"},
                    "occurrence": {"type": "integer", "description": "Which occurrence (default 1)"},
                },
                handler=self._ast_replace_handler,
                security_scope="write",
            )
        )
        self.registry.register(
            ToolDefinition(
                name="list_functions",
                description="List all functions and methods in a Python file with line ranges",
                parameters={
                    "file_path": {"type": "string", "description": "Python file path"},
                },
                handler=self._list_functions_handler,
                security_scope="read",
            )
        )
        self.registry.register(
            ToolDefinition(
                name="sub_agent",
                description="Spawn a sub-agent to autonomously research or analyze a task using read-only tools",
                parameters={
                    "task": {"type": "string", "description": "Task description for the sub-agent"},
                    "context": {"type": "string", "description": "Additional context (optional)"},
                    "max_rounds": {"type": "integer", "description": "Max agentic rounds (default 5)"},
                    "provider": {"type": "string", "description": "Backend provider: 'inprocess' (default) or 'acp'"},
                },
                handler=self._sub_agent_handler,
                security_scope="execute",
            )
        )
        # T1-6: 子代理控制工具
        self.registry.register(
            ToolDefinition(
                name="list_agents",
                description="List all running sub-agents and their status",
                parameters={},
                handler=self._list_agents_handler,
                security_scope="read",
            )
        )
        self.registry.register(
            ToolDefinition(
                name="interrupt_agent",
                description="Interrupt/abort a running sub-agent by agent_id",
                parameters={
                    "agent_id": {"type": "string", "description": "Agent ID to interrupt"},
                },
                handler=self._interrupt_agent_handler,
                security_scope="execute",
            )
        )
        self.registry.register(
            ToolDefinition(
                name="send_message",
                description="Send a message to a running sub-agent (ACP backend only)",
                parameters={
                    "agent_id": {"type": "string", "description": "Agent ID to send message to"},
                    "message": {"type": "string", "description": "Message content"},
                },
                handler=self._send_message_handler,
                security_scope="execute",
            )
        )
        self.registry.register(
            ToolDefinition(
                name="plan_mode",
                description="Toggle plan mode: think without tool execution to plan complex tasks",
                parameters={
                    "action": {"type": "string", "description": "'enter' or 'exit'"},
                },
                handler=self._plan_mode_handler,
                security_scope="read",
            )
        )
        self.registry.register(
            ToolDefinition(
                name="web_fetch",
                description="Fetch content from a URL and extract text",
                parameters={
                    "url": {"type": "string", "description": "URL to fetch"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)"},
                },
                handler=self._web_fetch_handler,
                security_scope="read",
            )
        )
        self.registry.register(
            ToolDefinition(
                name="web_search",
                description="Search the web for information",
                parameters={
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "description": "Max results to return (default 5)"},
                },
                handler=self._web_search_handler,
                security_scope="read",
            )
        )
        # P0-1: todo tool
        self.registry.register(
            ToolDefinition(
                name="todo",
                description="Task management: create/list/complete/cancel/start/delete a todo item",
                parameters={
                    "command": {"type": "string", "description": "Sub-command: create|list|complete|cancel|start|get|delete"},
                    "id": {"type": "string", "description": "Todo ID (for complete/cancel/start/get/delete)"},
                    "content": {"type": "string", "description": "Task content (for create)"},
                    "priority": {"type": "integer", "description": "Priority 0-9, higher=more urgent (for create)"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags (for create)"},
                    "status": {"type": "string", "description": "Filter by status: pending|in_progress|completed|cancelled (for list)"},
                },
                handler=self._todo_handler,
                security_scope="read",
            )
        )
        # P0-3: request_user_input tool
        self.registry.register(
            ToolDefinition(
                name="request_user_input",
                description="Request user input during agent loop (single/multiple/text modes)",
                parameters={
                    "header": {"type": "string", "description": "Optional header label"},
                    "question": {"type": "string", "description": "The question to ask the user"},
                    "mode": {"type": "string", "description": "Input mode: single (one answer) | multiple (comma-separated) | text (free-form)"},
                    "options": {"type": "array", "items": {"type": "object", "properties": {"label": {"type": "string"}, "description": {"type": "string"}}, "required": ["label"]}, "description": "Choice options for single/multiple mode"},
                },
                handler=self._request_user_input_handler,
                security_scope="read",
            )
        )
        # P1-1: LSP tool
        self.registry.register(
            ToolDefinition(
                name="lsp",
                description="LSP code navigation: goto_def / find_refs / hover / goto_impl",
                parameters={
                    "command": {"type": "string", "description": "lsp command: goto_def|find_refs|hover|goto_impl"},
                    "file_path": {"type": "string", "description": "Absolute file path"},
                    "line": {"type": "integer", "description": "0-based line number"},
                    "character": {"type": "integer", "description": "0-based character offset"},
                },
                handler=self._lsp_handler,
                security_scope="read",
            )
        )
        self._plan_mode_active: bool = False
        # LINGKERNEL_v1 #2: 5 段 pipeline (dsh tool-execution-pipeline 对位)
        self.tool_pipeline = ToolPipeline(
            self.registry,
            write_scoped_tools=WRITE_SCOPED_TOOLS,
            critical_tools=CRITICAL_TOOLS,
            timeout_seconds=self.config.optimizer.timeout_seconds,
        )
        # T0-1: plan_mode 接入 — 用于过滤写工具
        from lingclaude.engine.plan_mode import PlanMode
        self.plan_mode = PlanMode()
        # T0-2: 敏感路径门接入 pipeline — 覆盖全部带路径参数的工具（write/edit/ast_replace 等）
        self.tool_pipeline.add_guard(self._sensitive_path_guard)

    def _bash_handler(self, command: str, **_kwargs: Any) -> dict[str, Any]:
        # T0-2: sensitive_path_gate 检查 bash 命令中的路径
        from lingclaude.engine.sensitive_path_gate import check_sensitive_path
        # P2-1 收紧：只提取路径形 token（~ / ./ ../ 前缀），且 / 前不是单词字符
        # （否则 echo "abc/def" 会把 /def 误当路径；引号内真实路径 cat "/home/x" 仍会命中）
        import re
        paths_in_cmd = re.findall(r'(?:~|(?<![\w])/|\.\.?/)[\w./~-]+', command)
        for p in paths_in_cmd:
            if len(p) > 1:
                is_sensitive, reason = check_sensitive_path(p)
                if is_sensitive:
                    return {"error": f"Path blocked by sensitive_path_gate in command: {p} ({reason})"}
        result = self.bash.run(command)
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration": result.duration,
        }

    def _bash_lingxi_handler(self, command: str, **_kwargs: Any) -> dict[str, Any]:
        result = self.bash_lingxi.run(command)
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
        # T0-2: sensitive_path_gate 检查（带 T0-3 审批逃生门）
        gated = self._gate_sensitive("read", path)
        if gated:
            return {"error": gated}
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

    def _glob_handler(self, pattern: str, path: str | None = None, **_kwargs: Any) -> dict[str, Any]:
        # T0-2: sensitive_path_gate 检查（带 T0-3 审批逃生门）
        gated = self._gate_sensitive("glob", pattern, path)
        if gated:
            return {"error": gated}
        result = self.file_ops.glob(pattern, path=path)
        if result.is_error:
            return {"error": result.error}
        return {"files": result.data}

    def _grep_handler(
        self,
        pattern: str,
        path: str | None = None,
        include: str | None = None,
        literal: bool = False,
        case_sensitive: bool = True,
        before: int = 0,
        after: int = 0,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        # T0-2: sensitive_path_gate 检查（带 T0-3 审批逃生门）
        gated = self._gate_sensitive("grep", pattern, path, include)
        if gated:
            return {"error": gated}
        result = self.grep_tool.search(
            pattern, include=include, literal=literal, case_sensitive=case_sensitive,
            path=path, before=before, after=after,
        )
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

    def _ast_replace_handler(
        self,
        file_path: str,
        function_name: str,
        new_body: str,
        class_name: str | None = None,
        occurrence: int = 1,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        result = replace_function_body(
            file_path, function_name, new_body,
            class_name=class_name, occurrence=occurrence,
        )
        if result.is_error:
            return {"error": result.error}
        return result.data.to_dict()

    def _list_functions_handler(
        self,
        file_path: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        result = list_functions(file_path)
        if result.is_error:
            return {"error": result.error}
        return {"functions": result.data}

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
        from lingclaude.engine.subagent import SubagentManager
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
        from lingclaude.engine.subagent import SubagentManager
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

    def _send_message_handler(self, agent_id: str, message: str, **_kwargs: Any) -> dict[str, Any]:
        """T1-6: 向运行中的子代理发送消息（AcpSubagentBackend 支持）."""
        from lingclaude.engine.subagent import SubagentManager
        manager = getattr(self, "_subagent_manager", None)
        if manager is None:
            return {"success": False, "error": "No subagent manager"}
        # 仅 ACP 后端支持 send_message
        acp = manager.get_backend("acp")
        if not hasattr(acp, '_running') or agent_id not in acp._running:
            return {"success": False, "agent_id": agent_id, "error": "Agent not found or not running on ACP"}
        # ACP send_message 实现（简化版：记录消息，实际由 ACP server 处理）
        acp._running[agent_id]["last_message"] = message
        return {"success": True, "agent_id": agent_id, "message": "Message recorded"}

    def _plan_mode_handler(self, action: str = "enter", **_kwargs: Any) -> dict[str, Any]:
        if action == "enter":
            self.plan_mode.enter()
            self._plan_mode_active = True
            return {"plan_mode": True, "message": "Plan mode activated. Tool execution disabled."}
        elif action == "exit":
            self.plan_mode.exit()
            self._plan_mode_active = False
            return {"plan_mode": False, "message": "Plan mode deactivated. Tool execution enabled."}
        return {"error": f"Unknown action: {action}. Use 'enter' or 'exit'."}

    def _web_fetch_handler(
        self,
        url: str,
        timeout: int = 30,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        from lingclaude.engine.web_tools import WebFetcher
        fetcher = WebFetcher(timeout=timeout)
        result = fetcher.fetch(url)
        if result.is_error:
            return {"error": result.error}
        return {"url": url, "content": result.data}

    def _todo_handler(
        self,
        command: str,
        id: str | None = None,
        content: str | None = None,
        priority: int = 0,
        tags: list[str] | None = None,
        status: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """P0-1: Todo list tool dispatcher."""
        h = self._todo_handlers
        cmd = command.lower()
        if cmd == "create":
            return h["create"](content=content, priority=priority, tags=tags)
        if cmd == "list":
            return h["list"](status=status, tags=tags)
        if cmd == "complete":
            return h["complete"](id=id)
        if cmd == "cancel":
            return h["cancel"](id=id)
        if cmd == "start":
            return h["start"](id=id)
        if cmd == "get":
            return h["get"](id=id)
        if cmd == "delete":
            return h["delete"](id=id)
        return {"ok": False, "error": f"unknown command: {command}"}

    def _lsp_handler(
        self,
        command: str,
        file_path: str,
        line: int = 0,
        character: int = 0,
        **_: Any,
    ) -> dict[str, Any]:
        """P1-1: LSP dispatcher — routes to StdioLspProvider on first use."""
        import asyncio
        from pathlib import Path

        cmd = command.lower()
        if cmd not in ("goto_def", "find_refs", "hover", "goto_impl"):
            return {"ok": False, "error": f"unknown LSP command: {command}"}

        # Lazy-init LSP provider
        if self._lsp_provider is None:
            # Auto-detect workspace root: find .git / pyproject.toml / Cargo.toml
            cwd = Path(file_path).resolve().parent
            for parent in [cwd, *cwd.parents]:
                if (parent / "pyproject.toml").exists():
                    self._lsp_workspace_root = parent
                    server = StdioLspProvider(["pylsp"], workspace_root=parent)
                    break
                if (parent / "Cargo.toml").exists():
                    self._lsp_workspace_root = parent
                    server = StdioLspProvider(["rust-analyzer"], workspace_root=parent)
                    break
            else:
                self._lsp_workspace_root = cwd
                server = StdioLspProvider(["pylsp"], workspace_root=cwd)
            self._lsp_provider = server

        provider = self._lsp_provider

        async def run():
            if not getattr(provider, "_initialized", False):
                await provider.initialize(self._lsp_workspace_root)
                provider._initialized = True
            if cmd == "goto_def":
                return await provider.go_to_definition(file_path, line, character)
            if cmd == "find_refs":
                return await provider.find_references(file_path, line, character)
            if cmd == "hover":
                return await provider.hover(file_path, line, character)
            return await provider.go_to_implementation(file_path, line, character)

        try:
            result = asyncio.run(run())
            return {
                "ok": True,
                "command": cmd,
                "result": [
                    {"uri": loc.uri, "line": loc.range.start.line, "col": loc.range.start.character}
                    for loc in (result if isinstance(result, list) else [])
                ],
            }
        except Exception as e:
            return {"ok": False, "error": f"lsp error: {e}"}

    # P0-3: request_user_input tool — registered below after _setup_tools
    def _request_user_input_handler(
        self,
        header: str | None = None,
        question: str | None = None,
        mode: str = "single",
        options: list[dict[str, str]] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """P0-3: Request user input during agent loop.

        对标 AtomCode request_user_input / DSH user-questions。
        当前实现：打印提示到 stdout 并等待 STDIN 读入。
        后续可升级为 FastAPI / WebSocket 推送通知到前端。
        """
        prompt_parts = []
        if header:
            prompt_parts.append(f"[{header}]")
        if question:
            prompt_parts.append(question)
        prompt = " ".join(prompt_parts) or "请输入："

        if mode == "single" and options:
            # single 模式：渲染选项列表
            prompt += "\n"
            for i, opt in enumerate(options, 1):
                label = opt.get("label", opt.get("description", f"选项{i}"))
                prompt += f"  {i}. {label}\n"
            prompt += "请输入选项编号或直接回答："
        elif mode == "multiple" and options:
            prompt += "\n"
            for i, opt in enumerate(options, 1):
                label = opt.get("label", opt.get("description", f"选项{i}"))
                prompt += f"  {i}. {label}\n"
            prompt += "请输入选项编号（多个用逗号分隔）："

        print(prompt, flush=True)
        try:
            answer = input().strip()
        except (EOFError, KeyboardInterrupt):
            return {"ok": False, "error": "input cancelled", "answer": None}

        # 解析选项编号
        selected: str | list[str] = answer
        if options and answer:
            # 尝试解析为编号
            try:
                if "," in answer:
                    indices = [int(x.strip()) for x in answer.split(",")]
                    selected = [options[i - 1]["label"] for i in indices if 0 < i <= len(options)]
                else:
                    idx = int(answer)
                    if 0 < idx <= len(options):
                        selected = options[idx - 1]["label"]
            except ValueError:
                pass  # 非数字，原样返回
        return {"ok": True, "answer": selected, "mode": mode}

    def _web_search_handler(
        self,
        query: str,
        max_results: int = 5,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        from lingclaude.engine.web_tools import WebSearcher
        searcher = WebSearcher()
        result = searcher.search(query, max_results=max_results)
        if result.is_error:
            return {"error": result.error}
        return {"query": query, "results": result.data}

    def _tool_scope(self, name: str) -> str:
        """工具的 security_scope（plan_mode 判定用）。"""
        tool = self.registry.get(name)
        return tool.data.security_scope if tool.is_ok else "read"

    @property
    def permission_store(self) -> PermissionStore:
        """T0-3: 当前会话的审批 store — webUI /permission 决策的落点与查询入口。"""
        from lingclaude.core.permissions import PermissionStore, get_permission_store

        store: PermissionStore = get_permission_store(getattr(self.config, "session_id", "default"))
        return store

    def _gate_sensitive(self, name: str, *candidates: str | None) -> str | None:
        """T0-2: 敏感路径门 — 命中敏感标记且无审批放行时返回错误信息（fail-closed）。

        T0-3 逃生门：会话内对该工具做过 allow/always_allow 决策则放行。
        """
        from lingclaude.engine.sensitive_path_gate import check_sensitive_path
        from lingclaude.core.permissions import get_permission_store

        for cand in candidates:
            if not cand:
                continue
            is_sensitive, reason = check_sensitive_path(str(cand))
            if is_sensitive:
                store = get_permission_store(getattr(self.config, "session_id", "default"))
                if store.explicitly_allowed(name):
                    return None
                return (
                    f"Path blocked by sensitive_path_gate: {cand} ({reason}；"
                    f"如需访问请通过审批放行 {name})"
                )
        return None

    def _sensitive_path_guard(self, tool_def: Any, ctx: Any) -> Any:
        """T0-2: pipeline 守卫 — 全部工具的 path/file_path 参数检查。

        read/glob/grep 的 pattern 级检查在各自 handler 内（含本守卫同一逃生门）。
        """
        from lingclaude.engine.tool_pipeline import GuardDecision

        candidates = [ctx.args.get("path"), ctx.args.get("file_path")]
        err = self._gate_sensitive(tool_def.name, *(str(c) for c in candidates if c))
        if err:
            return GuardDecision(decision="deny", reason=err)
        return GuardDecision(decision="abstain")

    def execute_tool(self, name: str, **kwargs: Any) -> dict[str, Any]:
        # LINGKERNEL_v1 #2: 5 段 pipeline (pre-execute -> guards -> execute -> post -> finalize)
        # 旧 inline 实现保留为 _execute_tool_legacy, pipeline 透传原有校验逻辑
        def _pre_write_verify(tool_name: str, file_path: Any, content: Any) -> tuple[bool, str]:
            if not self.verification_gate.enabled:
                return True, ""
            pre = self.verification_gate.verify(tool_name, file_path=file_path, content=content)
            if pre.passed:
                return True, ""
            failed = [c for c in pre.checks if not c.get("passed", True)]
            return False, f"[验证关卡] 写入被阻止: {pre.error} | failed_checks={failed}"

        def _post_write_verify(file_path: Any) -> tuple[bool, str]:
            if not self.verification_gate.enabled:
                return True, ""
            if not file_path:
                return True, ""
            post = self.verification_gate.verify_post_write(file_path)
            if post.passed:
                return True, ""
            failed = [c for c in post.checks if not c.get("passed", True)]
            return False, f"[验证关卡] 写入后验证失败: {post.error} | failed_checks={failed}"

        rate = self.verification_gate.check_rate_limit()
        rate_ok = rate.passed
        rate_err = ("[安全限制] " + (getattr(rate, "error", "") or "rate limited")) if not rate_ok else ""

        # T0-1: plan_mode 拦截 — 只放行读域工具 + plan_mode 自身（bash 等 execute 域同封）
        if self.plan_mode.is_active and not self.plan_mode.allows(name, self._tool_scope(name)):
            return {"error": f"Tool '{name}' blocked by plan_mode (read-only mode active; call plan_mode with action=exit to leave)"}

        # T0-3: 审批回路 — webUI /permission 决策实时回灌
        # deny 决策 → 拦截；always_allow → 覆盖静态 deny_names
        from lingclaude.core.permissions import (
            READ_ONLY_TOOLS,
            get_permission_mode,
            get_permission_store,
        )

        store = get_permission_store(getattr(self.config, "session_id", "default"))
        # T1-2 深化: 全局 mode 优先（webUI /permission/mode 可实时切换，覆盖静态 config mode）
        active_mode = get_permission_mode()

        def _blocks(tool_name: str) -> bool:
            if store.blocks(tool_name):
                return True
            if active_mode == "auto" and not store.blocks(tool_name):
                # auto 模式：非 deny 全部放行（写工具也放行）
                return False
            if self.permissions.blocks(tool_name) and not store.explicitly_allowed(tool_name):
                return True
            if active_mode == "strict" and tool_name not in READ_ONLY_TOOLS:
                # strict 模式：非只读工具一律拦截（除非显式放行）
                return not store.explicitly_allowed(tool_name)
            return False

        return self.tool_pipeline.execute(
            name,
            kwargs,
            permissions_blocks=_blocks,
            rate_check=lambda: (rate_ok, rate_err),
            pre_write_verify=_pre_write_verify,
            post_write_verify=_post_write_verify,
        )

    def _execute_tool_legacy(self, name: str, **kwargs: Any) -> dict[str, Any]:
        if self.permissions.blocks(name):
            return {"error": f"Tool blocked by permissions: {name}"}

        rate = self.verification_gate.check_rate_limit()
        if not rate.passed:
            return {"error": f"[安全限制] {rate.error}"}

        if name in WRITE_SCOPED_TOOLS and self.verification_gate.enabled:
            file_path = kwargs.get("path") or kwargs.get("file_path")
            content = kwargs.get("content") or kwargs.get("new_text") or kwargs.get("new_body")
            pre = self.verification_gate.verify(name, file_path=file_path, content=content)
            if not pre.passed:
                return {"error": f"[验证关卡] 写入被阻止: {pre.error}", "verification": {"passed": False, "checks": [c for c in pre.checks if not c.get("passed", True)]}}

        if name in CRITICAL_TOOLS:
            command = kwargs.get("command", "")
            dangerous_patterns = ("rm -rf /", "mkfs", "dd if=", "> /dev/sd", "chmod 777 /", ":(){:|:&};:")
            for pat in dangerous_patterns:
                if pat in command:
                    return {"error": f"[安全限制] 危险命令被阻止: 含有 '{pat}'"}

        result = self.registry.execute(name, **kwargs)
        if result.is_error:
            return {"error": result.error}
        data = result.data

        if name in WRITE_SCOPED_TOOLS and self.verification_gate.enabled:
            file_path = kwargs.get("path") or kwargs.get("file_path")
            if file_path:
                post = self.verification_gate.verify_post_write(file_path)
                if not post.passed:
                    return {"error": f"[验证关卡] 写入后验证失败: {post.error}", "verification": {"passed": False, "checks": [c for c in post.checks if not c.get("passed", True)]}}

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
