"""claudecode#1.3 回归测试: plan_mode 显式封禁 request_user_input。

request_user_input 的 security_scope 是 read（tool_registration.py:293），
曾因此被 plan_mode 放行——规划中提问打断规划流，非 TTY 下还会 pending
空转。本文件锁定: plan 模式对其实施 allows/filter_tools 双路封禁。
"""
from __future__ import annotations

from lingclaude.engine.plan_mode import PLAN_MODE_DENY_TOOLS, PlanMode


def test_plan_mode_blocks_request_user_input():
    """allows: plan 激活时 request_user_input 被封，即便 scope=read。"""
    pm = PlanMode()
    pm.enter()
    assert pm.allows("request_user_input", "read") is False


def test_plan_mode_denied_tool_hidden_from_model():
    """filter_tools: 封禁工具不再出现在模型可见工具列表。"""
    pm = PlanMode()
    pm.enter()
    tools = [
        {"name": "read", "security_scope": "read"},
        {"name": "request_user_input", "security_scope": "read"},
        {"name": "plan_mode", "security_scope": "read"},
    ]
    names = {t["name"] for t in pm.filter_tools(tools)}
    assert "request_user_input" not in names
    assert {"read", "plan_mode"} <= names  # 正常读工具与退出通道不受影响


def test_plan_mode_inactive_keeps_tool_available():
    """非 plan 模式下 request_user_input 行为完全不变。"""
    pm = PlanMode()
    assert pm.is_active is False
    assert pm.allows("request_user_input", "read") is True
    tools = [{"name": "request_user_input", "security_scope": "read"}]
    assert pm.filter_tools(tools) == tools


def test_deny_list_contains_only_request_user_input():
    """封禁清单当前仅含 request_user_input，防止误扩。"""
    assert PLAN_MODE_DENY_TOOLS == frozenset({"request_user_input"})
