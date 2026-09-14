from __future__ import annotations

"""灵克 LingBus 任务响应器 — 通过灵信接收灵通+派发的任务并执行。

灵通+的 TaskDispatcher 通过 LingBus 向灵克派发任务，
本模块轮询 LingBus 中的 lingclaude 消息，解析任务指令，
调用灵克 MCP 工具执行，并通过 LingBus 回复结果。

状态流转（与灵通+ TaskDispatcher 对齐）:
    SENT → ACKED → IN_PROGRESS → DONE / FAILED

使用方式:
    from lingclaude.coordination.bus_responder import BusResponder

    responder = BusResponder()
    responder.poll_and_respond()   # 单次轮询
    responder.run_loop(interval=30)  # 持续轮询
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from lingclaude.core.types import Result

logger = logging.getLogger(__name__)

_MEMBER_ID = "lingclaude"
_MEMBER_NAME = "灵克"


class TaskParseResult(str, Enum):
    TASK_FOUND = "task_found"
    NO_TASK = "no_task"
    ALREADY_HANDLED = "already_handled"


@dataclass
class ParsedTask:
    task_id: str
    thread_id: str
    description: str
    priority: int = 5
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResponseStats:
    polled: int = 0
    tasks_received: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    replies_sent: int = 0
    last_poll_at: float = 0.0


class BusResponder:
    """LingBus 任务响应器。

    轮询灵信中发给灵克的消息，识别灵通+派发的任务，
    执行后回复结果。
    """

    def __init__(self, bus: Any | None = None) -> None:
        self._bus = bus
        self._last_rowid = 0
        self._handled_tasks: set[str] = set()
        self._state_file = Path.home() / ".lingclaude" / "bus_responder_state.json"
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._stats = ResponseStats()
        self._engine: Any | None = None
        self._load_state()

    def _get_bus(self) -> Any:
        if self._bus is None:
            from lingclaude.lacp.cross_repo_seam import ensure_import_path

            ensure_import_path("lingmessage")
            from lingmessage.lingbus import LingBus
            self._bus = LingBus()
        return self._bus

    def _load_state(self) -> None:
        if self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text(encoding="utf-8"))
                self._last_rowid = data.get("last_rowid", 0)
                self._handled_tasks = set(data.get("handled_tasks", []))
            except Exception as e:
                logger.debug("Failed to load responder state: %s", e)

    def _save_state(self) -> None:
        data = {
            "last_rowid": self._last_rowid,
            "handled_tasks": list(self._handled_tasks)[-200:],
            "stats": {
                "polled": self._stats.polled,
                "tasks_received": self._stats.tasks_received,
                "tasks_completed": self._stats.tasks_completed,
                "tasks_failed": self._stats.tasks_failed,
            },
        }
        tmp = self._state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._state_file)

    def _parse_task(self, message: Any) -> tuple[TaskParseResult, ParsedTask | None]:
        body = message.body or ""
        sender = message.sender or ""

        if sender in ("lingflow_plus", "灵通+", "lingtong"):
            pass
        else:
            return TaskParseResult.NO_TASK, None

        task_id_match = re.search(r"任务ID:\s*(task_\w+)", body)
        priority_match = re.search(r"优先级:\s*(\d+)", body)
        desc_match = re.search(r"描述:\s*(.+?)(?:\n|$)", body)

        if not task_id_match:
            return TaskParseResult.NO_TASK, None

        task_id = task_id_match.group(1)
        if task_id in self._handled_tasks:
            return TaskParseResult.ALREADY_HANDLED, None

        task = ParsedTask(
            task_id=task_id,
            thread_id=message.thread_id,
            description=desc_match.group(1).strip() if desc_match else body[:200],
            priority=int(priority_match.group(1)) if priority_match else 5,
            metadata={
                "sender": sender,
                "message_id": message.message_id,
            },
        )
        return TaskParseResult.TASK_FOUND, task

    def _get_engine(self) -> Any | None:
        """惰性引导 engine+runtime（与 CLI `_cmd_run` 同路径），供 native 工具管线执行。"""
        if self._engine is None:
            from lingclaude.core.config import load_config
            from lingclaude.core.query_engine import QueryEngine
            from lingclaude.engine.coding import CodingRuntime

            engine_result = QueryEngine.from_config_file(None)
            if engine_result.is_error:
                logger.error("engine bootstrap failed: %s", engine_result.error)
                return None
            engine = engine_result.data
            engine.set_runtime(CodingRuntime(load_config(None)))
            self._engine = engine
        return self._engine

    def _run_tool(self, name: str, kwargs: dict[str, Any]) -> Result[str]:
        """经 engine.execute_tool 执行 — 主循环同款路径：native 5 段管线
        （pre/guards/execute/post/finalize）优先，未命中再落 MCP fallback。
        （审计#2 根因修复：旧实现直接调 mcp_proxy，注册表恒空必 "No server found"。）"""
        engine = self._get_engine()
        if engine is None:
            return Result.fail("引擎引导失败，native 工具管线不可用", code="ENGINE_UNAVAILABLE")
        try:
            import json as _json

            # engine._execute_tool = ToolExecutor 主路径：native runtime 管线优先，
            # 未命中自动落 MCP fallback（含 _ensure_mcp 注册表填充）
            output = engine._execute_tool(name, _json.dumps(kwargs, ensure_ascii=False))
        except Exception as e:
            return Result.fail(f"工具 {name} 执行异常: {e}", code="TOOL_ERROR")
        try:
            import json as _json

            parsed = _json.loads(output) if isinstance(output, str) else output
        except (ValueError, TypeError):
            parsed = {"result": str(output)[:500]}
        if isinstance(parsed, dict) and parsed.get("error"):
            return Result.fail(str(parsed["error"]), code="TOOL_ERROR")
        text = output if isinstance(output, str) else json.dumps(parsed, ensure_ascii=False, default=str)
        return Result.ok(text[:2000])

    def _execute_task(self, task: ParsedTask) -> Result[str]:
        """执行总线任务 — R4 重构（真执行链 + 安全门）。

        路由：
        - analyze/review → analyze_full（native registry → MCP fallback；MCP 不可达时
          报告真实失败，不再假装成功）
        - search/find → search_code（同上）
        - **其余一律默认拒绝**：旧实现把任意任务文本直接当 shell 命令跑
          （`run_bash(command=task.description)`）——总线消息即命令注入面。
          需 LINGCLAUDE_BUS_ALLOW_BASH=1 显式授权才放行（走 native `bash` 工具，
          含 5 段管线守卫；H8/钟表域：危险动作走确定性开关，不靠调度方自律）。
        """
        import os

        description_lower = task.description.lower()

        if any(kw in description_lower for kw in ("审查", "review", "分析", "analyze")):
            return self._run_tool("analyze_full", {"target": task.description})
        if any(kw in description_lower for kw in ("搜索", "search", "查找", "find")):
            return self._run_tool("search_code", {"pattern": task.description})
        if os.environ.get("LINGCLAUDE_BUS_ALLOW_BASH") != "1":
            return Result.fail(
                "任务需要执行 shell 命令，已被安全门拦截（默认拒绝）。"
                "放行需同时满足：LINGCLAUDE_BUS_ALLOW_BASH=1 + 治理审批记录。",
                code="BASH_NOT_AUTHORIZED",
            )
        # native 工具名是 bash（run_bash 是 lingflow MCP 侧的旧名，native 注册表没有）
        return self._run_tool("bash", {"command": task.description})

    def _send_reply(self, thread_id: str, body: str) -> str | None:
        from lingclaude.core.governance_integration import pre_submit_governance

        gov_result = pre_submit_governance(
            action="post_reply",
            content=body,
            agent_id=_MEMBER_ID,
        )
        if not gov_result.get("approved"):
            logger.warning("GovernanceGate blocked reply: %s", gov_result.get("reason"))
            return None

        try:
            bus = self._get_bus()
            msg_id = bus.post_reply(
                thread_id=thread_id,
                sender=_MEMBER_ID,
                recipient="lingflow_plus",
                body=body,
                message_type="reply",
            )
            self._stats.replies_sent += 1
            return msg_id
        except Exception as e:
            logger.error("Failed to send reply: %s", e)
            return None

    def poll_and_respond(self) -> list[dict[str, Any]]:
        bus = self._get_bus()
        messages = bus.poll(
            recipient=_MEMBER_ID,
            since_rowid=self._last_rowid,
            limit=20,
        )
        self._stats.polled += len(messages)
        self._stats.last_poll_at = time.time()

        results = []
        for msg in messages:
            # 审计#13:rowid 逐条推进（旧实现提前跳到批尾——批内后续任务在
            # 进程异常时被静默跳过且本进程内不会重试）
            self._last_rowid = msg.rowid
            parse_result, task = self._parse_task(msg)
            if parse_result == TaskParseResult.TASK_FOUND and task:
                self._stats.tasks_received += 1
                self._send_reply(
                    task.thread_id,
                    f"✅ {_MEMBER_NAME}已收到任务 {task.task_id}，开始执行。",
                )

                exec_result = self._execute_task(task)
                self._handled_tasks.add(task.task_id)

                if exec_result.is_ok:
                    self._stats.tasks_completed += 1
                    output_str = str(exec_result.data)[:500] if exec_result.data else "执行完成"
                    self._send_reply(
                        task.thread_id,
                        f"✅ 完成\n任务ID: {task.task_id}\n结果: {output_str}",
                    )
                else:
                    self._stats.tasks_failed += 1
                    self._send_reply(
                        task.thread_id,
                        f"❌ 失败\n任务ID: {task.task_id}\n错误: {exec_result.error}",
                    )

                results.append({
                    "task_id": task.task_id,
                    "success": exec_result.is_ok,
                    "thread_id": task.thread_id,
                })

        self._save_state()
        return results

    def run_loop(self, interval: float = 30.0) -> None:
        import signal

        running = True

        def _stop(signum: int, frame: Any) -> None:
            nonlocal running
            running = False
            logger.info("BusResponder received stop signal")

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

        logger.info("BusResponder started (interval=%.0fs)", interval)
        while running:
            try:
                self.poll_and_respond()
            except Exception as e:
                logger.error("BusResponder poll error: %s", e)
            time.sleep(interval)

        self._save_state()
        logger.info("BusResponder stopped")

    def get_stats(self) -> dict[str, Any]:
        return {
            "member_id": _MEMBER_ID,
            "member_name": _MEMBER_NAME,
            "last_rowid": self._last_rowid,
            "handled_tasks": len(self._handled_tasks),
            "polled": self._stats.polled,
            "tasks_received": self._stats.tasks_received,
            "tasks_completed": self._stats.tasks_completed,
            "tasks_failed": self._stats.tasks_failed,
            "replies_sent": self._stats.replies_sent,
            "last_poll_at": self._stats.last_poll_at,
        }

    def close(self) -> None:
        self._save_state()
        if self._bus:
            try:
                self._bus.close()
            except Exception as e:
                logger.debug("bus close failed (non-critical): %s", e)


def create_responder() -> BusResponder:
    return BusResponder()
