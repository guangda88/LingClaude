"""QueryEngine 与 L5对话层循环集成测试

验证：用户sure?的代码化接入 — 关键词触发检测 + audit_history 记录 + 失败回退。
不验证 run_l5_audit_full (那需要真实LLM, 留给三方联调后实测)。
"""

from __future__ import annotations

import pytest
from lingclaude.engine.loop.l5_conversation_loop import (
    L5ConversationConfig,
    L5ConversationLoop,
    L5RoundResult,
)
from lingclaude.core.query_engine import QueryEngine


class FakeQueryEngine:
    """仅暴露 _apply_l5_audit 需要的接口, 不启动完整QueryEngine"""

    def __init__(self, alerts=None):
        self._l5_loop = L5ConversationLoop()
        self._degradation_alerts = alerts or []

    _apply_l5_audit = QueryEngine._apply_l5_audit
    _collect_relevant_rules = QueryEngine._collect_relevant_rules
    _collect_tool_call_log = QueryEngine._collect_tool_call_log
    run_l5_audit_full = QueryEngine.run_l5_audit_full
    _call_model = lambda self, prompt: "fake-llm-response"
    get_l5_audit_history = QueryEngine.get_l5_audit_history


class TestL5IntegrationTrigger:
    def test_no_trigger_no_audit(self):
        engine = FakeQueryEngine()
        out = engine._apply_l5_audit("今天天气怎么样", "晴天")
        assert out == "晴天"
        assert len(engine._l5_loop.audit_history) == 0

    def test_trigger_records_placeholder(self):
        engine = FakeQueryEngine()
        out = engine._apply_l5_audit("硬化规则：优先code_search", "已使用code_search")
        assert out == "已使用code_search"
        assert len(engine._l5_loop.audit_history) == 1
        placeholder = engine._l5_loop.audit_history[0]
        assert placeholder.round_num == 0
        assert placeholder.response == "已使用code_search"
        assert any("三方联调" in inc for inc in placeholder.inconsistencies)

    def test_trigger_records_rules_and_tool_log(self):
        from lingclaude.core.degradation_detector import DegradationAlert, DegradationSignal

        alert = DegradationAlert(
            signal=DegradationSignal.REPETITION_LOOP,
            msg_index=1,
            detail="grep called 3 times",
            severity="warning",
        )
        engine = FakeQueryEngine(alerts=[alert])
        engine._apply_l5_audit("必须执行迁移", "done")
        placeholder = engine._l5_loop.audit_history[0]
        assert any("code_search" in r for r in placeholder.declared_rules)
        assert any("grep" in log for log in placeholder.actual_actions)

    def test_failure_does_not_block_output(self):
        engine = FakeQueryEngine()
        # 破坏 audit_history 让 append 失败
        engine._l5_loop._audit_history = None  # type: ignore[assignment]
        out = engine._apply_l5_audit("硬化规则", "ok")
        assert out == "ok"  # fallback 到原 output


class TestL5CollectHelpers:
    def test_collect_relevant_rules_non_empty(self):
        engine = FakeQueryEngine()
        rules = engine._collect_relevant_rules()
        assert len(rules) >= 5
        assert any("code_search" in r for r in rules)

    def test_collect_tool_log_handles_empty(self):
        engine = FakeQueryEngine()
        log = engine._collect_tool_call_log()
        assert log == []

    def test_collect_tool_log_caps_at_10(self):
        from lingclaude.core.degradation_detector import DegradationAlert, DegradationSignal

        alerts = [
            DegradationAlert(
                signal=DegradationSignal.REPETITION_LOOP,
                msg_index=i,
                detail=f"call {i}",
                severity="warning",
            )
            for i in range(20)
        ]
        engine = FakeQueryEngine(alerts=alerts)
        log = engine._collect_tool_call_log()
        assert len(log) == 10


class TestGetL5AuditHistory:
    def test_returns_copy(self):
        engine = FakeQueryEngine()
        engine._l5_loop._audit_history.append(L5RoundResult(round_num=0, response="x"))
        h = engine.get_l5_audit_history()
        assert len(h) == 1
        h.clear()
        assert len(engine._l5_loop.audit_history) == 1  # 原历史未被改


if __name__ == "__main__":
    pytest.main([__file__, "-v"])