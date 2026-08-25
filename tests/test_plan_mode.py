from __future__ import annotations


from lingclaude.engine.plan_mode import PlanMode


class TestPlanMode:
    def test_initial_state(self) -> None:
        pm = PlanMode()
        assert pm.is_active is False

    def test_enter(self) -> None:
        pm = PlanMode()
        result = pm.enter()
        assert pm.is_active is True
        assert result["status"] == "active"

    def test_exit(self) -> None:
        pm = PlanMode()
        pm.enter()
        result = pm.exit()
        assert pm.is_active is False
        assert result["status"] == "inactive"

    def test_filter_tools_inactive(self) -> None:
        pm = PlanMode()
        tools = [{"name": "bash"}, {"name": "read"}]
        assert pm.filter_tools(tools) == tools

    def test_filter_tools_active(self) -> None:
        """T0-1 契约更新：plan 模式保留读域工具 + plan_mode 自身，不再清空。

        （旧契约返回 []，会让模型进 plan 模式后完全失能）
        """
        pm = PlanMode()
        pm.enter()
        tools = [
            {"name": "bash", "security_scope": "execute"},
            {"name": "read", "security_scope": "read"},
            {"name": "write", "security_scope": "write"},
            {"name": "plan_mode", "security_scope": "read"},
        ]
        visible = pm.filter_tools(tools)
        assert [t["name"] for t in visible] == ["read", "plan_mode"]

    def test_toggle_cycle(self) -> None:
        pm = PlanMode()
        pm.enter()
        assert pm.is_active is True
        pm.exit()
        assert pm.is_active is False
        pm.enter()
        assert pm.is_active is True
