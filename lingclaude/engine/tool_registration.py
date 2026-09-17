"""工具注册 specs 表 — 由 scripts/extract_tool_specs.py 从 coding.py AST 机械提取。

T4（opencode 架构演进项）：注册数据与运行时解耦 —— coding.py 主干不再内联
32 条 ToolDefinition，改由 register_all_tools(registry, runtime) 消费本表。
G8 守卫锁定「主干零注册」。

字段说明：
- handler_attr: runtime 上的私有 handler 方法名（register_all_tools 负责按名绑定）
- concurrency_safe: True = 只读工具，可被 pipeline 并行调度（T1-3）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lingclaude.engine.tools import ToolDefinition


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler_attr: str
    security_scope: str
    concurrency_safe: bool = False



SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name='bash',
        description='Execute bash commands (native subprocess)',
        parameters={'command': {'type': 'string'}},
        handler_attr='_bash_handler',
        security_scope='execute',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='bash_lingxi',
        description='Execute bash commands via lingxi MCP server (secure, monitored)',
        parameters={'command': {'type': 'string'}},
        handler_attr='_bash_lingxi_handler',
        security_scope='execute',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='read',
        description='Read file contents with line numbers, offset/limit support',
        parameters={'path': {'type': 'string'}, 'offset': {'type': 'integer'}, 'limit': {'type': 'integer'}, 'line_numbers': {'type': 'boolean'}},
        handler_attr='_read_handler',
        security_scope='read',
        concurrency_safe=True,
    ),
    ToolSpec(
        name='write',
        description='Write file contents',
        parameters={'path': {'type': 'string'}, 'content': {'type': 'string'}},
        handler_attr='_write_handler',
        security_scope='write',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='edit',
        description='Edit file by replacing text (with backup/rollback)',
        parameters={'path': {'type': 'string'}, 'old_text': {'type': 'string'}, 'new_text': {'type': 'string'}, 'replace_all': {'type': 'boolean'}},
        handler_attr='_edit_handler',
        security_scope='write',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='file_create',
        description='Create a new file with content',
        parameters={'path': {'type': 'string'}, 'content': {'type': 'string'}},
        handler_attr='_file_create_handler',
        security_scope='write',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='file_insert',
        description='Insert text at a specific line number',
        parameters={'path': {'type': 'string'}, 'line': {'type': 'integer'}, 'text': {'type': 'string'}},
        handler_attr='_file_insert_handler',
        security_scope='write',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='file_delete_lines',
        description='Delete a range of lines from a file',
        parameters={'path': {'type': 'string'}, 'start_line': {'type': 'integer'}, 'end_line': {'type': 'integer'}},
        handler_attr='_file_delete_lines_handler',
        security_scope='write',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='file_undo',
        description='Undo last edit by restoring backup',
        parameters={'path': {'type': 'string'}},
        handler_attr='_file_undo_handler',
        security_scope='write',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='glob',
        description='Find files by pattern',
        parameters={'pattern': {'type': 'string'}, 'path': {'type': 'string', 'description': 'Root directory (default: cwd)'}},
        handler_attr='_glob_handler',
        security_scope='read',
        concurrency_safe=True,
    ),
    ToolSpec(
        name='grep',
        description='Search file contents with regex (defaults to all file types)',
        parameters={'pattern': {'type': 'string'}, 'path': {'type': 'string', 'description': 'Search root directory (default: cwd)'}, 'include': {'type': 'string', 'description': 'File glob filter (default: * = all files)'}, 'literal': {'type': 'boolean'}, 'case_sensitive': {'type': 'boolean'}, 'before': {'type': 'integer', 'description': 'Context lines before match (grep -B)'}, 'after': {'type': 'integer', 'description': 'Context lines after match (grep -A)'}},
        handler_attr='_grep_handler',
        security_scope='read',
        concurrency_safe=True,
    ),
    ToolSpec(
        name='stt',
        description='Record audio and transcribe to text',
        parameters={'duration': {'type': 'integer', 'description': 'Recording duration in seconds'}, 'file': {'type': 'string', 'description': 'Audio file path to transcribe (optional)'}, 'backend': {'type': 'string', 'description': 'STT backend: whisper or sherpa_onnx'}},
        handler_attr='_stt_handler',
        security_scope='execute',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='git_status',
        description='Show working tree status (short/porcelain format)',
        parameters={'path': {'type': 'string', 'description': 'Repository path (default: .)'}},
        handler_attr='_git_status_handler',
        security_scope='read',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='git_diff',
        description='Show staged or unstaged changes',
        parameters={'path': {'type': 'string', 'description': 'Repository path'}, 'target': {'type': 'string', 'description': 'Specific file or directory to diff'}, 'staged': {'type': 'boolean', 'description': 'Show staged changes (--staged)'}, 'stat': {'type': 'boolean', 'description': 'Show diffstat summary'}},
        handler_attr='_git_diff_handler',
        security_scope='read',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='git_log',
        description='Show commit history',
        parameters={'path': {'type': 'string', 'description': 'Repository path'}, 'count': {'type': 'integer', 'description': 'Number of commits (default 10)'}, 'follow': {'type': 'string', 'description': 'Follow file renames for a specific file'}},
        handler_attr='_git_log_handler',
        security_scope='read',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='git_blame',
        description='Show line-level authorship for a file',
        parameters={'file_path': {'type': 'string', 'description': 'File to blame'}, 'cwd': {'type': 'string', 'description': 'Repository root'}, 'start_line': {'type': 'integer', 'description': 'Start line (optional)'}, 'end_line': {'type': 'integer', 'description': 'End line (optional)'}},
        handler_attr='_git_blame_handler',
        security_scope='read',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='git_push',
        description='Push commits to a remote (parameterized, remote/branch allowlist, no shell interpolation)',
        parameters={'path': {'type': 'string', 'description': 'Repository path (default: .)'}, 'remote': {'type': 'string', 'description': 'Remote name (allowlist: origin/github/upstream)'}, 'branch': {'type': 'string', 'description': 'Branch/refspec (allowlist chars only)'}, 'force': {'type': 'boolean', 'description': 'Force push (default false)'}, 'timeout': {'type': 'integer', 'description': 'Timeout seconds (default 120)'}},
        handler_attr='_git_push_handler',
        security_scope='execute',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='git_push_preflight',
        description='Preflight check before push: remotes, unpushed commits, dirty state, gate blocking',
        parameters={'path': {'type': 'string', 'description': 'Repository path (default: .)'}},
        handler_attr='_git_push_preflight_handler',
        security_scope='read',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='index_project',
        description='Scan Python project and build symbol table (classes, functions, imports)',
        parameters={'path': {'type': 'string', 'description': 'Project root directory'}, 'max_files': {'type': 'integer', 'description': 'Max files to scan (default 200)'}},
        handler_attr='_index_project_handler',
        security_scope='read',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='ast_replace',
        description='Replace function/method body at AST level. Decision rules: (1) Prefer this over edit when replacing a whole function/method body — no text matching needed. (2) new_body must NOT include the "def" line, use indent of the body only (method bodies indented). (3) For methods pass class_name; use occurrence>1 only after list_functions confirms multiple candidates.',
        parameters={'file_path': {'type': 'string', 'description': 'Python file path'}, 'function_name': {'type': 'string', 'description': 'Function or method name'}, 'new_body': {'type': 'string', 'description': 'New function body (without def line)'}, 'class_name': {'type': 'string', 'description': 'Class name (for methods)'}, 'occurrence': {'type': 'integer', 'description': 'Which occurrence (default 1)'}},
        handler_attr='_ast_replace_handler',
        security_scope='write',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='list_functions',
        description='List all functions and methods in a Python file with line ranges',
        parameters={'file_path': {'type': 'string', 'description': 'Python file path'}},
        handler_attr='_list_functions_handler',
        security_scope='read',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='sub_agent',
        description='Spawn a sub-agent to autonomously research or analyze a task using read-only tools',
        parameters={'task': {'type': 'string', 'description': 'Task description for the sub-agent'}, 'context': {'type': 'string', 'description': 'Additional context (optional)'}, 'max_rounds': {'type': 'integer', 'description': 'Max agentic rounds (default 5)'}, 'provider': {'type': 'string', 'description': "Backend provider: 'inprocess' (default) or 'acp'"}},
        handler_attr='_sub_agent_handler',
        security_scope='execute',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='list_agents',
        description='List all running sub-agents and their status',
        parameters={},
        handler_attr='_list_agents_handler',
        security_scope='read',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='interrupt_agent',
        description='Interrupt/abort a running sub-agent by agent_id',
        parameters={'agent_id': {'type': 'string', 'description': 'Agent ID to interrupt'}},
        handler_attr='_interrupt_agent_handler',
        security_scope='execute',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='run_in_background',
        description='Run a bash command in the background, returns a job_id immediately',
        parameters={'command': {'type': 'string', 'description': 'Bash command to run in background'}, 'timeout': {'type': 'integer', 'description': 'Timeout in seconds (default 300)'}},
        handler_attr='_run_in_background_handler',
        security_scope='execute',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='list_jobs',
        description='List all background jobs and their status',
        parameters={},
        handler_attr='_list_jobs_handler',
        security_scope='read',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='job_status',
        description='Get status of a background job by job_id',
        parameters={'job_id': {'type': 'string', 'description': 'Job ID to query'}},
        handler_attr='_job_status_handler',
        security_scope='read',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='cancel_job',
        description='Cancel a pending/running background job by job_id',
        parameters={'job_id': {'type': 'string', 'description': 'Job ID to cancel'}},
        handler_attr='_cancel_job_handler',
        security_scope='execute',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='plan_mode',
        description='Toggle plan mode: think without tool execution to plan complex tasks',
        parameters={'action': {'type': 'string', 'description': "'enter' or 'exit'"}},
        handler_attr='_plan_mode_handler',
        security_scope='read',
        concurrency_safe=False,
    ),
    ToolSpec(
        # 2026-09-17 第3级a: 任务自动拆解（对标 AtomCode todowrite）。
        # 模型遇多步任务时调用：传完整任务清单 + 唯一 active_id（当前执行项），
        # 全量覆写进 session 级 TodoStore，其余项退回 pending（状态纪律①）。
        # 用户经 /tasks 查看面板，进度在对话中由模型调用本工具推进。
        name='todo_write',
        description=(
            'Replace the session task list with a plan of concrete, verifiable '
            'sub-steps for multi-step work. Pass `todos` (list of {content, status}) '
            'and `active_id` = the content of the single in_progress item (exactly '
            'one; others must be pending). Call at task start and again as items '
            'complete. Use for multi-step/ambiguous work, not simple one-off edits.'
        ),
        parameters={
            'todos': {
                'type': 'array',
                'description': 'Full task list (replaces previous). Each item: {"content": "one verifiable action", "status": "pending|in_progress|completed"}.',
                'items': {
                    'type': 'object',
                    'properties': {
                        'content': {'type': 'string', 'description': 'Concrete task, e.g. "add retry to fetch_user"'},
                        'status': {'type': 'string', 'enum': ['pending', 'in_progress', 'completed']},
                    },
                    'required': ['content', 'status'],
                },
            },
            'active_id': {
                'type': 'string',
                'description': 'Content of the ONE in_progress item ("none" if all pending/completed). Exactly one unless "none".',
            },
        },
        handler_attr='_todo_write_handler',
        # 2026-09-17 修复: 原 'read' —— todo_write 全量删除+重插任务存储，
        # 是状态写入型工具，挂 read scope 绕开了 write 闸门语义。
        security_scope='write',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='web_fetch',
        description='Fetch content from a URL and extract text',
        parameters={'url': {'type': 'string', 'description': 'URL to fetch'}, 'timeout': {'type': 'integer', 'description': 'Timeout in seconds (default 30)'}},
        handler_attr='_web_fetch_handler',
        security_scope='read',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='web_search',
        description='Search the web for information',
        parameters={'query': {'type': 'string', 'description': 'Search query'}, 'max_results': {'type': 'integer', 'description': 'Max results to return (default 5)'}},
        handler_attr='_web_search_handler',
        security_scope='read',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='todo',
        description='Task management: create/list/complete/cancel/start/delete a todo item',
        parameters={'command': {'type': 'string', 'description': 'Sub-command: create|list|complete|cancel|start|get|delete'}, 'id': {'type': 'string', 'description': 'Todo ID (for complete/cancel/start/get/delete)'}, 'content': {'type': 'string', 'description': 'Task content (for create)'}, 'priority': {'type': 'integer', 'description': 'Priority 0-9, higher=more urgent (for create)'}, 'tags': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Tags (for create)'}, 'status': {'type': 'string', 'description': 'Filter by status: pending|in_progress|completed|cancelled (for list)'}},
        handler_attr='_todo_handler',
        security_scope='read',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='request_user_input',
        description='Request user input during agent loop (single/multiple/text modes)',
        parameters={'header': {'type': 'string', 'description': 'Optional header label'}, 'question': {'type': 'string', 'description': 'The question to ask the user'}, 'mode': {'type': 'string', 'description': 'Input mode: single (one answer) | multiple (comma-separated) | text (free-form)'}, 'options': {'type': 'array', 'items': {'type': 'object', 'properties': {'label': {'type': 'string'}, 'description': {'type': 'string'}}, 'required': ['label']}, 'description': 'Choice options for single/multiple mode'}},
        handler_attr='_request_user_input_handler',
        security_scope='read',
        concurrency_safe=False,
    ),
    ToolSpec(
        name='lsp',
        description='LSP code navigation: goto_def / find_refs / hover / goto_impl',
        parameters={'command': {'type': 'string', 'description': 'lsp command: goto_def|find_refs|hover|goto_impl'}, 'file_path': {'type': 'string', 'description': 'Absolute file path'}, 'line': {'type': 'integer', 'description': '0-based line number'}, 'character': {'type': 'integer', 'description': '0-based character offset'}},
        handler_attr='_lsp_handler',
        security_scope='read',
        concurrency_safe=False,
    ),
)



def register_all_tools(registry: Any, runtime: Any) -> None:
    """统一注册入口：specs 表 × runtime handlers → ToolRegistry。

    T3：全部走 handler_name 插片 —— ToolDefinition 不直持 Callable，
    定义（schema）与实现（handler）解耦。绑定失败立即 raise（不静默）。
    """
    for spec in SPECS:
        handler = getattr(runtime, spec.handler_attr, None)
        if handler is None or not callable(handler):
            raise AttributeError(
                f"tool '{spec.name}': runtime 缺 handler {spec.handler_attr}"
            )
        registry.register_handler(spec.name, handler)
        registry.register(
            ToolDefinition(
                name=spec.name,
                description=spec.description,
                parameters=spec.parameters,
                security_scope=spec.security_scope,
                is_concurrency_safe=spec.concurrency_safe,
                handler_name=spec.name,
            )
        )
