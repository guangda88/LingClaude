"""Todo 工具 handler 插片 — 从 coding.py 拆分（灵元：工具是插片）。

TodoToolsMixin: todo（依赖 self._todo_handlers，__init__ 里由 TodoStore 构建）。
2026-09-17 第3级a: todo_write（模型可调用，多步任务自动拆解 → 全量覆写
session 级 TodoStore，走状态纪律①：唯一 in_progress=active_id，其余退回 pending）。
"""

from __future__ import annotations

from typing import Any

from lingclaude.core.types import ToolResult
from lingclaude.engine.todo import (
    TODO_WRITE_STATUSES,
    TodoStatus,
    build_plan_items,
    make_handlers,
    plan_echo,
    validate_plan,
)


def _norm(c: Any) -> str:
    """归一工具入参文本（strip / None→空串）。"""
    return str(c or "").strip()


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
    # 2026-10-08 R4 重写（OC/AC/CC 三方审计定案，AC 借鉴1/2/4 + OC 借鉴1/3/4）：
    #   · 单工具双形态（AC 式扁平 union，弱模型友好）：
    #       PLAN/RE-PLAN  {"todos":[...], "active_id":...}  全量基线（原子 merge_replace）
    #       增量          {"action":"add","content":...}    O(1) 追加（插新任务正解）
    #       增量          {"action":"update","id":...}      单项状态推进
    #   · 校验先行：非法清单整条拒绝，绝不清掉旧基线（AC parse_todos 语义）
    #   · 恰好一个 in_progress 在写入路径无条件强制（AC reducer 语义）
    #   · 稳定序号 id：按 content 归一匹配复用旧 id，只给新增项发新号
    #   · source 保护：user 来源项全量替换时保留（merge 语义）
    #   · 权威回灌：结果返回完整 {id,content,status} 清单（OC toModelOutput 语义）
    # ------------------------------------------------------------------
    def _todo_write_handler(
        self,
        todos: list[dict[str, Any]] | None = None,
        active_id: str | None = None,
        action: str | None = None,
        id: str | None = None,
        content: str | None = None,
        status: str | None = None,
        **_: Any,
    ) -> ToolResult[dict[str, Any]]:
        """todo_write：单工具双形态（PLAN 全量基线 / action 增量补丁）。"""
        import time

        store = getattr(self, "_todo_store", None)
        if store is None:
            return ToolResult.err(
                "当前模式无 TodoStore（单轮/降级模式不可用）", tool_name="todo_write"
            )
        handlers = getattr(self, "_todo_handlers", None) or {}

        # ---- 增量形态分流（按参数形状，不看工具名；AC §1.1）------------
        act = (action or "").strip().lower()
        if act == "add":
            text = _norm(content)
            if not text:
                return ToolResult.err(
                    '增量追加用法: {"action":"add","content":"…"}',
                    tool_name="todo_write",
                )
            if not handlers.get("create"):
                return ToolResult.err("todo handlers 不可用", tool_name="todo_write")
            res = handlers["create"](content=text)
            return ToolResult.ok(
                plan_echo(
                    store, f"已插入 1 项 #{res['todo']['id']}: {text}（pending）"
                )
            )
        if act == "update":
            tid = _norm(id)
            new_status = _norm(status).lower()
            if not tid or new_status not in TODO_WRITE_STATUSES:
                return ToolResult.err(
                    '增量更新用法: {"action":"update","id":"t3","status":"completed"}'
                    f"（status ∈ {', '.join(TODO_WRITE_STATUSES)}）",
                    tool_name="todo_write",
                )
            if not handlers.get("start") or not handlers.get("complete"):
                return ToolResult.err("todo handlers 不可用", tool_name="todo_write")
            if new_status == "in_progress":
                res = handlers["start"](tid)  # store.start_item 自带纪律①释放旧项
            elif new_status == "completed":
                res = handlers["complete"](tid)
            else:  # pending: 直接退回
                ok = store.update_status(tid, TodoStatus.PENDING)
                res = {"ok": ok, "id": tid}
            if not res.get("ok"):
                return ToolResult.err(
                    f"id={tid!r} 不存在（权威清单见返回 todos 字段）",
                    tool_name="todo_write",
                )
            return ToolResult.ok(
                plan_echo(store, f"#{tid} → {new_status}")
            )
        if act:
            return ToolResult.err(
                f"未知 action={act!r}（允许 add|update；全量替换请传 todos）",
                tool_name="todo_write",
            )

        # ---- PLAN / RE-PLAN 全量形态 ------------------------------------
        todos = todos or []
        if not todos:
            return ToolResult.err(
                "todos 为空 — 至少给一项 {content, status}", tool_name="todo_write"
            )
        norm, _active_exact, err = validate_plan(todos, active_id)
        if err is not None:
            # 基线保护：非法清单整条拒绝，旧清单原样保留（AC parse_todos 语义）
            return ToolResult.err(
                f"{err}。旧任务清单未受影响，修正后重试。",
                tool_name="todo_write",
            )

        now = time.time()
        old_items = store.list()
        # 稳定 id：归一 content 匹配复用旧 id，新增项发新序号（OC 借鉴3）
        n_fresh = sum(
            1 for t in norm if t["content"].lower() not in {i.content.lower() for i in old_items}
        )
        new_items = build_plan_items(norm, old_items, store.alloc_seq_id(n_fresh), now)
        # user 来源未被模型清单继承的项原样保留（生成期间插入不被静默清掉；
        # OC 借鉴2b merge 语义）。merge_replace 单锁原子落盘（OC 借鉴1）。
        merged = store.merge_replace(new_items, protect_sources=("user",))

        n_ip = sum(1 for i in merged if i.status == TodoStatus.IN_PROGRESS)
        n_pd = sum(1 for i in merged if i.status == TodoStatus.PENDING)
        n_cp = sum(1 for i in merged if i.status == TodoStatus.COMPLETED)
        msg = (
            f"任务清单已更新：{len(merged)} 项"
            f"（{n_ip} 进行中 / {n_pd} 待办 / {n_cp} 完成）。"
            '插入新任务用 {"action":"add"}；推进用 {"action":"update"}；'
            "完成一项必须先验证再打 completed。"
        )
        return ToolResult.ok(plan_echo(store, msg))
