"""PreToolUse hook for L7/L10 工程化 v0.2

依据灵族方向例会 #3 (LM-20260727-0945) L7/L10 实施规划 v0.1 + 11 决策点 R2 收敛：

- 强制注入 CRUSH.md 身份摘要（灵安 R1 安全边界修订）
- 强制查询 lingmemory 上次任务（避免 9:30 假会议基于过期快照）
- 强制角色边界检查（check_role_boundary，议程 5 R7 教训）
- 强制 LingBus 实时状态（议程 2 治理盲区 #3）
- 5s 超时降级为 warning 而非 block（灵扬 R1 社区运营 25s 延迟担忧）
- READ_ONLY_TOOLS 白名单（灵创 R1 工具分类）
- signature + audit trail（灵安 R1 安全前置 #2）

部署位置：~/.crush/hooks/pre_tool_use.py（符号链接或拷贝）
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path

AGENT_ID = os.environ.get("LINGCLAUDE_AGENT_ID", "lingclaude")
HOOK_TIMEOUT = int(os.environ.get("L7_HOOK_TIMEOUT", "5"))
HOOK_AUDIT_LOG = Path(
    os.environ.get(
        "L7_HOOK_AUDIT_LOG",
        "/home/ai/lingclaude/.lingclaude/hooks/audit.log",
    )
)

READ_ONLY_TOOLS = frozenset(
    {
        "list",
        "get",
        "read",
        "view",
        "ls",
        "discover",
        "search",
        "glob",
        "grep",
        "audit_log",
        "get_history",
        "export",
    }
)

WRITE_TOOLS = frozenset(
    {
        "edit",
        "write",
        "multiedit",
        "bash",
        "delete",
        "create",
        "deploy",
        "publish",
        "register",
        "save",
        "upload",
        "update",
    }
)

GOVERNANCE_ACTIONS = frozenset(
    {
        "vote",
        "vote_rule_change",
        "propose_rule",
        "amend_rule",
        "evaluate_member",
        "assign_tier",
        "check_governance",
        "penalize_member",
        "collect_stats",
        "calculate_score",
        "generate_report",
    }
)

logger = logging.getLogger("l7_pre_tool_use")
logger.setLevel(logging.INFO)


def _audit(message: str) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    line = f"{ts} {AGENT_ID} {message}\n"
    try:
        HOOK_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with HOOK_AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception as exc:
        logger.warning("audit log write failed: %s", exc)


def inject_agents_md_summary(context: dict) -> tuple[str, str]:
    """注入 CRUSH.md 身份摘要（灵安 R1 安全边界修订）。

    Returns: (status, content) — status in {"pass", "warning", "block"}
    """
    agents_md = Path("/home/ai/lingclaude/AGENTS.md")
    if not agents_md.exists():
        return ("warning", "AGENTS.md not found")
    try:
        content = agents_md.read_text(encoding="utf-8")[:500]
        context["agents_md_summary"] = content
        _audit("inject_agents_md_summary ok")
        return ("pass", content)
    except Exception as exc:
        return ("warning", f"AGENTS.md read failed: {exc}")


def inject_community_tone_subset(context: dict) -> tuple[str, str]:
    """灵扬 R1 对话术子集注入（仅当目标是社区/外部场景）。"""
    tone_file = Path("/home/ai/lingclaude/.lingclaude/community_tone_subset.md")
    if not tone_file.exists():
        return ("warning", "tone subset not found, skip")
    target = context.get("target", "")
    if "community" not in target and "external" not in target and "researcher" not in target:
        return ("pass", "skip: non-community target")
    try:
        content = tone_file.read_text(encoding="utf-8")
        context["community_tone"] = content
        _audit("inject_community_tone_subset ok")
        return ("pass", content)
    except Exception as exc:
        return ("warning", f"tone subset read failed: {exc}")


def inject_previous_task(context: dict) -> tuple[str, str]:
    """强制查询 lingmemory 上次任务，避免 9:30 假会议模式。"""
    try:
        result = subprocess.run(
            [
                "python3",
                "-c",
                (
                    "from lingclaude.core.lingmemory_client import LingmemoryClient; "
                    "client = LingmemoryClient(member='%s'); "
                    "records = client.query(type='session', limit=1); "
                    "print(records[0].get('data', {}).get('summary', '') if records else 'no previous')"
                )
                % AGENT_ID,
            ],
            capture_output=True,
            text=True,
            timeout=HOOK_TIMEOUT,
            cwd="/home/ai/lingclaude",
        )
        previous = result.stdout.strip()[:300]
        context["previous_task"] = previous
        _audit("inject_previous_task ok")
        return ("pass", previous)
    except subprocess.TimeoutExpired:
        return ("warning", f"previous_task query timeout >{HOOK_TIMEOUT}s")
    except Exception as exc:
        return ("warning", f"previous_task query failed: {exc}")


def inject_live_state(context: dict) -> tuple[str, str]:
    """强制 LingBus 实时状态注入（议程 2 治理盲区 #3）。"""
    try:
        result = subprocess.run(
            [
                "python3",
                "-c",
                (
                    "import json; from ling_term_mcp import poll_messages; "
                    "msgs = poll_messages(channels='council', limit=5, recipient='%s', since_rowid=0); "
                    "print(json.dumps([{'rowid': m.get('rowid'), 'subject': m.get('subject', '')[:50]} for m in msgs]))"
                )
                % AGENT_ID,
            ],
            capture_output=True,
            text=True,
            timeout=HOOK_TIMEOUT,
        )
        live = result.stdout.strip()[:300]
        context["live_state"] = live
        _audit("inject_live_state ok")
        return ("pass", live)
    except subprocess.TimeoutExpired:
        return ("warning", f"live_state query timeout >{HOOK_TIMEOUT}s")
    except Exception as exc:
        return ("warning", f"live_state query failed: {exc}")


