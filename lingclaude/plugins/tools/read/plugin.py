"""read 示范工具插件（P0: plugins/tools 载体首批插片）。

灵元纪律：
- 变化（工具实现）= 插片，不焊进主干。
- 注册进 SeamRegistry.TOOL 后，ToolRegistry.execute 优先走本插件。
- 不复制主干逻辑：execute 委托 engine/file_read.FileReadTool（复用实现）。
"""

from __future__ import annotations

from typing import Any


class ReadPlugin:
    """ToolPlugin 协议实现：name + execute（SeamType.TOOL 槽位）。"""

    name = "read_plugin"

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        from lingclaude.engine.file_read import FileReadTool

        return FileReadTool().read(**kwargs)
