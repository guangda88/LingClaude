"""bash 示范工具插件（P0: plugins/tools 载体首批插片）。

灵元纪律：
- 变化（工具实现）= 插片，不焊进主干 —— 本文件自包含 manifest + 实现。
- 注册进 SeamRegistry.TOOL 后，ToolRegistry.execute 优先走本插件（热拔插通道）。
- 不复制主干逻辑：execute 委托 engine/bash.BashExecutor（与主干共用同一执行器）。
"""

from __future__ import annotations

from typing import Any


class BashPlugin:
    """ToolPlugin 协议实现：name + execute。

    满足 lingclaude.core.seam.ToolPlugin 协议（SeamType.TOOL 槽位要求）。
    execute 委托 BashExecutor 执行实际命令 —— 插片只做「注册 + 委托」，
    不重复实现沙箱/超时/脱敏（那是 BashExecutor 的不变项）。
    """

    name = "bash_plugin"

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        from lingclaude.engine.bash import BashExecutor

        command = kwargs.pop("command", kwargs.pop("cmd", ""))
        timeout = kwargs.pop("timeout", 30)
        executor = BashExecutor()
        return executor.run(command=command, timeout=timeout)
