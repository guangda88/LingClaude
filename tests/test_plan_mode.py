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
        pm = PlanMode()
        pm.enter()
        tools = [{"name": "bash"}, {"name": "read"}]
        assert pm.filter_tools(tools) == []

    def test_toggle_cycle(self) -> None:
        pm = PlanMode()
        pm.enter()
        assert pm.is_active is True
        pm.exit()
        assert pm.is_active is False
        pm.enter()
        assert pm.is_active is True
