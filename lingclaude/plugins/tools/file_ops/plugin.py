"""file_ops 工具插件组（P1: plugins/tools 载体第二批插片）。

灵元纪律：
- 变化（工具实现）= 插片，不焊进主干 —— 本文件自包含 manifest + 实现。
- 不复制主干逻辑：execute 委托 engine/file_edit.FileEditTool（与主干共用实现）。
- 插件提供 write/edit/create/insert/delete_lines/undo 六种能力，按 op 分派。
"""

from __future__ import annotations

from typing import Any

_OPS = {
    "write": "replace",
    "edit": "replace",
    "file_create": "create",
    "file_insert": "insert",
    "file_delete_lines": "delete_lines",
    "file_undo": "undo",
}


class FileOpsPlugin:
    """ToolPlugin 协议实现：name + execute（SeamType.TOOL 槽位）。"""

    name = "file_ops_plugin"

    def execute(self, name: str = "edit", **kwargs: Any) -> Any:
        """按工具名分派到 FileEditTool 对应方法。

        - write/edit → replace（path + content）
        - file_create → create（path + content）
        - file_insert → insert（path + line + text）
        - file_delete_lines → delete_lines（path + start_line + end_line）
        - file_undo → undo（path）
        """
        from lingclaude.engine.file_edit import FileEditTool

        op = _OPS.get(name, "replace")
        tool = FileEditTool()
        return getattr(tool, op)(**kwargs)
