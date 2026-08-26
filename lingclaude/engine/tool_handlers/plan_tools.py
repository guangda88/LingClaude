"""Plan mode 工具 handler 插片 — 从 coding.py 拆分（灵元：工具是插片）。

PlanToolsMixin: plan_mode（依赖 self.plan_mode / self._plan_mode_active）。
"""

from __future__ import annotations

from typing import Any


class PlanToolsMixin:
    """plan_mode 工具 handler。"""

    def _plan_mode_handler(self, action: str = "enter", **_kwargs: Any) -> dict[str, Any]:
        if action == "enter":
            self.plan_mode.enter()
            self._plan_mode_active = True
            return {"plan_mode": True, "message": "Plan mode activated. Tool execution disabled."}
        elif action == "exit":
            self.plan_mode.exit()
            self._plan_mode_active = False
            return {"plan_mode": False, "message": "Plan mode deactivated. Tool execution enabled."}
        return {"error": f"Unknown action: {action}. Use 'enter' or 'exit'."}
