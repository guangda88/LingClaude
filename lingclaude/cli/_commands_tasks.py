"""斜杠命令处理器（P4.1 从 cli/app.py 拆出）— _interactive_loop 嵌套闭包的外提。

原实现是 _interactive_loop 内约 280 行闭包（radon 将嵌套函数复杂度聚合进宿主，
是 F(69) 的主因）；现外提为 SlashCommandProcessor，engine/status 构造注入，
quit_requested 由 nonlocal 改为实例属性（语义不变）。
命令体自 app.py 原样迁移，仅去掉一层闭包缩进。
"""

from typing import Any

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from lingclaude.cli.repl_turn import _record_long_task_metrics
import json as _json
import shlex


def _next_fork_tag() -> str:
    """缺省 fork tag：fork-<HHMMSS>（冲突加 pid 后缀）。"""
    tag = f"fork-{time.strftime('%H%M%S')}"
    rodir = Path(".lingclaude/rollouts")
    if rodir.exists() and any(rodir.glob(f"{tag}*.jsonl")):
        tag += f"-{os.getpid() % 10000}"
    return tag

# Step 3: Tab 补全清单（F2 修复:删 /undo — handler 缺失不得留在补全里误导用户）
SLASH_COMPLETER_WORDS = [
    "/help", "/clear", "/compact", "/model", "/schedule", "/lsp",
    "/resume", "/continue", "/session", "/checkpoint", "/recover", "/rewind", "/quit",
    # 2026-09-23: C 路线统一——/fork（rollout 不可变分叉）/ /share（自包含导出）
    "/fork", "/share",
    # 2026-09-17: 任务面板（对标 AtomCode todowrite）—— /tasks /todo /plan 同义
    "/tasks",
    # 2026-09-20: 会话历史查看（TUI 优化方案 P2-1，cc 建议）—— 退出后回看入口
    "/history",
    # 2026-09-21: OpenRouter 一键接入（OAuth PKCE，学 atomcode）——
    # CodingPlan 配额耗尽时的免费池逃生门
    "/openrouter",
    # 2026-09-20: P3 全量重绘输出窗（atomcode invalidate 借鉴）
    "/resync",
]



class SlashCommandTasksMixin:
    """任务面板命令域（/tasks /todo /plan）— P5 拆分自 commands.py。"""


    def _cmd_tasks(self, arg: str) -> None:
        """2026-09-17 任务面板（第1级渲染 + 第2级纪律）。

        用法：
          /tasks              活跃面板（in_progress 高亮 + pending，按优先级）
          /tasks all         全量（含 completed/cancelled）
          /tasks add <文本>  新增 pending 项
          /tasks start <id>  置 in_progress（其他 in_progress 自动退回 pending）
          /tasks done <id>   完成一项（禁批量——逐项核销，面板永远真实）
        数据源：engine._runtime._todo_store（TodoStore，session 级 SQLite 持久化，
        跨 /continue 恢复仍在）。无 runtime（单轮/降级模式）时明确提示不静默。
        """
        from lingclaude.engine.todo import TodoStatus

        runtime = getattr(self.engine, "_runtime", None)
        store = getattr(runtime, "_todo_store", None) if runtime else None
        if store is None:
            print("[任务] 当前模式无任务存储（TodoStore 需 CodingRuntime；单轮/降级模式不可用）")
            return

        handlers = getattr(runtime, "_todo_handlers", None) or {}
        arg = arg.strip()

        if not arg:
            items = store.active_items()
            self._print_task_panel(items)
            return
        if arg == "all":
            items = store.list()
            self._print_task_panel(items)
            return
        parts = arg.split(maxsplit=1)
        verb, val = parts[0], (parts[1] if len(parts) > 1 else "").strip()
        if verb == "add":
            if not val:
                print("[任务] 用法: /tasks add <文本>")
                return
            res = handlers.get("create", lambda *a, **k: None)(val)
            tid = res.get("todo", {}).get("id", "?") if isinstance(res, dict) else "?"
            print(f"[任务] 已新增 #{tid[:8]}: {val}（pending）")
            self._print_task_panel(store.active_items())
            return
        if verb in ("start", "done"):
            if not val:
                print(f"[任务] 用法: /tasks {verb} <id>")
                return
            # 短前缀匹配（id 8 位，用户可输前缀）
            matches = [i for i in store.list() if str(i.id).startswith(val)]
            if len(matches) != 1:
                print(f"[任务] 无法定位 '{val}'（{len(matches)} 个匹配）")
                return
            tid = matches[0].id
            if verb == "start":
                res = handlers.get("start")(tid)
                if res.get("ok"):
                    print(f"[任务] #{tid[:8]} 置 in_progress"
                          + (f"（中断退回: {', '.join(r[:8] for r in res['released'])}）" if res.get("released") else ""))
                else:
                    print(f"[任务] 启动失败: {res.get('error')}")
            else:
                res = handlers.get("complete")(tid)
                if res.get("ok"):
                    print(f"[任务] #{tid[:8]} 已完成")
                else:
                    print(f"[任务] 完成失败: {res.get('error')}")
            self._print_task_panel(store.active_items())
            return
        # 未知动词 —— 当作 id 前缀尝试 start
        if handlers.get("start"):
            res = handlers["start"](arg)
            if res.get("ok"):
                print(f"[任务] #{arg[:8]} 置 in_progress")
                self._print_task_panel(store.active_items())
            else:
                print(f"[任务] 未知操作 '{arg}'；用法: /tasks [add|start|done|all] [参数]")

    @staticmethod
    def _print_task_panel(items) -> None:
        """渲染任务面板：in_progress 🔄 高亮置顶、pending ⬜、已完成 ✅、取消 ✗。"""
        from lingclaude.engine.todo import TodoStatus

        if not items:
            print("[任务] 无待办")
            return
        icon = {
            TodoStatus.IN_PROGRESS: "🔄",
            TodoStatus.PENDING: "⬜",
            TodoStatus.COMPLETED: "✅",
            TodoStatus.CANCELLED: "✗",
        }
        # in_progress 最上、pending 次之（均按 priority 降序）、完成/取消沉底
        order = {TodoStatus.IN_PROGRESS: 0, TodoStatus.PENDING: 1,
                 TodoStatus.COMPLETED: 2, TodoStatus.CANCELLED: 3}
        items = sorted(items, key=lambda i: (order.get(i.status, 9), -i.priority, i.created_at))
        print(f"[任务] {len(items)} 项（"
              f"{sum(1 for i in items if i.status == TodoStatus.IN_PROGRESS)} 进行中 / "
              f"{sum(1 for i in items if i.status == TodoStatus.PENDING)} 待办 / "
              f"{sum(1 for i in items if i.status == TodoStatus.COMPLETED)} 完成）")
        for i in items:
            mark = icon.get(i.status, "·")
            line = f"  {mark} #{i.id[:8]}  {i.content}"
            if i.status == TodoStatus.IN_PROGRESS:
                line += "  ← 当前执行"
            print(line)
