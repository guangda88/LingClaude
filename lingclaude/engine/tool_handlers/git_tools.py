"""Git 工具 handler 插片 — 从 coding.py 拆分（灵元：工具是插片）。"""

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


class GitToolsMixin:
    """git_status / git_diff / git_log / git_blame / git_push / git_push_preflight（依赖 engine.git 模块函数）。"""

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

    def _git_push_handler(
        self,
        path: str = ".",
        remote: str = "origin",
        branch: str = "master",
        force: bool = False,
        timeout: int = 120,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        """专用 git push：参数化构造（防注入）+ remote/branch 白名单。"""
        result = git_push(path=path, remote=remote, branch=branch, force=force, timeout=timeout)
        if result.is_error:
            return {"error": result.error}
        return result.data

    def _git_push_preflight_handler(self, path: str = ".", **_kwargs: Any) -> dict[str, Any]:
        """push 前置预检：remote / 未推送提交 / 门禁状态 / 工作区。"""
        result = git_push_preflight(path=path)
        if result.is_error:
            return {"error": result.error}
        return result.data
