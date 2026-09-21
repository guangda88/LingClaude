"""L5对话层循环测试

验证：触发条件、循环流程、早停、修正、审计历史
"""

import pytest
from lingclaude.engine.loop.l5_conversation_loop import (
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


class TestL5AwareMetadata:
    """响应灵通 rowid 174137 工程视角: L5-aware metadata 用于避免 proxy3 fallback 误判"""

    def test_default_session_id_empty(self):
        loop = L5ConversationLoop()
        assert loop.l5_session_id == ""
        assert loop.l5_round == 0
        meta = loop.get_l5_metadata()
        assert meta["X-L5-Session"] == ""
        assert meta["X-L5-Round"] == 0
        assert meta["X-L5-Total-Rounds"] == 4  # L5ConversationConfig default
        assert meta["X-L5-Claim"] == ""

    def test_custom_session_id(self):
        loop = L5ConversationLoop(l5_session_id="session-abc-123")
        assert loop.l5_session_id == "session-abc-123"

    def test_round_advances_during_run(self, mock_model_inconsistent):
        loop = L5ConversationLoop(l5_session_id="sess-1")
        assert loop.l5_round == 0
        loop.run("必须执行", ["rule"], ["grep"], mock_model_inconsistent)
        # 3 轮后, _l5_round 停在最后一轮
        assert loop.l5_round == 3

    def test_no_trigger_resets_round(self, mock_model_consistent):
        loop = L5ConversationLoop()
        loop._l5_round = 5  # 污染值
        loop.run("plain text", [], [], mock_model_consistent)
        assert loop.l5_round == 0

    def test_history_records_session_and_round(self, mock_model_inconsistent):
        loop = L5ConversationLoop(l5_session_id="sess-xyz")
        loop.run("必须执行规则", ["rule"], ["grep"], mock_model_inconsistent)
        assert len(loop.audit_history) == 3
        for h in loop.audit_history:
            assert h.l5_session_id == "sess-xyz"
        assert [h.l5_round for h in loop.audit_history] == [1, 2, 3]

    def test_early_exit_records_correct_rounds(self, mock_model_consistent):
        loop = L5ConversationLoop(l5_session_id="sess-fast")
        loop.run("必须执行规则", ["rule"], ["code_search"], mock_model_consistent)
        # 一致, 2 轮早停 (round 1 + round 2)
        assert [h.l5_round for h in loop.audit_history] == [1, 2]

    def test_metadata_reflects_current_state(self, mock_model_inconsistent):
        loop = L5ConversationLoop(l5_session_id="meta-test")
        meta_before = loop.get_l5_metadata()
        assert meta_before["X-L5-Round"] == 0
        loop.run("必须执行", [], ["x"], mock_model_inconsistent)
        meta_after = loop.get_l5_metadata()
        assert meta_after["X-L5-Session"] == "meta-test"
        assert meta_after["X-L5-Round"] == 3
