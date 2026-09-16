from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# plan 模式下的退出通道：plan_mode 工具自身始终可见（否则模型无法退出）。
PLAN_MODE_EXIT_TOOL = "plan_mode"

# plan 模式下额外显式封禁的工具：security_scope 虽标 read，但语义不属于
# 只读探索。request_user_input 在 plan 中提问会打断规划流；非 TTY 上下文
# （LACP remote/CI）下更会以 pending 空转（b68feb5 防卡死修复的配套收口）。
PLAN_MODE_DENY_TOOLS: frozenset[str] = frozenset({"request_user_input"})


class PlanMode:
    """Plan mode — 只读探索，封禁一切写/执行工具。

    T0-1 接线（对标 Claude Code plan mode / AtomCode PlanModeGate）：
    - `allows()` 供 CodingRuntime.execute_tool 拦截非读域工具（含 bash 等 execute 域）
    - `filter_tools()` 供 query_engine._build_openai_tools 过滤模型可见工具列表
    判定依据 ToolDefinition.security_scope：read 放行，write/execute 全封。
    """

    def __init__(self) -> None:
        self._active: bool = False

    @property
    def is_active(self) -> bool:
        return self._active

    def enter(self) -> dict[str, str]:
        self._active = True
        logger.info("Plan mode activated")
        return {"status": "active"}

    def exit(self) -> dict[str, str]:
        self._active = False
        logger.info("Plan mode deactivated")
        return {"status": "inactive"}

    def allows(self, tool_name: str, security_scope: str = "read") -> bool:
        """plan 模式下是否放行该工具：读域工具 + plan_mode 自身，显式清单除外。"""
        if not self._active:
            return True
        if tool_name in PLAN_MODE_DENY_TOOLS:
            return False
        return tool_name == PLAN_MODE_EXIT_TOOL or security_scope == "read"

    def filter_tools(self, tools: list[Any]) -> list[Any]:
        """过滤模型可见工具列表：plan 模式只保留读域工具 + plan_mode 自身。

        兼容 ToolDefinition 对象与 dict（getattr/get 双路取值）。
        """
        if not self._active:
            return list(tools)

        def _field(t: Any, key: str, default: Any) -> Any:
            if isinstance(t, dict):
                return t.get(key, default)
            return getattr(t, key, default)

        return [
            t for t in tools
            if _field(t, "name", "") == PLAN_MODE_EXIT_TOOL
            or (
                _field(t, "name", "") not in PLAN_MODE_DENY_TOOLS
                and _field(t, "security_scope", "read") == "read"
            )
        ]
