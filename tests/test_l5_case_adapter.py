"""test_l5_conversation_loop.py — TestCase 契约适配版

所有 18 个测试保持语义不变，改用 TestCase + pytest_case。
与原 pytest 文件并行存在，跑相同验证。
"""

import sys
sys.path.insert(0, "lingmemory")

from test_engine import TestCase, pytest_case
from lingclaude.engine.loop.l5_conversation_loop import (
    L5ConversationConfig,
    L5ConversationLoop,
)


# ─── 共享 fixture ──────────────────────────────────
def _consistent_model(prompt: str) -> str:
    if "审计员" in prompt:
        return '{"consistency_score": 0.98, "inconsistencies": []}'
    return "回应：已使用code_search完成搜索"


def _inconsistent_model(prompt: str) -> str:
    if "审计员" in prompt:
        return '{"consistency_score": 0.2, "inconsistencies": ["声明用code_search但实际用grep"]}'
    if "修正" in prompt:
        return "修正后回应：已使用code_search完成搜索"
    return "回应：应该优先使用code_search，但我用了grep -rn"


# ─── TestShouldTrigger (2 tests → 4 cases) ────────
def test_trigger_on_keywords():
    cases = [
        TestCase("trigger_硬化", "硬化规则：优先使用code_search", True),
        TestCase("trigger_必须", "必须执行迁移", True),
        TestCase("trigger_教训", "这个教训很重要", True),
    ]
    for tc in cases:
        pytest_case(tc, lambda p: L5ConversationLoop().should_trigger(p))


def test_no_trigger_on_plain_text():
    cases = [
        TestCase("no_trigger_你好", "你好", False),
        TestCase("no_trigger_天气", "今天天气怎么样", False),
        TestCase("no_trigger_数学", "1+1等于几", False),
    ]
    for tc in cases:
        pytest_case(tc, lambda p: L5ConversationLoop().should_trigger(p))


# ─── TestRun (4 tests) ─────────────────────────────
def test_no_trigger_returns_directly():
    loop = L5ConversationLoop()
    tc = TestCase("no_trigger_direct", "你好", "回应：已使用code_search完成搜索")
    pytest_case(tc, lambda p: loop.run(p, [], [], _consistent_model))


def test_consistent_early_exit():
    loop = L5ConversationLoop()
    tc = TestCase("consistent_early_exit", (
        "硬化规则：优先使用code_search",
        ["优先使用code_search"],
        ["code_search: 搜索完成"],
        _consistent_model,
    ), {"hist_len": 2})
    def _run(args):
        loop.run(*args)
        return {"hist_len": len(loop.audit_history)}
    pytest_case(tc, _run)


def test_inconsistent_triggers_fix():
    loop = L5ConversationLoop()
    tc = TestCase("inconsistent_triggers_fix", (
        "硬化规则：优先使用code_search",
        ["优先使用code_search替代grep"],
        ["bash: grep -rn pattern /home/"],
        _inconsistent_model,
    ), {"hist_len": 3, "fixed": True})
    def _run(args):
        loop.run(*args)
        h = loop.audit_history
        return {"hist_len": len(h), "fixed": h[-1].fixed}
    pytest_case(tc, _run)


def test_empty_tool_log():
    loop = L5ConversationLoop()
    tc = TestCase("empty_tool_log", (
        "硬化规则", [], [], _consistent_model,
    ), True)
    def _run(args):
        r = loop.run(*args)
        return isinstance(r, str)
    pytest_case(tc, _run)


# ─── TestParseAudit (3 tests) ──────────────────────
def test_valid_json():
    loop = L5ConversationLoop()
    cases = [
        TestCase("parse_valid", '{"consistency_score": 0.8, "inconsistencies": ["issue1"]}', (0.8, ["issue1"])),
    ]
    pytest_case(cases[0], lambda j: loop._parse_audit(j))


def test_invalid_json():
    loop = L5ConversationLoop()
    tc = TestCase("parse_invalid", "not json at all", (0.5, ...))
    def _check(j):
        s, incs = loop._parse_audit(j)
        return (s, ...)
    pytest_case(tc, _check)


