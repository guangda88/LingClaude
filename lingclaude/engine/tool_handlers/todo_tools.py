"""Todo 工具 handler 插片 — 从 coding.py 拆分（灵元：工具是插片）。

TodoToolsMixin: todo（依赖 self._todo_handlers，__init__ 里由 TodoStore 构建）。
"""

from __future__ import annotations

from typing import Any


class TodoToolsMixin:
    """todo 工具 handler（P0-1 Todo list tool dispatcher）。"""

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
