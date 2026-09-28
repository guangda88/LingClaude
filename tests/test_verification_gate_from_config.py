"""P0 红→绿验收：verification_gate 走 from_config + _lsp_provider 死槽移除。

红因（改动前）：
- CodingRuntime.__init__ 裸调 VerificationGate()，config.yaml 的 verification 段被旁路；
- _lsp_provider 恒 None 占着主干一个直构位，是"截肢测试"现成案例。

改动后以下测试必须绿。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from lingclaude.core.config import VerificationConfig, lingclaudeConfig
from lingclaude.engine.coding import CodingRuntime
from lingclaude.engine.verification_gate import VerificationGate


def _config_with_verification(verification: VerificationConfig) -> lingclaudeConfig:
    """最小 lingclaudeConfig，仅覆盖 verification 字段。"""
    base = lingclaudeConfig()
    return replace(base, verification=verification)


class TestVerificationGateFromConfig:
    """主干必须走 VerificationGate.from_config 工厂，不接受裸默认值。"""

    def test_gate_uses_config_enabled_flag(self) -> None:
        cfg = _config_with_verification(VerificationConfig(enabled=False))
        runtime = CodingRuntime(cfg)
        assert runtime.verification_gate.enabled is False

    def test_gate_uses_config_blocked_extensions(self) -> None:
        cfg = _config_with_verification(VerificationConfig(blocked_extensions=(".py", ".md")))
        runtime = CodingRuntime(cfg)
        assert runtime.verification_gate.blocked_extensions == (".py", ".md")

    def test_gate_uses_config_max_tool_calls(self) -> None:
        cfg = _config_with_verification(VerificationConfig(max_tool_calls_per_session=7))
        runtime = CodingRuntime(cfg)
        assert runtime.verification_gate.max_tool_calls_per_session == 7

    def test_gate_default_config_matches_bare_constructor(self) -> None:
        """无自定义 config 时，from_config(VerificationConfig()) 与裸调等价（回归保护）。"""
        runtime = CodingRuntime(lingclaudeConfig())
        default_gate = VerificationGate()
        assert runtime.verification_gate.enabled == default_gate.enabled
        assert runtime.verification_gate.syntax_check == default_gate.syntax_check
        assert runtime.verification_gate.test_run == default_gate.test_run
        assert runtime.verification_gate.test_command == default_gate.test_command
        assert runtime.verification_gate.blocked_extensions == default_gate.blocked_extensions
        assert runtime.verification_gate.allowed_write_roots == default_gate.allowed_write_roots
        assert (
            runtime.verification_gate.max_tool_calls_per_session
            == default_gate.max_tool_calls_per_session
        )


class TestLspProviderDeadSlotRemoved:
    """P0 截肢测试：_lsp_provider 死槽必须不再被 __init__ 直构。"""

    def test_lsp_provider_not_initialized_by_init(self) -> None:
        runtime = CodingRuntime(lingclaudeConfig())
        # __init__ 不再直构；legacy 注入路径由 lsp_tools 的 getattr 兜底，
        # 因此这里用 pytest.raises 验证属性确实未被 __init__ 写入。
        with pytest.raises(AttributeError):
            _ = runtime._lsp_provider

    def test_lsp_workspace_root_not_initialized_by_init(self) -> None:
        runtime = CodingRuntime(lingclaudeConfig())
        with pytest.raises(AttributeError):
            _ = runtime._lsp_workspace_root
