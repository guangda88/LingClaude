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
        self._load_state()

    def _get_bus(self) -> Any:
        if self._bus is None:
            import sys
            sys.path.insert(0, str(Path.home() / "lingmessage"))
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

    def _execute_task(self, task: ParsedTask) -> Result[str]:
        from lingclaude.engine.mcp_proxy import call_tool

        description_lower = task.description.lower()

        if any(kw in description_lower for kw in ("审查", "review", "分析", "analyze")):
            return call_tool("analyze_full", target=task.description)
        elif any(kw in description_lower for kw in ("搜索", "search", "查找", "find")):
            return call_tool("search_code", pattern=task.description)
        elif any(kw in description_lower for kw in ("测试", "test")):
            return call_tool("run_bash", command=task.description, timeout=120)
        else:
            return call_tool("run_bash", command=task.description, timeout=120)

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

        if messages:
            self._last_rowid = messages[-1].rowid

        results = []
        for msg in messages:
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
            except Exception:
                pass


def create_responder() -> BusResponder:
    return BusResponder()
