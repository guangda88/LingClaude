"""R9：长任务首 turn 拆解信号（对 R8 的前置补位）。

覆盖：
- is_decomposition_candidate 判据（长度 200 + 动词 2）
- build_adaptive_system_prompt 的 current_query 注入路径
- R8/R9 正交性（阈值内 R8 不出、R9 出）
- 时序约束：_build_adaptive_system_prompt 由 _build_messages(prompt) 显式传当前 query
"""
from __future__ import annotations

import pytest

from lingclaude.model.intelligent_router import is_decomposition_candidate


class TestIsDecompositionCandidate:
    # 中文信息密度高：200 字符 ≈ 一整段任务描述。夹具必须真超阈值。
    LONG_Q = (
        "请查看并整理仓库的未提交改动，然后分析 task_router 的路由逻辑，"
        "重构 query_engine 的消息构建链路，优化 system prompt 的注入效率，"
        "修复 model_call 里的死代码，测试回归后按仓库纪律提交。"
        "另外顺带梳理 tool_executor 里 provider 选择与 legacy router 的优先级关系，"
        "把结论写进 docs，并检查 wiring manifest 里各组件的接线状态是否与实际一致。"
    )

    def test_long_with_verbs(self):
        """>200 字符 + 动词≥2 → True。"""
        assert len(self.LONG_Q) > 200
        assert is_decomposition_candidate(self.LONG_Q) is True

    def test_short_with_verbs_rejected(self):
        """短 prompt 即使多动词也不触发（与建议判据一致）。"""
        q = "查看改动并修复 bug，然后测试提交"
        assert len(q) <= 200
        assert is_decomposition_candidate(q) is False

    def test_long_without_verbs_rejected(self):
        """>200 字符但无动作词（纯粘贴文本/日志）不触发。"""
        q = "x" * 260
        assert is_decomposition_candidate(q) is False

    def test_long_single_verb_rejected(self):
        """>200 字符但只 1 个动词不触发。"""
        q = "请修复 " + "一些细节 " * 30 + "谢谢"
        assert is_decomposition_candidate(q) is False

    def test_empty_and_boundary(self):
        assert is_decomposition_candidate("") is False
        assert is_decomposition_candidate("a" * 201 + " 分析 修复") is True


class TestR9PromptInjection:
    """build_adaptive_system_prompt(current_query=...) 的注入行为。"""

    def _behavior(self, **kw):
        from lingclaude.core.behavior import BehaviorMetrics

        defaults = dict(
            total_turns=0,
            turns_with_tools=0,
            turns_without_tools_but_needed=0,
            tool_call_count=0,
            tool_error_count=0,
            emotions_detected=(),
            corrections_received=0,
            frustration_count=0,
        )
        defaults.update(kw)
        return BehaviorMetrics(**defaults)

    def _build(self, **kw):
        from lingclaude.core.system_prompt_builder import build_adaptive_system_prompt
        from lingclaude.core.meta_cognition import MetaCognition
        from lingclaude.core.dementia_detector import DementiaDetector

        args = dict(
            behavior=kw.pop("behavior", self._behavior()),
            layered_memory=kw.pop("layered_memory", _StubLayeredMemory()),
            meta_cognition=kw.pop("meta_cognition", MetaCognition()),
            messages=kw.pop("messages", []),
            session_cache_hits=kw.pop("session_cache_hits", 0),
            dementia_detector=kw.pop("dementia_detector", DementiaDetector()),
            project_index=kw.pop("project_index", None),
        )
        args.update(kw)
        return build_adaptive_system_prompt(**args)

    def test_r9_injected_first_turn(self):
        """首 turn（tool_call_count=0）长任务 → R9 提示出现。"""
        prompt = self._build(tool_call_count=0, current_query=TestIsDecompositionCandidate.LONG_Q)
        assert "R9 提示" in prompt
        assert "3-7 个子任务" in prompt

    def test_r9_absent_for_short_query(self):
        """短 query → 无 R9。"""
        prompt = self._build(tool_call_count=0, current_query="你好")
        assert "R9 提示" not in prompt

    def test_r9_absent_when_query_missing(self):
        """不传 current_query（旧调用方）→ 不注入，向后兼容。"""
        prompt = self._build(tool_call_count=0)
        assert "R9 提示" not in prompt

    def test_r9_and_r8_orthogonal(self):
        """R8/R9 正交：阈值内 R8 不出、R9 照出。"""
        prompt = self._build(tool_call_count=1, current_query=TestIsDecompositionCandidate.LONG_Q)
        assert "R8 提示" not in prompt
        assert "R9 提示" in prompt

    def test_r9_not_triggered_by_empty_string(self):
        """空串按 falsy 跳过判定（含 import 异常路径也不炸）。"""
        prompt = self._build(tool_call_count=0, current_query="")
        assert "R9 提示" not in prompt


class _StubLayeredMemory:
    """最小桩：只实现 builder 用到的注入接口。"""

    def inject_common_to_prompt(self) -> str:
        return ""

    def build_context_injection(self) -> str:
        return ""


# 防呆：query_engine_model_mixin 的 R9 取数点必须显式来自 _build_messages(prompt)
def test_mixin_passes_prompt_through():
    import inspect

    from lingclaude.core import query_engine_turn_mixin as tm

    src = inspect.getsource(tm)
    assert "_build_adaptive_system_prompt(current_query=prompt)" in src


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
