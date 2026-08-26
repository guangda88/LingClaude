# T3-1: Capability Seam 局部引入 RFC

**日期**: 2026-08-25
**作者**: 灵克 (lingclaude)
**状态**: ✅ 已实施（2026-08-26 更新：5 个 seam 落地 + sandbox_seam 新增）
**前置**: P1-1 LSP ✅ + P1-6 code intel ✅（已满足）

---

## 实施记录（2026-08-26）

RFC 已按设计落地于 `lingclaude/lacp/capability_seam.py`，并超出原设计补充：

| Seam | 能力 | 默认 Provider | 落地 |
|---|---|---|---|
| FS_SEAM | read/write/edit/glob/grep | FileOpsProvider | ✅ |
| SHELL_SEAM | execute/sandbox | BashExecutorProvider | ✅ |
| LLM_SEAM | complete/stream | （待注册） | ✅ |
| SUBAGENT_SEAM | spawn/abort/status | （待注册） | ✅ |
| **SANDBOX_SEAM**（新增） | wrap/available | Bwrap/Noop | ✅ 2026-08-26 |

超出原设计的落地：
- **双签**：`SignedProvider`（required_signers + sign + is_approved）——治理层对齐
- **5 个 seam**：原设计 4 个（fs/shell/llm/subagent），新增 SANDBOX_SEAM
- **sandbox 后端插片化**：`engine/sandbox_provider.py`（Protocol + Bwrap/Noop），bash.py 通过 `set_sandbox_provider` 解耦
- **消费方**：webui_seam.py 使用 capability_seam 注册

---

## 目标

engine/tools 层引入 capability seam：每个能力（fs/shell/llm/subagent）为独立 Service Definition + Provider 注册表，插件可替换实现。

## 现状

已有设施：
- `webui_seam.py`: webui seam 注册（register_service + declare_consumer）
- `sandbox_policy.py`: 4 档沙箱策略（permissive/restricted/strict/paranoid）
- `manifest.py`: LACP 插件契约（Plugin + Interface + Transport）
- `tools.py`: ToolDefinition + ToolRegistry

缺口：
- fs/shell/llm/subagent 无独立 Service Definition + Provider 注册表
- 插件无法替换这些能力的实现

## 设计

### 1. Capability Seam 定义

```python
# lingclaude/lacp/capability_seam.py

from typing import Any, Protocol

class CapabilityProvider(Protocol):
    """能力提供者协议"""
    name: str
    version: str
    
    def execute(self, *args, **kwargs) -> Any: ...


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
```

### 2. 现有 Provider 注册

```python
# lingclaude/lacp/capability_seam.py (续)

def register_default_providers() -> None:
    """注册默认能力提供者"""
    from lingclaude.engine.file_ops import FileReadTool, FileWriteTool
    from lingclaude.engine.bash import BashExecutor
    from lingclaude.engine.sub_agent import SubAgent
    
    # fs seam
    FS_SEAM.register_provider(FileReadTool(), default=True)
    FS_SEAM.register_provider(FileWriteTool())
    
    # shell seam
    SHELL_SEAM.register_provider(BashExecutor(), default=True)
    
    # subagent seam
    SUBAGENT_SEAM.register_provider(SubAgent(), default=True)
```

### 3. 与现有 seam_registry 集成

```python
# lingclaude/webui_seam.py (扩展)

from lingclaude.lacp.capability_seam import (
    FS_SEAM, SHELL_SEAM, LLM_SEAM, SUBAGENT_SEAM,
    register_default_providers,
)

def register_capability_seams() -> None:
    """注册所有 capability seam 到 SeamRegistry"""
    from lingflow.coordination.seam_registry import get_seam_registry
    
    registry = get_seam_registry()
    
    # 注册能力 seam
    registry.register_service("fs", FS_SEAM.interface, provider="lingclaude")
    registry.register_service("shell", SHELL_SEAM.interface, provider="lingclaude")
    registry.register_service("llm", LLM_SEAM.interface, provider="lingclaude")
    registry.register_service("subagent", SUBAGENT_SEAM.interface, provider="lingclaude")
    
    # 注册默认提供者
    register_default_providers()
```

## 约束

- monorepo 已成型，不可强行拆包
- 局部引入：只 fs/shell/llm/subagent 4 个能力，不全面插件化
- 保持现有 ToolDefinition/ToolRegistry 兼容（capability seam 是补充，不是替换）

## 验收标准

1. `FS_SEAM.get_provider()` 返回默认 FileReadTool
2. `FS_SEAM.replace_provider("custom", CustomFSProvider())` 热替换成功
3. `webui_seam.register_capability_seams()` 注册 4 个 seam 到 SeamRegistry
4. 测试：`tests/test_capability_seam.py` 覆盖注册/替换/获取

## 工作量

- `lingclaude/lacp/capability_seam.py`: ~80 行（新建）
- `lingclaude/webui_seam.py`: ~20 行（扩展）
- `tests/test_capability_seam.py`: ~50 行（新建）
- **总计**: ~150 行，1-2 人天

## 风险

- 与现有 ToolRegistry 的边界不清晰 → 需明确分工（ToolRegistry 管工具注册，capability seam 管能力替换）
- 插件市场（marketplace.py）未消费 capability seam → 后续接入

---

**决策**：是否按此 RFC 实施？或需调整范围？
