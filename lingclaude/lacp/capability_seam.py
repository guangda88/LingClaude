"""T3-1: Capability Seam — 能力 seam 局部引入。

对标 DSH Cordis 架构（50 packages），但明确局部引入而非全插件化。
每个能力（fs/shell/llm/subagent）为独立 Service Definition + Provider 注册表，
插件可替换实现。

约束：monorepo 已成型，不可强行拆包。
"""

from __future__ import annotations

import time as _time
from typing import Any, Protocol


class CapabilityProvider(Protocol):
    """能力提供者协议"""
    name: str
    version: str
    
    def execute(self, *args: Any, **kwargs: Any) -> Any:
        """执行能力"""
        ...


class SignedProvider:
    """双签能力提供者包装器."""
    
    def __init__(self, provider: CapabilityProvider, required_signers: list[str] = None):
        self._wrapped = provider
        # 空列表 [] = 免签（纯展示能力）；None = 默认双签
        if required_signers is None:
            self.required_signers = set(["lingclaude", "lingminopt"])
        else:
            self.required_signers = set(required_signers)
        self.signed_by: set[str] = set()
        self._approved = len(self.required_signers) == 0
    
    @property
    def name(self) -> str:
        return self._wrapped.name
    
    @property
    def version(self) -> str:
        return self._wrapped.version
    
    def sign(self, signer: str) -> bool:
        """记录签名."""
        if signer not in self.required_signers:
            return False
        self.signed_by.add(signer)
        if self.signed_by >= self.required_signers:
            self._approved = True
        return True
    
    @property
    def is_approved(self) -> bool:
        return self._approved
    
    def execute(self, *args: Any, **kwargs: Any) -> Any:
        """执行能力 (需双签)."""
        if not self._approved:
            raise PermissionError(
                f"Provider {self.name} 未获双签: "
                f"已签={[*self.signed_by]}, 待签={[*self.required_signers - self.signed_by]}"
            )
        return self._wrapped.execute(*args, **kwargs)
    
    def __eq__(self, other):
        """相等性比较 (解包后比较)."""
        if isinstance(other, SignedProvider):
            return self._wrapped == other._wrapped
        return self._wrapped == other
    
    def __repr__(self):
        return f"SignedProvider({self._wrapped!r})"


