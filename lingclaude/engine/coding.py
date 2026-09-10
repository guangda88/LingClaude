from __future__ import annotations

from pathlib import Path
from typing import Any

from lingclaude.core.config import lingclaudeConfig
from lingclaude.core.model_call import _ToolLoopDetector
from lingclaude.core.permissions import PermissionContext, PermissionStore
from lingclaude.core.session_runtime import SessionRuntime
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
from lingclaude.engine.tool_handlers import (
    BackgroundToolsMixin,
    BashToolsMixin,
    FileToolsMixin,
    GitToolsMixin,
    LspToolsMixin,
    PlanToolsMixin,
    SearchToolsMixin,
    SubagentToolsMixin,
    TodoToolsMixin,
    WebToolsMixin,
)


class CodingRuntime(
    BashToolsMixin, FileToolsMixin, SearchToolsMixin, GitToolsMixin,
    SubagentToolsMixin, BackgroundToolsMixin, WebToolsMixin,
    PlanToolsMixin, TodoToolsMixin, LspToolsMixin,
):
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

        # P0.2 (E1 修活熔断): 5b 分支的真实依赖 — 此前从未初始化,
        # execute_tool._blocks 里对 _session_runtime/_loop_detector 的双重
        # hasattr 永远为 False，observe_denial 熔断是死代码（V3 §三 E1）。
        self._loop_detector = _ToolLoopDetector()
        self._denial_abort_log: str | None = None
        # 5b 第一道门: log_denial 桥（复用 SessionRuntime→DataFlywheel，不造新文件）。
        # session_id 兜底 "default"，与 execute_tool 里 permission store 的取法一致。
        self.session_id = getattr(self.config, "session_id", "default")
        self._session_runtime = SessionRuntime(self)

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
        # P0-1: run_in_background 后台任务（对标 DSH jobs / CC 后台 shell 简化版）
        self.registry.register(
            ToolDefinition(
                name="run_in_background",
                description="Run a bash command in the background, returns a job_id immediately",
                parameters={
                    "command": {"type": "string", "description": "Bash command to run in background"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default 300)"},
                },
                handler=self._run_in_background_handler,
                security_scope="execute",
            )
        )
        self.registry.register(
            ToolDefinition(
                name="list_jobs",
                description="List all background jobs and their status",
                parameters={},
                handler=self._list_jobs_handler,
                security_scope="read",
            )
        )
        self.registry.register(
            ToolDefinition(
                name="job_status",
                description="Get status of a background job by job_id",
                parameters={
                    "job_id": {"type": "string", "description": "Job ID to query"},
                },
                handler=self._job_status_handler,
                security_scope="read",
            )
        )
        # P1 解耦示范: 先注册 handler 到 HandlerRegistry，再以 handler_name 引用
        # （定义与实现解耦，换 handler 只需 register_handler 覆盖，不改 ToolDefinition）
        self.registry.register_handler("cancel_job_handler", self._cancel_job_handler)
        self.registry.register(
            ToolDefinition(
                name="cancel_job",
                description="Cancel a pending/running background job by job_id",
                parameters={
                    "job_id": {"type": "string", "description": "Job ID to cancel"},
                },
                handler_name="cancel_job_handler",
                security_scope="execute",
            )
        )
        # T1-6: send_message 已撤下 — ACP run() 是同步单轮（POST /message 等完整结果返回），
        # _running 里注册的是已完成结果、不持有 session_id，没有可投递的活会话；
        # 原实现只写 _running[id]["last_message"] 就返回 success（假成功）。
        # 待 ACP 后端支持异步会话后再真实现并重新注册。
        self.registry.register_handler("plan_mode_handler", self._plan_mode_handler)
        self.registry.register(
            ToolDefinition(
                name="plan_mode",
                description="Toggle plan mode: think without tool execution to plan complex tasks",
                parameters={
                    "action": {"type": "string", "description": "'enter' or 'exit'"},
                },
                handler_name="plan_mode_handler",
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
            # 注意（harness fix 2026-09-02）：原 `store.blocks(tool_name)` 在
            # 单例 store 与 runtime ctx 分离时会被模式污染或审批回灌吞掉。
            # 改用「runtime 静态配置 ∪ store 运行时 ctx」并集作为唯一真源；
            # 显式放行 (always_allow) 优先于 deny 翻转（业务约定）。
            effective_deny = self.permissions.deny_names | store.context.deny_names
            allowed = store.explicitly_allowed(tool_name)
            blocked = False
            rule_id = "no_match"
            if tool_name.lower() in effective_deny and not allowed:
                blocked = True
                rule_id = "config.deny_tools.exact" if tool_name.lower() in self.permissions.deny_names else "session.deny_tools.exact"
            elif active_mode == "auto":
                blocked = False
            elif active_mode == "strict" and tool_name not in READ_ONLY_TOOLS:
                blocked = not allowed
                if blocked:
                    rule_id = "strict_mode.non_readonly"
            # R2：把 denial 喂给 DataFlywheel（不造新文件），让"同类 denial"
            # 统计可查（SYSTEMS_THEORY §一.4 摩擦台账 + R1 熔断前置）。
            # 5b：按 rule_id 喂 _ToolLoopDetector 熔断（执行层硬规则,非推理层）。
            if blocked and hasattr(self, "_session_runtime"):
                try:
                    self._session_runtime.log_denial(
                        denial_kind=rule_id,
                        command_prefix=str(tool_name),
                        reason=(
                            f"mode={active_mode}; "
                            f"in_deny={tool_name.lower() in effective_deny}; "
                            f"explicitly_allowed={allowed}"
                        ),
                    )
                    if hasattr(self, "_loop_detector"):
                        # 5b：单次触发不烧轮次,与"denial key 2 次→暂停+升级用户"对齐
                        verdict = self._loop_detector.observe_denial(rule_id, tool_name, threshold=2)
                        if verdict == "denial_abort":
                            # P0.2: 写实例属性（__init__ 已初始化），execute_tool
                            # 返回前消费；原写 _loop_detector._denial_abort_log 全库无消费者。
                            self._denial_abort_log = (
                                f"denial_abort: rule_id={rule_id} tool={tool_name} "
                                f"consecutive={self._loop_detector._denial_streak.get(rule_id, 0)}"
                            )
                except Exception:  # noqa: BLE001 — 飞轮/熔断写入失败不阻塞拦截语义
                    pass
            return blocked

        result = self.tool_pipeline.execute(
            name,
            kwargs,
            permissions_blocks=_blocks,
            rate_check=lambda: (rate_ok, rate_err),
            pre_write_verify=_pre_write_verify,
            post_write_verify=_post_write_verify,
        )
        # P0.2: 5b 熔断触发后把信号挂到本次工具结果上（模型可见），消费即清零,
        # 防止旧信号泄漏到后续无关调用。
        if getattr(self, "_denial_abort_log", None):
            result.setdefault("denial_circuit_breaker", self._denial_abort_log)
            self._denial_abort_log = None
        return result

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
