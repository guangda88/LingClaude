"""P1 (2026-09-23): 中文意图 → flywheel_pattern 规则检索回归保护。

背景：P1 实测发现注入检索的 LIKE 匹配对真实中文查询（"错误/权限/失败"）
天然断裂——flywheel_pattern 规则 name/description 是英文错误串，中文查询全漏。
修复：
  1. scripts/flywheel_aggregator.py build_rule: description 加中文意图标签
     （_PATTERN_ZH 映射）+ context_keywords 纳入中文标签
  2. knowledge.py search_rules: LIKE 匹配增加 pattern_json（存中文标签）
本测试固守这两处修复，防止回退成"中文全漏"。
"""
from __future__ import annotations

import pytest

from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase


def _insert_zh_rule(kb: KnowledgeBase, rid: str, name: str, desc: str,
                    pattern_json: str = "{}") -> None:
    """直接插一条带中文标签的规则（模拟 build_rule 产物）。"""
    from lingclaude.self_optimizer.learner.models import FeedbackCategory, LearnedRule, Pattern
    kb.add_rule(
        LearnedRule(
            id=rid,
            name=name,
            description=desc,
            category=FeedbackCategory.BUG_RISK,
            pattern=Pattern(
                file_patterns=("*",),
                context_keywords=("权限被拒", "permission_denial"),
            ),
            tools=(),
            frequency=100,
            confidence=0.9,
        )
    )


@pytest.fixture
def kb(tmp_path) -> KnowledgeBase:
    return KnowledgeBase(db_path=tmp_path / "kb.db")


def test_zh_tag_in_description_is_retrievable(kb):
    """中文标签写入 description → 中文查询命中（P1 修复①）。"""
    _insert_zh_rule(
        kb,
        "flywheel_pattern_perm1",
        "高频错误模式: permission_denial ×100",
        "[权限被拒] [permission_denial] * — tool=write reason=no permission",
    )
    res = kb.search_rules(keyword="权限", limit=5)
    assert res.is_ok
    assert any("flywheel_pattern" in (r.id or "") for r in res.data), \
        "中文查询'权限'应命中 flywheel_pattern 规则"


def test_zh_query_hits_pattern_json_keyword(kb):
    """context_keywords 中文标签在 pattern_json → LIKE 命中（P1 修复②）。"""
    _insert_zh_rule(
        kb,
        "flywheel_pattern_perm2",
        "高频错误模式: permission_denial ×50",
        "[permission_denial] * — no permission",
    )
    res = kb.search_rules(keyword="被拒", limit=5)
    assert res.is_ok
    assert any("flywheel_pattern" in (r.id or "") for r in res.data), \
        "pattern_json 中的中文标签'被拒'应被 LIKE 命中"


def test_english_keyword_still_works(kb):
    """修复不回退英文检索（回归保护）。"""
    _insert_zh_rule(
        kb,
        "flywheel_pattern_tool1",
        "高频错误模式: tool_error ×200",
        "[工具执行错误] [tool_error] * — command failed",
    )
    res = kb.search_rules(keyword="tool_error", limit=5)
    assert res.is_ok
    assert any("flywheel_pattern" in (r.id or "") for r in res.data), \
        "英文关键词 tool_error 应继续命中"


def test_zh_query_no_longer_polluted_by_placeholder(kb):
    """中文查询不被旧占位规则（工具错误记录）淹没（P1 清理回归保护）。"""
    from lingclaude.self_optimizer.learner.models import FeedbackCategory, LearnedRule, Pattern
    kb.add_rule(
        LearnedRule(
            id="tool_error_turn_legacy",
            name="工具错误记录",
            description="legacy 占位",
            category=FeedbackCategory.BUG_RISK,
            pattern=Pattern(file_patterns=("*",), context_keywords=("legacy",)),
            tools=(),
            frequency=1,
            confidence=0.5,
        )
    )
    _insert_zh_rule(
        kb,
        "flywheel_pattern_perm3",
        "高频错误模式: permission_denial ×80",
        "[权限被拒] [permission_denial] * — denied",
    )
    res = kb.search_rules(keyword="权限", limit=5)
    assert res.is_ok
    fw = [r for r in res.data if "flywheel_pattern" in (r.id or "")]
    assert len(fw) >= 1, "真实 flywheel_pattern 不应被占位规则挤掉"