class CapabilitySeam:
    """能力 seam — 声明式接口 + 可替换实现 + 安全审计."""
    
    def __init__(self, name: str, interface: dict[str, Any]):
        self.name = name
        self.interface = interface
        self._providers: dict[str, CapabilityProvider] = {}
        self._default: str | None = None
        self._audit_log: list[dict] = []
    
    def register_provider(
        self,
        provider: CapabilityProvider,
        default: bool = False,
        required_signers: list[str] | None = None,
    ) -> None:
        """注册能力提供者 (默认加双签保护).

        required_signers: 签名者白名单. None=默认双签("lingclaude","lingminopt");
        [] = 免签（只读/纯展示能力用，如 TUI 渲染，不适用能力执行的双签安全模型）.
        """
        wrapped = SignedProvider(provider, required_signers=required_signers)
        self._providers[provider.name] = wrapped
        if default or self._default is None:
            self._default = provider.name
        self._audit_log.append({
            "action": "register",
            "seam": self.name,
            "provider": provider.name,
            "timestamp": _time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
    
    def get_provider(self, name: str | None = None) -> CapabilityProvider:
        """获取能力提供者（默认或指定）."""
        if name is None:
            name = self._default
        if name is None or name not in self._providers:
            raise ValueError(f"Provider not found: {name}")
        return self._providers[name]
    
    def replace_provider(self, name: str, provider: CapabilityProvider) -> None:
        """替换能力提供者 (热替换, 需双签保护)."""
        if name not in self._providers:
            raise ValueError(f"Provider {name} 不存在")
        old = self._providers[name]
        self._providers[name] = SignedProvider(provider)
        self._audit_log.append({
            "action": "replace",
            "seam": self.name,
            "provider": name,
            "old_approved": getattr(old, '_approved', True),
            "new_approved": False,
            "timestamp": _time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
    
    def list_providers(self) -> list[str]:
        """列出所有提供者."""
        return list(self._providers.keys())
    
    def get_audit_log(self) -> list[dict]:
        """获取审计日志."""
        return list(self._audit_log)


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

SANDBOX_SEAM = CapabilitySeam("sandbox", {
    "methods": ["wrap", "available"],
    "transport": "local",
})


def register_default_providers() -> None:
    """注册默认能力提供者（延迟导入避免循环依赖）。

    修复：FileReadTool/FileWriteTool 不存在，实际类名是 FileOps/BashExecutor。
    FileOps/BashExecutor 无 name/version 属性，用类名代替。
    """
    from lingclaude.engine.file_ops import FileOps
    from lingclaude.engine.bash import BashExecutor

    # 包装器：给 FileOps/BashExecutor 加 name/version 属性
    class FileOpsProvider:
        name = "file_ops"
        version = "0.1.0"
        def __init__(self):
            self._impl = FileOps()
        def execute(self, *args, **kwargs):
            return self._impl.read(*args, **kwargs)

    class BashExecutorProvider:
        name = "bash_executor"
        version = "0.1.0"
        def __init__(self):
            self._impl = BashExecutor()
        def execute(self, *args, **kwargs):
            return self._impl.run(*args, **kwargs)

    # fs seam
    FS_SEAM.register_provider(FileOpsProvider(), default=True)

    # shell seam
    SHELL_SEAM.register_provider(BashExecutorProvider(), default=True)

    # sandbox seam — bwrap 可用时默认 bwrap，否则 noop（fail-safe 降级）
    from lingclaude.engine.sandbox_provider import create_default_sandbox_provider
    _sandbox = create_default_sandbox_provider()

    class SandboxProviderAdapter:
        """给 SandboxProvider 加 version 属性（适配 CapabilityProvider 协议）。

        同时暴露 available/wrap（SandboxProvider 接口），使 bash 可直接消费 seam 后端。
        """
        name = _sandbox.name
        version = "0.1.0"
        def execute(self, *args: Any, **kwargs: Any) -> Any:
            return _sandbox.wrap(*args, **kwargs)
        def available(self) -> bool:
            return _sandbox.available()
        def wrap(
            self,
            command: str,
            working_dir: Any = None,
            allow_network: bool = False,
            extra_writable_dirs: list[str] | None = None,
        ) -> str:
            # 修复(2026-09-14 全量回归): 不透传 extra_writable_dirs → P2-1 默认可写目录
            # (sandbox_policy.yaml default_writable_dirs=/home/ai) 在 seam/adapter 生产路径被丢弃。
            # bash.py:684 传 extra_writable_dirs → 此处必须透传到底层 SandboxProvider.wrap。
            return _sandbox.wrap(
                command,
                working_dir=working_dir,
                allow_network=allow_network,
                extra_writable_dirs=extra_writable_dirs,
            )

    SANDBOX_SEAM.register_provider(SandboxProviderAdapter(), default=True)


# 全局注册表
_CAPABILITY_SEAMS: dict[str, CapabilitySeam] = {
    "fs": FS_SEAM,
    "shell": SHELL_SEAM,
    "llm": LLM_SEAM,
    "subagent": SUBAGENT_SEAM,
    "sandbox": SANDBOX_SEAM,
}


def get_capability_seam(name: str) -> CapabilitySeam:
    """获取能力 seam"""
    if name not in _CAPABILITY_SEAMS:
        raise ValueError(f"Capability seam not found: {name}")
    return _CAPABILITY_SEAMS[name]


def list_capability_seams() -> list[str]:
    """列出所有能力 seam"""
    return list(_CAPABILITY_SEAMS.keys())
