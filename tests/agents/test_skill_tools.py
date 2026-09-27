"""skill_search/skill_read 工具面守卫测试（hermetic：只读真实索引，零网络）。"""
from __future__ import annotations

from lingclaude.engine.tool_handlers.skill_tools import (
    skill_read_impl,
    skill_search_impl,
)


def test_search_hit():
    out = skill_search_impl("review", limit=5)
    assert "code-review" in out
    assert "命中" in out


def test_search_miss():
    out = skill_search_impl("zzz-no-such-skill-xyz")
    assert "无命中" in out


def test_search_empty_keyword():
    out = skill_search_impl("", limit=3)
    assert "命中" in out or "skill" in out.lower()


def test_read_known_skill():
    out = skill_read_impl("mcp-wrap")
    assert "mcp-wrap" in out
    assert len(out) > 100  # 全文非空


def test_read_unknown_skill():
    out = skill_read_impl("zzz-no-such-skill-xyz")
    assert "未索引" in out