def test_missing_fields():
    loop = L5ConversationLoop()
    tc = TestCase("parse_missing", '{"other": "value"}', 0.5)
    def _check(j):
        s, _ = loop._parse_audit(j)
        return s
    pytest_case(tc, _check)


# ─── TestAuditHistory (1 test) ─────────────────────
def test_history_accumulates():
    loop = L5ConversationLoop()
    tc = TestCase("history_accumulates", (
        "规则", ["规则1"], ["bash: grep"], _inconsistent_model,
    ), (1, 2, 3))
    def _run(args):
        loop.run(*args)
        return (loop.audit_history[0].round_num,
                loop.audit_history[1].round_num,
                loop.audit_history[2].round_num)
    pytest_case(tc, _run)


# ─── TestConfig (1 test) ───────────────────────────
def test_custom_config():
    cfg = L5ConversationConfig(max_rounds=2, early_exit_threshold=0.9, fix_threshold=0.3)
    loop = L5ConversationLoop(cfg)
    tc = TestCase("custom_config", loop, {"max_rounds": 2, "exit": 0.9, "fix": 0.3})
    pytest_case(tc, lambda l: {"max_rounds": l.config.max_rounds, "exit": l.config.early_exit_threshold, "fix": l.config.fix_threshold})


# ─── TestL5AwareMetadata (7 tests) ─────────────────
def test_default_session_id_empty():
    loop = L5ConversationLoop()
    tc = TestCase("meta_default", loop, {"sid": "", "round": 0, "total": 4, "claim": ""})
    pytest_case(tc, lambda l: {"sid": l.l5_session_id, "round": l.l5_round, "total": l.config.max_rounds, "claim": l._l5_claim})


def test_custom_session_id():
    tc = TestCase("meta_custom_sid", "session-abc-123", "session-abc-123")
    pytest_case(tc, lambda s: L5ConversationLoop(l5_session_id=s).l5_session_id)


def test_round_advances_during_run():
    loop = L5ConversationLoop(l5_session_id="sess-1")
    tc = TestCase("meta_round_advance", ("必须执行", ["rule"], ["grep"], _inconsistent_model), 3)
    def _run(args):
        loop.run(*args)
        return loop.l5_round
    pytest_case(tc, _run)


def test_no_trigger_resets_round():
    loop = L5ConversationLoop()
    loop._l5_round = 5
    tc = TestCase("meta_no_trigger_reset", ("plain text", [], [], _consistent_model), 0)
    def _run(args):
        loop.run(*args)
        return loop.l5_round
    pytest_case(tc, _run)


def test_history_records_session_and_round():
    loop = L5ConversationLoop(l5_session_id="sess-xyz")
    tc = TestCase("meta_hist_session_round", ("必须执行规则", ["rule"], ["grep"], _inconsistent_model), {"sessions": ["sess-xyz", "sess-xyz", "sess-xyz"], "rounds": [1, 2, 3]})
    def _run(args):
        loop.run(*args)
        return {"sessions": [h.l5_session_id for h in loop.audit_history], "rounds": [h.l5_round for h in loop.audit_history]}
    pytest_case(tc, _run)


def test_early_exit_records_correct_rounds():
    loop = L5ConversationLoop(l5_session_id="sess-fast")
    tc = TestCase("meta_early_exit_rounds", ("必须执行规则", ["rule"], ["code_search"], _consistent_model), [1, 2])
    def _run(args):
        loop.run(*args)
        return [h.l5_round for h in loop.audit_history]
    pytest_case(tc, _run)


def test_metadata_reflects_current_state():
    loop = L5ConversationLoop(l5_session_id="meta-test")
    tc = TestCase("meta_reflects_state", ("必须执行", [], ["x"], _inconsistent_model), {"before_round": 0, "after_sess": "meta-test", "after_round": 3})
    def _run(args):
        before = loop.get_l5_metadata()["X-L5-Round"]
        loop.run(*args)
        after = loop.get_l5_metadata()
        return {"before_round": before, "after_sess": after["X-L5-Session"], "after_round": after["X-L5-Round"]}
    pytest_case(tc, _run)