def check_role_boundary(context: dict) -> tuple[str, str]:
    """角色越位检查（议程 5 R7 教训：议程 owner vs 召集人 vs 主持人）。"""
    try:
        from lingclaude.core.role_separation import (
            AgentRoles,
            ROLE_DEFINITIONS,
            RoleConflictChecker,
            RoleType,
        )

        checker = RoleConflictChecker(
            agent_roles=[
                AgentRoles(
                    agent_id=AGENT_ID,
                    roles=[ROLE_DEFINITIONS[RoleType.PARTICIPANT]],
                    enabled=True,
                ),
            ]
        )
        result = checker.check_role_boundary(
            {
                "agent_id": AGENT_ID,
                "action": context.get("action", "edit"),
                "agenda_owner": context.get("agenda_owner"),
                "convener": context.get("convener"),
                "host": context.get("host"),
            }
        )
        if not result["allowed"]:
            _audit(f"check_role_boundary BLOCK: {result['reason']}")
            return ("block", result["reason"])
        _audit("check_role_boundary ok")
        return ("pass", "role boundary ok")
    except Exception as exc:
        return ("warning", f"check_role_boundary failed: {exc}")


def is_read_only(tool_name: str) -> bool:
    name = tool_name.lower()
    if name in READ_ONLY_TOOLS:
        return True
    return any(name.startswith(p + "_") or name.startswith(p + "-") for p in READ_ONLY_TOOLS)


def main() -> int:
    try:
        context = json.loads(os.environ.get("L7_CONTEXT_JSON", "{}"))
    except json.JSONDecodeError:
        context = {}

    tool_name = context.get("tool_name", "")
    is_ro = is_read_only(tool_name)
    action = context.get("action", tool_name)
    is_governance = action in GOVERNANCE_ACTIONS

    hooks = [
        ("inject_agents_md_summary", inject_agents_md_summary),
        ("inject_community_tone_subset", inject_community_tone_subset),
        ("inject_previous_task", inject_previous_task),
        ("inject_live_state", inject_live_state),
        ("check_role_boundary", check_role_boundary),
    ]

    if is_ro:
        hooks = [h for h in hooks if h[0] != "check_role_boundary" and h[0] != "inject_community_tone_subset"]

    if not is_governance and not is_ro:
        hooks = [h for h in hooks if h[0] != "check_role_boundary"]

    with ThreadPoolExecutor(max_workers=len(hooks)) as executor:
        futures = {executor.submit(h, context): name for name, h in hooks}
        for future, name in futures.items():
            try:
                status, content = future.result(timeout=HOOK_TIMEOUT)
                if status == "block":
                    _audit(f"hook {name} BLOCKED: {content}")
                    print(json.dumps({"blocked": True, "hook": name, "reason": content}))
                    return 1
            except FuturesTimeout:
                _audit(f"hook {name} TIMEOUT, degraded to warning")
                print(json.dumps({"warning": f"{name} timeout >{HOOK_TIMEOUT}s, degraded"}))
            except Exception as exc:
                _audit(f"hook {name} failed: {exc}")
                print(json.dumps({"warning": f"{name} failed: {exc}"}))

    print(json.dumps({"blocked": False, "context_keys": sorted(context.keys())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())