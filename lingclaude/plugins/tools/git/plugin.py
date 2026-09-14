"""git 工具组插件（P1: plugins/tools 载体第三批插片）。

灵元纪律：
- 变化（工具实现）= 插片，不焊进主干 —— 本文件自包含 manifest + 实现。
- 不复制主干逻辑：execute 委托 engine/git 模块函数（与 tool_handlers/git_tools.py
  共用同一实现，只做「按名分派」，不重复 Git 参数化/白名单/防注入逻辑）。
- 插件提供 git_status/git_diff/git_log/git_blame/git_push/git_push_preflight
  六种能力，按 name 分派到 git.py 对应函数。
"""

from __future__ import annotations

from typing import Any

from lingclaude.engine.git import (
    git_blame,
    git_diff,
    git_log,
    git_push,
    git_push_preflight,
    git_status,
)


class GitPlugin:
    """ToolPlugin 协议实现：name + execute（SeamType.TOOL 槽位）。"""

    name = "git_plugin"

    def execute(self, name: str = "git_status", **kwargs: Any) -> Any:
        """按工具名分派到 engine.git 对应函数。

        - git_status → git_status(path)
        - git_diff → git_diff(path, target, staged, stat)
        - git_log → git_log(path, count, follow)
        - git_blame → git_blame(file_path, cwd, start_line, end_line)
        - git_push → git_push(path, remote, branch, force, timeout)
        - git_push_preflight → git_push_preflight(path)
        """
        if name == "git_diff":
            result = git_diff(
                kwargs.pop("path", "."),
                target=kwargs.pop("target", ""),
                staged=kwargs.pop("staged", False),
                stat=kwargs.pop("stat", False),
            )
        elif name == "git_log":
            result = git_log(
                kwargs.pop("path", "."),
                count=kwargs.pop("count", 10),
                follow=kwargs.pop("follow", None),
            )
        elif name == "git_blame":
            result = git_blame(
                kwargs.pop("file_path", ""),
                cwd=kwargs.pop("cwd", "."),
                start_line=kwargs.pop("start_line", None),
                end_line=kwargs.pop("end_line", None),
            )
        elif name == "git_push":
            result = git_push(
                path=kwargs.pop("path", "."),
                remote=kwargs.pop("remote", "origin"),
                branch=kwargs.pop("branch", "master"),
                force=kwargs.pop("force", False),
                timeout=kwargs.pop("timeout", 120),
            )
        elif name == "git_push_preflight":
            result = git_push_preflight(path=kwargs.pop("path", "."))
        else:  # git_status
            result = git_status(kwargs.pop("path", "."))

        if getattr(result, "is_error", False):
            return {"error": getattr(result, "error", "unknown git error")}
        return result.data
