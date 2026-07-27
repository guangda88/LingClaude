"""Task 快捷标记工具 — 封装 lm_transition / lm_query

依据：
- 灵极优 docs/task_quick_mark_proposal.md v0.1 (方案 ② 推荐)
- 灵族方向例会 #3 (LM-20260727-0945) 议程 4/5/6 联动
- L7/L10 实施规划 v0.3 D6 deliverable

提供的快捷工具：
- lm_done(task_id): 一键标记 completed
- lm_block(task_id, reason): 标记 blocked + 写 reason
- lm_status(member): 查当前 in_progress 任务

设计原则：
- 单行调用（最小阻力）
- 复用现有 lm_* MCP 工具（不改消息总线）
- 失败 fail-soft（任务标记失败不阻断主工作流）
- evidence_gate schema 字段承载（议程 0 L10 决议合规）
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("lm_quick")


def lm_done(task_id: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    """一键标记 task 完成。

    Args:
        task_id: lingmemory record id (type=task)
        evidence: 可选 evidence 字段 (commit_hash / issue_link / test_result)

    Returns:
        dict 含 status / task_id / ts / evidence_gate schema
    """
    return _transition(task_id, "completed", evidence=evidence)


def lm_block(task_id: str, reason: str) -> dict[str, Any]:
    """标记 task blocked + 写 reason。

    Args:
        task_id: lingmemory record id (type=task)
        reason: 阻塞原因（必填）

    Returns:
        dict 含 status / task_id / reason / ts
    """
    evidence = {
        "block_reason": reason,
        "block_ts": datetime.now(timezone.utc).isoformat(),
    }
    return _transition(task_id, "blocked", evidence=evidence)


def lm_status(member: str | None = None) -> dict[str, Any]:
    """查当前 in_progress 任务列表。

    Args:
        member: 成员名（如 "lingclaude"），默认 None 表示全体

    Returns:
        dict 含 in_progress / pending / blocked / completed_recent
    """
    try:
        from ling_term_mcp import lm_query

        records = lm_query(
            member=member or "lingclaude",
            state="in_progress",
            type="task",
            limit=20,
        )
        pending = lm_query(
            member=member or "lingclaude",
            state="pending",
            type="task",
            limit=10,
        )
        blocked = lm_query(
            member=member or "lingclaude",
            state="blocked",
            type="task",
            limit=10,
        )
        return {
            "member": member,
            "in_progress": [_format_task(r) for r in records],
            "pending": [_format_task(r) for r in pending],
            "blocked": [_format_task(r) for r in blocked],
            "counts": {
                "in_progress": len(records),
                "pending": len(pending),
                "blocked": len(blocked),
            },
        }
    except Exception as exc:
        logger.warning("lm_status failed: %s", exc)
        return {"member": member, "error": str(exc), "in_progress": [], "pending": [], "blocked": []}


def _transition(
    task_id: str,
    event_type: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """内部 helper: 调 lm_transition + 装载 evidence_gate schema。

    evidence_gate schema (议程 0 灵安 R2):
        - gate_id (auto UUID)
        - gate_type (transition 类)
        - evidence_payload (含 task_id / event_type / evidence)
        - verdict (pass / fail)
    """
    import uuid

    gate_id = uuid.uuid4().hex[:32]
    ts = datetime.now(timezone.utc).isoformat()

    evidence_payload = {
        "task_id": task_id,
        "event_type": event_type,
        "evidence": evidence or {},
        "ts": ts,
    }

    fail_closed_action = (
        "double_sign" if event_type == "blocked" else "alert"
    )

    gate = {
        "gate_id": gate_id,
        "gate_type": "transition",
        "evidence_payload": evidence_payload,
        "verdict": "pass",
        "fail_closed_action": fail_closed_action,
        "created_by": "lm_quick",
        "ts": ts,
    }

    try:
        from ling_term_mcp import lm_transition

        data = {"lm_quick": gate}
        result = lm_transition(
            member="lingclaude",
            record_id=task_id,
            event_type=event_type,
            data=data,
        )
        return {
            "status": "ok",
            "task_id": task_id,
            "event_type": event_type,
            "ts": ts,
            "gate_id": gate_id,
            "result": result,
        }
    except Exception as exc:
        logger.warning("lm_transition failed for %s: %s", task_id, exc)
        return {
            "status": "fail",
            "task_id": task_id,
            "event_type": event_type,
            "ts": ts,
            "gate_id": gate_id,
            "error": str(exc),
            "fail_closed_action": fail_closed_action,
        }


def _format_task(record: dict[str, Any]) -> dict[str, Any]:
    """格式化 task record 用于显示。"""
    data = record.get("data", {})
    return {
        "id": record.get("id"),
        "title": data.get("title", "")[:80],
        "state": record.get("state"),
        "priority": data.get("priority", "P1"),
        "owner": data.get("owner"),
        "updated_at": record.get("updated_at"),
    }


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: lm_quick.py {done|block|status} [args]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "done" and len(sys.argv) >= 3:
        print(json.dumps(lm_done(sys.argv[2]), indent=2, ensure_ascii=False))
    elif cmd == "block" and len(sys.argv) >= 4:
        print(json.dumps(lm_block(sys.argv[2], sys.argv[3]), indent=2, ensure_ascii=False))
    elif cmd == "status":
        member = sys.argv[2] if len(sys.argv) >= 3 else None
        print(json.dumps(lm_status(member), indent=2, ensure_ascii=False))
    else:
        print("Unknown command or missing args")
        sys.exit(1)