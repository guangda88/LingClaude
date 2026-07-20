"""小闭环验证: test_l5 部分测试适配到 TestCase 契约"""
import sys; sys.path.insert(0, "lingmemory")
from test_engine import TestCase, run, check, report
from lingclaude.core.l5_conversation_loop import L5ConversationLoop, L5ConversationConfig

def test_should_trigger():
    """关键词触发 — 适配为 TestCase 契约"""
    cases = []
    loop = L5ConversationLoop()
    for prompt, expected in [
        ("硬化规则：优先使用code_search", True),
        ("你好", False),
        ("今天天气怎么样", False),
        ("必须执行迁移", True),
    ]:
        tc = TestCase(name=f"trigger_{prompt[:8]}", input=prompt, expected=expected)
        tc = run(tc, lambda p: loop.should_trigger(p))
        tc = check(tc)
        cases.append(tc)
    r = report(cases)
    assert r["passed_all"], f"trigger tests failed: {r['failures']}"
    print(f"  ✅ trigger: {r['passed']}/{r['total']}")

def test_consistent_early_exit():
    """声明一致早停 — 适配 TestCase"""
    loop = L5ConversationLoop()
    def model_consistent(prompt):
        return '{"consistency_score": 0.98, "inconsistencies": []}' if "审计员" in prompt else "回应"
    tc = TestCase(
        name="consistent_early_exit",
        input=("硬化规则：优先使用code_search", ["code_search优先"], ["code_search"], model_consistent),
        expected={"hist_len": 2, "exit": True},
    )
    def run_loop(args):
        result = loop.run(*args)
        h = loop.audit_history
        return {"hist_len": len(h), "exit": h[-1].consistency_score >= 0.95 if h else False}
    tc = run(tc, run_loop)
    tc = check(tc, eq=lambda a, e: a["hist_len"] == e["hist_len"] and a["exit"] == e["exit"])
    assert tc.verdict is True, f"consistent test failed: {tc.error}"
    print(f"  ✅ consistent_early_exit: hist={tc.actual['hist_len']}")

def test_inconsistent_triggers_fix():
    """不一致触发修正"""
    loop = L5ConversationLoop()
    def model_inconsistent(prompt):
        if "审计员" in prompt:
            return '{"consistency_score": 0.2, "inconsistencies": ["声明code_search但实际用grep"]}'
        if "修正" in prompt:
            return "修正后回应：已使用code_search"
        return "应该优先使用code_search，但我用了grep -rn"
    tc = TestCase(
        name="inconsistent_triggers_fix",
        input=("硬化规则：优先使用code_search", ["code_search替代grep"], ["bash: grep"], model_inconsistent),
        expected={"hist_len": 3, "fixed": True},
    )
    def run_loop(args):
        result = loop.run(*args)
        h = loop.audit_history
        return {"hist_len": len(h), "fixed": h[-1].fixed}
    tc = run(tc, run_loop)
    tc = check(tc, eq=lambda a, e: a["hist_len"] == e["hist_len"] and a["fixed"] == e["fixed"])
    assert tc.verdict is True
    print(f"  ✅ inconsistent_triggers_fix: hist={tc.actual['hist_len']}, fixed={tc.actual['fixed']}")

test_should_trigger()
# 重建 loop 避免共享状态
test_consistent_early_exit()
test_inconsistent_triggers_fix()
print("\n✅ 小闭环验证通过 — TestCase 契约适配成功")
