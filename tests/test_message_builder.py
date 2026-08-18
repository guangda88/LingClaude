"""LINGKERNEL_v1 task #1 — MessageBuilder 测试。"""

from __future__ import annotations

from lingclaude.core.message_builder import (
    MessageBuilder,
    assemble_system_prompt,
)


class FakeBehavior:
    hallucination_risk = 0.0
    frustration_rate = 0.0
    tool_error_rate = 0.0
    corrections_received = 0
    total_turns = 0
    tool_use_rate = 0.0
    tool_error_count = 0


class FakeMemory:
    def inject_common_to_prompt(self) -> str:
        return ""

    def build_context_injection(self, current_query: str) -> str:
        return ""


class FakeMetaCog:
    def get_system_prompt_injection(self) -> str:
        return ""


class FakeDementia:
    class _Diag:
        intervention_prompt = ""

    def diagnose(self):
        return self._Diag()


def _mk_builder(
    *,
    behavior=None,
    memory=None,
    meta=None,
    dementia=None,
):
    return MessageBuilder(
        behavior=behavior or FakeBehavior(),
        layered_memory=memory or FakeMemory(),
        meta_cognition=meta or FakeMetaCog(),
        dementia_detector=dementia or FakeDementia(),
    )


def test_base_prompt_only():
    mb = _mk_builder()
    out = mb.build_adaptive_system_prompt(messages=[])
    assert out.startswith("你是灵克")
    assert "核心规则" in out
    # 无任何 extras 时, 只返回 BASE_PROMPT
    assert "⚠" not in out
    assert "💡" not in out


def test_base_prompt_constant_exposed():
    assert "开源 AI 编程助手" in MessageBuilder.BASE_PROMPT
    assert "用中文回答" in MessageBuilder.BASE_PROMPT


def test_hallucination_warning_above_threshold():
    b = FakeBehavior()
    b.hallucination_risk = 0.5
    mb = _mk_builder(behavior=b)
    out = mb.build_adaptive_system_prompt(messages=["hi"])
    assert "幻觉风险" in out


def test_hallucination_warning_below_threshold_suppressed():
    b = FakeBehavior()
    b.hallucination_risk = 0.1
    mb = _mk_builder(behavior=b)
    out = mb.build_adaptive_system_prompt(messages=["hi"])
    assert "幻觉风险" not in out


def test_frustration_warning_above_threshold():
    b = FakeBehavior()
    b.frustration_rate = 0.5
    mb = _mk_builder(behavior=b)
    out = mb.build_adaptive_system_prompt(messages=[""])
    assert "沮丧" in out


def test_tool_error_warning_above_threshold():
    b = FakeBehavior()
    b.tool_error_rate = 0.5
    mb = _mk_builder(behavior=b)
    out = mb.build_adaptive_system_prompt(messages=[""])
    assert "工具问题" in out


def test_corrections_warning_above_2():
    b = FakeBehavior()
    b.corrections_received = 3
    mb = _mk_builder(behavior=b)
    out = mb.build_adaptive_system_prompt(messages=[""])
    assert "纠正记录" in out


def test_low_tool_use_reminder():
    b = FakeBehavior()
    b.total_turns = 5
    b.tool_use_rate = 0.1
    mb = _mk_builder(behavior=b)
    out = mb.build_adaptive_system_prompt(messages=[""])
    assert "工具使用率较低" in out


def test_session_cache_hits_above_2():
    mb = _mk_builder()
    out = mb.build_adaptive_system_prompt(messages=[""], session_cache_hits=5)
    assert "文件缓存" in out


def test_dementia_intervention_included():
    class D:
        intervention_prompt = "⚠ 痴呆干预提醒"

        def diagnose(self):
            class R:
                intervention_prompt = "⚠ 痴呆干预提醒"
            return R()
    mb = _mk_builder(dementia=D())
    out = mb.build_adaptive_system_prompt(messages=[""])
    assert "痴呆干预" in out


def test_project_index_included():
    mb = _mk_builder()
    out = mb.build_adaptive_system_prompt(
        messages=[""],
        project_index={"lingclaude": ["coding.py", "tools.py"]},
    )
    assert "项目结构" in out
    assert "lingclaude" in out


def test_project_index_excludes_dot():
    """项目索引 '.' 不应作为顶级 key 出现。"""
    mb = _mk_builder()
    out = mb.build_adaptive_system_prompt(
        messages=[""],
        project_index={".": ["file1"], "lingclaude": ["file2"]},
    )
    # 渲染时 '.' 被过滤
    assert ".: " not in out


def test_meta_cognition_injection():
    class M:
        def get_system_prompt_injection(self) -> str:
            return "[META_INJECTION]"
    mb = _mk_builder(meta=M())
    out = mb.build_adaptive_system_prompt(messages=[""])
    assert "[META_INJECTION]" in out


def test_memory_injection():
    class M:
        def inject_common_to_prompt(self) -> str:
            return "[MEM_INJECTION]"

        def build_context_injection(self, current_query: str) -> str:
            return ""
    mb = _mk_builder(memory=M())
    out = mb.build_adaptive_system_prompt(messages=[""])
    assert "[MEM_INJECTION]" in out


def test_memory_experience_injection_long_enough():
    long_text = "x" * 100
    class M:
        def inject_common_to_prompt(self) -> str:
            return ""

        def build_context_injection(self, current_query: str) -> str:
            return long_text
    mb = _mk_builder(memory=M())
    out = mb.build_adaptive_system_prompt(messages=[""])
    assert long_text in out


def test_memory_experience_short_skipped():
    """build_context_injection 返回 < 50 字符会被忽略。"""
    class M:
        def inject_common_to_prompt(self) -> str:
            return ""

        def build_context_injection(self, current_query: str) -> str:
            return "short"
    mb = _mk_builder(memory=M())
    out = mb.build_adaptive_system_prompt(messages=[""])
    assert "short" not in out  # 跳过


def test_messages_last_used_for_kb_keyword():
    """KnowledgeBase keyword 取 messages[-1][:50]。"""
    messages = ["a" * 100, "b" * 100]  # 后一条长
    mb = _mk_builder()
    # 我们只验证不抛异常 (KnowledgeBase 集成在 try/except)
    out = mb.build_adaptive_system_prompt(messages=messages)
    assert out  # just succeed


def test_all_thresholds_suppressed():
    """全部低于阈值时, 任何 section 都不出现。"""
    b = FakeBehavior()
    b.hallucination_risk = 0.1
    b.frustration_rate = 0.1
    b.tool_error_rate = 0.1
    b.corrections_received = 0
    b.tool_error_count = 0
    mb = _mk_builder(behavior=b)
    out = mb.build_adaptive_system_prompt(messages=[""], session_cache_hits=0)
    # 无任何 extras section
    assert "幻觉风险" not in out
    assert "沮丧" not in out
    assert "工具问题" not in out
    assert "纠正记录" not in out
    assert "文件缓存" not in out


def test_assemble_function_equivalence():
    """便捷函数 assemble_system_prompt 与 MessageBuilder 等价。"""
    b = FakeBehavior()
    b.hallucination_risk = 0.5
    out_fn = assemble_system_prompt(
        behavior=b,
        layered_memory=FakeMemory(),
        meta_cognition=FakeMetaCog(),
        dementia_detector=FakeDementia(),
        messages=["hi"],
    )
    out_class = _mk_builder(behavior=b).build_adaptive_system_prompt(messages=["hi"])
    assert out_fn == out_class


if __name__ == "__main__":
    import sys
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
            traceback.print_exc()
    if failed:
        sys.exit(1)
    print(f"\nAll {len(fns)} tests passed")