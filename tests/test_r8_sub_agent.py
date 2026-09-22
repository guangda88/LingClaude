"""R8：sub_agent 通道在主 agent 内被启用。

覆盖：
- sub_agent_handler 写 flywheel 统计
- system_prompt_builder 在 tool_call_count >= threshold 时注入 R8 推荐
- IntelConfig.auto_sub_agent_threshold 配置字段
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ── 配置字段 ────────────────────────────────────────────────────────────


class TestAutoSubAgentThresholdConfig:
    def test_default_threshold_is_5(self):
        from lingclaude.core.config import IntelConfig

        c = IntelConfig()
        assert c.auto_sub_agent_threshold == 5

    def test_custom_threshold_from_dict(self):
        """用户可在 config.yaml 设 intel.auto_sub_agent_threshold 调整阈值。"""
        from lingclaude.core.config import IntelConfig

        c = IntelConfig(auto_sub_agent_threshold=15)
        assert c.auto_sub_agent_threshold == 15

    def test_threshold_zero_means_disabled(self):
        """auto_sub_agent_threshold=0 = 关闭提示（与 L1 设计约定一致）。"""
        from lingclaude.core.config import IntelConfig

        c = IntelConfig(auto_sub_agent_threshold=0)
        assert c.auto_sub_agent_threshold == 0


# ── System prompt 注入 ──────────────────────────────────────────────────


class TestSubAgentPromptInjection:
    """threshold 触发时,system prompt 必须含 R8 推荐语。"""

    def _behavior(self, **kw):
        """构造一个真实 BehaviorMetrics 实例——system_prompt_builder 对字段非常敏感,
        MagicMock/SimpleNamespace 会因缺字段抛 AttributeError。"""
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

    def _make_args(self, **kw):
        defaults = dict(
            behavior=self._behavior(),
            layered_memory=MagicMock(inject_common_to_prompt=MagicMock(return_value="")),
            meta_cognition=MagicMock(get_system_prompt_injection=MagicMock(return_value="")),
            messages=[],
            session_cache_hits=0,
            dementia_detector=MagicMock(
                diagnose=MagicMock(return_value=MagicMock(intervention_prompt="")),
            ),
            project_index=None,
        )
        defaults.update(kw)
        return defaults

    def test_no_injection_below_threshold(self):
        from lingclaude.core.system_prompt_builder import build_dynamic_system_suffix

        args = self._make_args(tool_call_count=4)
        prompt = build_dynamic_system_suffix(**args)
        assert "R8" not in prompt, "tool_call_count < threshold 不应注入"

    def test_injection_at_threshold(self):
        from lingclaude.core.system_prompt_builder import build_dynamic_system_suffix

        args = self._make_args(tool_call_count=5)
        prompt = build_dynamic_system_suffix(**args)
        assert "R8 提示" in prompt
        assert "5 次工具调用" in prompt
        assert "sub_agent" in prompt  # 推荐使用 sub_agent 工具

    def test_injection_far_above_threshold(self):
        """大量工具调用后,提示仍然显示（不是一次性的"提议"）。"""
        from lingclaude.core.system_prompt_builder import build_dynamic_system_suffix

        args = self._make_args(tool_call_count=50)
        prompt = build_dynamic_system_suffix(**args)
        assert "50 次" in prompt

    def _behavior_with_threshold(self, threshold: int):
        """含 auto_sub_agent_threshold 字段的 SimpleNamespace（不属于 BehaviorMetrics,
        builder 通过 getattr 兜底读字段）。"""
        from types import SimpleNamespace

        return SimpleNamespace(
            hallucination_risk=0.0,
            frustration_rate=0.0,
            tool_error_rate=0.0,
            corrections_received=0,
            total_turns=0,
            turns_with_tools=0,
            tool_use_rate=0.0,
            tool_error_count=0,
            auto_sub_agent_threshold=threshold,
        )

    def test_custom_threshold_respected(self):
        from lingclaude.core.system_prompt_builder import build_dynamic_system_suffix

        args = self._make_args(
            tool_call_count=12,
            behavior=self._behavior_with_threshold(10),
        )
        prompt = build_dynamic_system_suffix(**args)
        assert "R8" in prompt, "12 >= 10 必须触发"
        assert "阈值 10" in prompt

    def test_zero_threshold_disables(self):
        """auto_sub_agent_threshold=0 = 不触发推荐语句。"""
        from lingclaude.core.system_prompt_builder import build_dynamic_system_suffix

        args = self._make_args(
            tool_call_count=100,
            behavior=self._behavior_with_threshold(0),
        )
        prompt = build_dynamic_system_suffix(**args)
        # 不查 "R8"（提示模板里始终含 "R8 提示" 字样）——查具体推荐关键字
        assert "建议拆给 sub_agent" not in prompt, "threshold=0 必须禁用推荐语句"
        assert "100 次" not in prompt

    def test_default_threshold_fallback(self):
        """behavior 缺 auto_sub_agent_threshold 字段时回落到 5。"""
        from lingclaude.core.system_prompt_builder import build_dynamic_system_suffix

        args = self._make_args(
            tool_call_count=5,
            behavior=self._behavior(),  # 不传 auto_sub_agent_threshold
        )
        prompt = build_dynamic_system_suffix(**args)
        assert "R8" in prompt


# ── Flywheel 统计 ──────────────────────────────────────────────────────


class TestSubAgentFlywheelLog:
    """sub_agent_handler 调用必须写 DataFlyWheel 让 token 节流效果可查。"""

    def test_handler_invokes_flywheel_on_success(self, monkeypatch):
        """mock 整套依赖,验证 handler 在返回前调 log_to_flywheel。

        handler 内 `from lingclaude.engine.subagent import ...` 是局部 import,
        必须通过 sys.modules.patch 模块路径才能截到。
        """
        recorded: list[dict] = []

        class FakeRuntime:
            """handler 通过 self._session_runtime.log_to_flywheel 写日志。"""

            pass

        rt = FakeRuntime()
        rt._model_provider = None
        # mock session_runtime 的 log_to_flywheel
        rt._session_runtime = MagicMock()
        rt._session_runtime.log_to_flywheel = (
            lambda pattern_type, error_message, tool_name="", file_path="", context="": recorded.append(
                {"pattern_type": pattern_type, "error_message": error_message,
                 "tool_name": tool_name, "context": context}
            )
        )

        # mock manager.run() 返回的 result
        fake_result = MagicMock(
            agent_id="a1", output="hello", tools_used=["bash"],
            success=True, error=None, rounds=2, provider="inprocess",
        )

        # handler 局部 import lingclaude.engine.subagent,在 sys.modules 替换整个模块,
        # 让 from ... import SubagentManager 拿到 mock 实例
        import lingclaude.engine.subagent as subagent_module

        class FakeSubagentModule:
            SubagentManager = lambda default="inprocess": MagicMock(run=lambda *a, **kw: fake_result)
            SubagentContext = MagicMock
            SubagentRequest = MagicMock

        monkeypatch.setattr(subagent_module, "SubagentManager", FakeSubagentModule.SubagentManager)

        from lingclaude.engine.tool_handlers import subagent_tools

        result = subagent_tools.SubagentToolsMixin._sub_agent_handler(
            self=rt,
            task="ping",
        )

        assert result.is_ok, result.error
        assert result.data["success"] is True
        assert recorded, "handler 必须写 flywheel"
        rec = recorded[0]
        assert rec["pattern_type"] == "sub_agent_call"
        assert rec["tool_name"] == "sub_agent"
        assert "success=True" in rec["error_message"]
        assert "rounds=2" in rec["error_message"]
