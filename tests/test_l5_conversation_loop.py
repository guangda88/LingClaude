"""L5对话层循环测试

验证：触发条件、循环流程、早停、修正、审计历史
"""

import pytest
from lingclaude.core.l5_conversation_loop import (
    L5ConversationConfig,
    L5ConversationLoop,
    L5RoundResult,
)


@pytest.fixture
def l5():
    return L5ConversationLoop()


@pytest.fixture
def mock_model_consistent():
    """模拟一致的回应（声明和行为一致）"""
    def _call(prompt):
        if "审计员" in prompt:
            return '{"consistency_score": 0.98, "inconsistencies": []}'
        return "回应：已使用code_search完成搜索"
    return _call


@pytest.fixture
def mock_model_inconsistent():
    """模拟不一致的回应（声明code_search但用grep）"""
    def _call(prompt):
        if "审计员" in prompt:
            return '{"consistency_score": 0.2, "inconsistencies": ["声明用code_search但实际用grep"]}'
        if "修正" in prompt:
            return "修正后回应：已使用code_search完成搜索"
        return "回应：应该优先使用code_search，但我用了grep -rn"
    return _call


class TestShouldTrigger:
    def test_trigger_on_keywords(self, l5):
        assert l5.should_trigger("硬化规则：优先使用code_search")
        assert l5.should_trigger("必须执行迁移")
        assert l5.should_trigger("这个教训很重要")

    def test_no_trigger_on_plain_text(self, l5):
        assert not l5.should_trigger("你好")
        assert not l5.should_trigger("今天天气怎么样")
        assert not l5.should_trigger("1+1等于几")


class TestRun:
    def test_no_trigger_returns_directly(self, l5, mock_model_consistent):
        """不触发L5时直接返回model_call结果"""
        result = l5.run("你好", [], [], mock_model_consistent)
        assert result == "回应：已使用code_search完成搜索"
        assert len(l5.audit_history) == 0

    def test_consistent_early_exit(self, l5, mock_model_consistent):
        """声明和行为一致时早停（round2后）"""
        result = l5.run(
            "硬化规则：优先使用code_search",
            ["优先使用code_search"],
            ["code_search: 搜索完成"],
            mock_model_consistent,
        )
        assert len(l5.audit_history) == 2  # round1 + round2
        assert l5.audit_history[1].consistency_score >= 0.95
        assert not l5.audit_history[1].fixed

    def test_inconsistent_triggers_fix(self, l5, mock_model_inconsistent):
        """声明和行为不一致时触发修正（round3）"""
        result = l5.run(
            "硬化规则：优先使用code_search",
            ["优先使用code_search替代grep"],
            ["bash: grep -rn pattern /home/"],
            mock_model_inconsistent,
        )
        assert len(l5.audit_history) == 3  # round1 + round2 + round3
        assert l5.audit_history[1].consistency_score < 0.5
        assert len(l5.audit_history[1].inconsistencies) > 0
        assert l5.audit_history[2].fixed is True
        assert "修正" in result

    def test_empty_tool_log(self, l5, mock_model_consistent):
        """空tool_call_log也能正常运行"""
        result = l5.run("硬化规则", [], [], mock_model_consistent)
        assert isinstance(result, str)


class TestParseAudit:
    def test_valid_json(self, l5):
        score, incs = l5._parse_audit('{"consistency_score": 0.8, "inconsistencies": ["issue1"]}')
        assert score == 0.8
        assert incs == ["issue1"]

    def test_invalid_json(self, l5):
        score, incs = l5._parse_audit("not json at all")
        assert score == 0.5
        assert len(incs) > 0

    def test_missing_fields(self, l5):
        score, incs = l5._parse_audit('{"other": "value"}')
        assert score == 0.5


class TestAuditHistory:
    def test_history_accumulates(self, l5, mock_model_inconsistent):
        l5.run("规则", ["规则1"], ["bash: grep"], mock_model_inconsistent)
        assert len(l5.audit_history) == 3
        assert l5.audit_history[0].round_num == 1
        assert l5.audit_history[1].round_num == 2
        assert l5.audit_history[2].round_num == 3


class TestConfig:
    def test_custom_config(self):
        config = L5ConversationConfig(
            max_rounds=2,
            early_exit_threshold=0.9,
            fix_threshold=0.3,
        )
        loop = L5ConversationLoop(config)
        assert loop.config.max_rounds == 2
        assert loop.config.early_exit_threshold == 0.9
