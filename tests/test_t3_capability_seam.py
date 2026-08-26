"""T3-1: capability seam 测试。"""
from __future__ import annotations

import pytest

from lingclaude.lacp.capability_seam import (
    CapabilitySeam,
    FS_SEAM,
    SHELL_SEAM,
    LLM_SEAM,
    SUBAGENT_SEAM,
    SANDBOX_SEAM,
    get_capability_seam,
    list_capability_seams,
)


class TestCapabilitySeam:
    """能力 seam 基础功能。"""

    def test_capability_seams_exist(self):
        """4 个能力 seam 已定义。"""
        seams = list_capability_seams()
        assert "fs" in seams
        assert "shell" in seams
        assert "llm" in seams
        assert "subagent" in seams

    def test_get_capability_seam(self):
        """获取能力 seam。"""
        fs = get_capability_seam("fs")
        assert fs.name == "fs"
        assert "read" in fs.interface["methods"]

    def test_register_and_get_provider(self):
        """注册 + 获取提供者。"""
        class MockProvider:
            name = "mock"
            version = "0.1.0"
            def execute(self, *args, **kwargs):
                return "mock_result"
        
        seam = CapabilitySeam("test", {"methods": ["test"]})
        provider = MockProvider()
        seam.register_provider(provider, default=True)
        
        assert seam.get_provider() == provider
        assert seam.get_provider("mock") == provider

    def test_replace_provider(self):
        """热替换提供者。"""
        class OldProvider:
            name = "old"
            version = "0.1.0"
            def execute(self, *args, **kwargs):
                return "old"
        
        class NewProvider:
            name = "new"
            version = "0.2.0"
            def execute(self, *args, **kwargs):
                return "new"
        
        seam = CapabilitySeam("test", {"methods": ["test"]})
        seam.register_provider(OldProvider(), default=True)
        assert seam.get_provider().name == "old"
        
        seam.replace_provider("old", NewProvider())
        assert seam.get_provider("old").name == "new"

    def test_provider_not_found(self):
        """提供者不存在时抛错。"""
        seam = CapabilitySeam("test", {"methods": ["test"]})
        with pytest.raises(ValueError, match="Provider not found"):
            seam.get_provider()


class TestCapabilitySeamIntegration:
    """能力 seam 集成。"""

    def test_fs_seam_interface(self):
        """fs seam 接口定义。"""
        assert FS_SEAM.name == "fs"
        assert "read" in FS_SEAM.interface["methods"]
        assert "write" in FS_SEAM.interface["methods"]

    def test_shell_seam_interface(self):
        """shell seam 接口定义。"""
        assert SHELL_SEAM.name == "shell"
        assert "execute" in SHELL_SEAM.interface["methods"]

    def test_llm_seam_interface(self):
        """llm seam 接口定义。"""
        assert LLM_SEAM.name == "llm"
        assert "complete" in LLM_SEAM.interface["methods"]

    def test_subagent_seam_interface(self):
        """subagent seam 接口定义。"""
        assert SUBAGENT_SEAM.name == "subagent"
        assert "spawn" in SUBAGENT_SEAM.interface["methods"]

    def test_sandbox_seam_interface(self):
        """sandbox seam 接口定义（P0 sandbox_seam）。"""
        assert SANDBOX_SEAM.name == "sandbox"
        assert "wrap" in SANDBOX_SEAM.interface["methods"]
        assert "available" in SANDBOX_SEAM.interface["methods"]

    def test_sandbox_provider_replaceable(self):
        """实证：sandbox 后端插片可换（mock 替换默认 → get_provider 返回新 provider）。

        灵元尺子：主干（seam 协议）不变，插片（provider 实现）可换。
        SignedProvider 双签治理：execute 前需 lingclaude+lingminopt 双签（治理设计）。
        """
        class FakeSandbox:
            name = "fake"
            version = "0.1.0"
            def execute(self, command, working_dir=None):
                return f"FAKE_WRAPPED:{command}"

        # 注册 fake 为默认 → get_provider 返回 fake（name 断言即证明可换）
        SANDBOX_SEAM.register_provider(FakeSandbox(), default=True)
        provider = SANDBOX_SEAM.get_provider()
        assert provider.name == "fake", f"默认应切到 fake, 实际 {provider.name}"
        # 双签后 execute（治理要求）
        if hasattr(provider, "sign"):
            assert provider.sign("lingclaude") is True
            assert provider.sign("lingminopt") is True
            assert provider.is_approved
            assert provider.execute("echo hi") == "FAKE_WRAPPED:echo hi"
        # 再替换为另一个 fake2 → 同 seam 可继续换
        class FakeSandbox2:
            name = "fake2"
            version = "0.1.0"
            def execute(self, command, working_dir=None):
                return f"FAKE2:{command}"
        SANDBOX_SEAM.replace_provider("fake", FakeSandbox2())
        provider2 = SANDBOX_SEAM.get_provider("fake")
        assert provider2.name == "fake2", f"替换后应为 fake2, 实际 {provider2.name}"
        if hasattr(provider2, "sign"):
            assert provider2.sign("lingclaude") is True
            assert provider2.sign("lingminopt") is True
            assert provider2.execute("x") == "FAKE2:x"
