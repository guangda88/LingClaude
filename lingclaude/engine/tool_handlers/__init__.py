"""工具 handler 插片目录 — coding.py 厚主干拆分（灵元尺子：变化变成插片）。

每个能力域一个 Mixin，CodingRuntime 继承后获得对应工具 handler。
handler 通过 self 访问 CodingRuntime 初始化的 executor（bash/file_ops/grep 等）。

注意：目录名不用 tools/（与 engine/tools.py 的 ToolDefinition/ToolRegistry 冲突）。
"""
from lingclaude.engine.tool_handlers.bash_tools import BashToolsMixin
from lingclaude.engine.tool_handlers.file_tools import FileToolsMixin
from lingclaude.engine.tool_handlers.search_tools import SearchToolsMixin
from lingclaude.engine.tool_handlers.git_tools import GitToolsMixin
from lingclaude.engine.tool_handlers.subagent_tools import SubagentToolsMixin
from lingclaude.engine.tool_handlers.background_tools import BackgroundToolsMixin
from lingclaude.engine.tool_handlers.web_tools import WebToolsMixin
from lingclaude.engine.tool_handlers.plan_tools import PlanToolsMixin
from lingclaude.engine.tool_handlers.todo_tools import TodoToolsMixin
from lingclaude.engine.tool_handlers.lsp_tools import LspToolsMixin

__all__ = [
    "BashToolsMixin",
    "FileToolsMixin",
    "SearchToolsMixin",
    "GitToolsMixin",
    "SubagentToolsMixin",
    "BackgroundToolsMixin",
    "WebToolsMixin",
    "PlanToolsMixin",
    "TodoToolsMixin",
    "LspToolsMixin",
]
