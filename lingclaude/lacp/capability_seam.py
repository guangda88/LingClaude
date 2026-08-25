"""T3-1: Capability Seam — 能力 seam 局部引入。

对标 DSH Cordis 架构（50 packages），但明确局部引入而非全插件化。
每个能力（fs/shell/llm/subagent）为独立 Service Definition + Provider 注册表，
插件可替换实现。

约束：monorepo 已成型，不可强行拆包。
"""

from __future__ import annotations

from typing import Any, Protocol


class CapabilityProvider(Protocol):
    """能力提供者协议"""
    name: str
    version: str
    
    def execute(self, *args: Any, **kwargs: Any) -> Any:
        """执行能力"""
        ...


class CapabilitySeam:
    """能力 seam — 声明式接口 + 可替换实现"""
    
    def __init__(self, name: str, interface: dict[str, Any]):
        self.name = name
        self.interface = interface
        self._providers: dict[str, CapabilityProvider] = {}
        self._default: str | None = None
    
    def register_provider(self, provider: CapabilityProvider, default: bool = False) -> None:
        """注册能力提供者"""
        self._providers[provider.name] = provider
        if default or self._default is None:
            self._default = provider.name
    
    def get_provider(self, name: str | None = None) -> CapabilityProvider:
        """获取能力提供者（默认或指定）"""
        if name is None:
            name = self._default
        if name is None or name not in self._providers:
            raise ValueError(f"Provider not found: {name}")
        return self._providers[name]
    
    def replace_provider(self, name: str, provider: CapabilityProvider) -> None:
        """替换能力提供者（热替换）"""
        self._providers[name] = provider
    
    def list_providers(self) -> list[str]:
        """列出所有提供者"""
        return list(self._providers.keys())


# 预定义能力 seam
FS_SEAM = CapabilitySeam("fs", {
    "methods": ["read", "write", "edit", "glob", "grep"],
    "transport": "local",
})

SHELL_SEAM = CapabilitySeam("shell", {
    "methods": ["execute", "sandbox"],
    "transport": "local",
})

LLM_SEAM = CapabilitySeam("llm", {
    "methods": ["complete", "stream"],
    "transport": "http",
})

SUBAGENT_SEAM = CapabilitySeam("subagent", {
    "methods": ["spawn", "abort", "status"],
    "transport": "in-process",
})


def register_default_providers() -> None:
    """注册默认能力提供者（延迟导入避免循环依赖）"""
    from lingclaude.engine.file_ops import FileReadTool, FileWriteTool
    from lingclaude.engine.bash import BashExecutor
    
    # fs seam
    FS_SEAM.register_provider(FileReadTool(), default=True)
    FS_SEAM.register_provider(FileWriteTool())
    
    # shell seam
    SHELL_SEAM.register_provider(BashExecutor(), default=True)


# 全局注册表
_CAPABILITY_SEAMS: dict[str, CapabilitySeam] = {
    "fs": FS_SEAM,
    "shell": SHELL_SEAM,
    "llm": LLM_SEAM,
    "subagent": SUBAGENT_SEAM,
}


def get_capability_seam(name: str) -> CapabilitySeam:
    """获取能力 seam"""
    if name not in _CAPABILITY_SEAMS:
        raise ValueError(f"Capability seam not found: {name}")
    return _CAPABILITY_SEAMS[name]


def list_capability_seams() -> list[str]:
    """列出所有能力 seam"""
    return list(_CAPABILITY_SEAMS.keys())
