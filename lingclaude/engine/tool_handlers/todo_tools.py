"""Todo 工具 handler 插片 — 从 coding.py 拆分（灵元：工具是插片）。

TodoToolsMixin: todo（依赖 self._todo_handlers，__init__ 里由 TodoStore 构建）。
2026-09-17 第3级a: todo_write（模型可调用，多步任务自动拆解 → 全量覆写
session 级 TodoStore，走状态纪律①：唯一 in_progress=active_id，其余退回 pending）。
"""

from __future__ import annotations

import uuid

from typing import Any

from lingclaude.core.types import ToolResult
from lingclaude.engine.todo import TodoItem, TodoStatus, make_handlers


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
    ) -> ToolResult[dict[str, Any]]:
        """P0-1: Todo list tool dispatcher."""
        h = self._todo_handlers
        cmd = command.lower()
        if cmd == "create":
            return ToolResult.ok(h["create"](content=content, priority=priority, tags=tags))
        if cmd == "list":
            return ToolResult.ok(h["list"](status=status, tags=tags))
        if cmd == "complete":
            return ToolResult.ok(h["complete"](id=id))
        if cmd == "cancel":
            return ToolResult.ok(h["cancel"](id=id))
        if cmd == "start":
            return ToolResult.ok(h["start"](id=id))
        if cmd == "get":
            return ToolResult.ok(h["get"](id=id))
        if cmd == "delete":
            return ToolResult.ok(h["delete"](id=id))
        return ToolResult.err(
            f"unknown command: {command}",
            tool_name="todo",
        )

    # ------------------------------------------------------------------
    # 第3级a（2026-09-17）：todo_write — 模型自动拆解多步任务。
    # 对标 AtomCode todowrite：全量替换当前任务清单，恰好一个 in_progress
    # （= active_id 指定的项，其余退回 pending）；completed 项保留历史。
    # ------------------------------------------------------------------
    def _todo_write_handler(
        self,
        todos: list[dict[str, Any]] | None = None,
        active_id: str | None = None,
        **_: Any,
    ) -> ToolResult[dict[str, Any]]:
        """全量覆写 session 任务清单（多步任务拆解入口）。

        参数：
          todos:    [{content, status}]，status ∈ pending|in_progress|completed；
                    全量替换（未列出的旧项移除，已完成项保留则更新）。
          active_id: 唯一 in_progress 项的 content（"none" 或空 = 无进行中）。
        """
        import time

        todos = todos or []
        if not todos:
            return ToolResult.err(
                "todos 为空 — 至少给一项 {content, status}", tool_name="todo_write"
            )

        store = getattr(self, "_todo_store", None)
        if store is None:
            return ToolResult.err(
                "当前模式无 TodoStore（单轮/降级模式不可用）", tool_name="todo_write"
            )

        # 1) 校验 active_id 与 todos 的 in_progress 一致性（纪律①恰好一个）
        # 2026-09-17 修复 (双匹配): active_id 归一只用于"是否存在"判断，
        # 改写按精确原文匹配 —— 原两处 .lower() 归一使仅大小写不同的
        # 重复 content 全部命中 → 多个 in_progress，违反纪律①。
        active_given = bool((active_id or "").strip()) and (active_id or "").strip().lower() != "none"
        in_prog = [t for t in todos if t.get("status") == "in_progress"]
        if active_given:
            active_exact = (active_id or "").strip()
            matched = [t for t in todos if t.get("content", "").strip() == active_exact]
            if matched:
                for t in in_prog:
                    if t not in matched:
                        t["status"] = "pending"
                for t in matched:
                    t["status"] = "in_progress"
            else:
                # active_id 指向不存在项 → 视同未指定，全部退回 pending
                for t in in_prog:
                    t["status"] = "pending"
        else:
            # 无 active_id → 全部退回 pending
            for t in in_prog:
                t["status"] = "pending"

        # 2) 全量覆写：移除旧项，插入新清单（id 全部重新生成）
        # 2026-09-17 更正注释: 原注释声称"保留已完成项的 id 作历史"，
        # 实现是全删后 uuid4 重造 —— id 引用（跨轮 active_id）会断，如实标注。
        now = time.time()
        for old in store.list():
            store.delete(old.id)
        inserted = 0
        new_ids: dict[str, str] = {}
        for t in todos:
            content = t.get("content", "").strip()
            if not content:
                continue  # 空 content 跳过且不计入统计（下方计数基于 inserted）
            st = t.get("status", "pending")
            st = st if st in ("pending", "in_progress", "completed") else "pending"
            item = TodoItem(
                id=str(uuid.uuid4())[:8],
                content=content,
                status=TodoStatus(st),
                created_at=now,
                updated_at=now,
            )
            store.add(item)
            new_ids[content.lower()] = item.id
            inserted += 1

        # 2026-09-17 修复 (统计口径): 原按入参 todos 计数，空 content 被跳过
        # 却照常计入 → 返回统计与实际入库不一致。改为只统计实际入库项。
        active_count = sum(
            1 for t in todos
            if t.get("content", "").strip() and t.get("status") == "in_progress"
        )
        pending_count = sum(
            1 for t in todos
            if t.get("content", "").strip() and t.get("status") == "pending"
        )
        completed_count = sum(
            1 for t in todos
            if t.get("content", "").strip() and t.get("status") == "completed"
        )
        return ToolResult.ok({
            "ok": True,
            "count": len(new_ids),
            "in_progress": active_count,
            "pending": pending_count,
            "completed": completed_count,
            "active_id": (active_id or "").strip() if active_given else None,
            "message": (
                f"任务清单已更新：{len(new_ids)} 项"
                f"（{active_count} 进行中 / {pending_count} 待办 / {completed_count} 完成）。"
                "用户可经 /tasks 查看面板；完成一项后再次调用本工具推进。"
            ),
        })
