"""T3-1: capability seam 测试。"""
from __future__ import annotations

import pytest

from lingclaude.lacp.capability_seam import (
    CapabilitySeam,
    FS_SEAM,
    SHELL_SEAM,
    LLM_SEAM,
    SUBAGENT_SEAM,
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
