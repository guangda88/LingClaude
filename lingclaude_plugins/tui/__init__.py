"""lingclaude_plugins.tui — TUI 渲染插片骨架（Step A）。

按 proposals/2026-09-09_TUI_PLUGIN_SEAM_PROPOSAL.md Step A：
- default_renderer.py: display.py 的 Rich+Markdown 迁移（provider 适配器）
- bottom_toolbar.py: status.py 的 StatusModel+toolbar_fragments 迁移
- simple_prompt.py: interface.py 的 FallbackSession 迁移
- __init__.py: install() → TUI_SEAM.register_provider(..., required_signers=[])

设计纪律：
- 免签注册（required_signers=[]）：纯展示无副作用，不适用能力执行的双签安全模型
- provider 结构：name/version/execute（CapabilityProvider 协议）
- 原 cli/status.py / display.py / interface.py 保持不动（re-export 兼容层）
"""

from lingclaude.lacp.capability_seam import CapabilitySeam

# TUI 能力 seam（注册表条目）
TUI_SEAM = CapabilitySeam("tui", {
    "methods": ["render_markdown", "toolbar", "prompt"],
    "transport": "local",
})

__all__ = ["TUI_SEAM", "install"]


def install() -> bool:
    """把 TUI 渲染 provider 注册进 seam（免签）。

    返回注册是否成功（失败=provider 未注册，CLI 继续用原 fallback，不崩溃）。
    """
    try:
        from lingclaude_plugins.tui.default_renderer import (
            DefaultRendererProvider,
        )
        from lingclaude_plugins.tui.bottom_toolbar import (
            ToolbarProvider,
        )
        from lingclaude_plugins.tui.simple_prompt import (
            SimplePromptProvider,
        )

        TUI_SEAM.register_provider(DefaultRendererProvider(), default=True, required_signers=[])
        TUI_SEAM.register_provider(ToolbarProvider(), required_signers=[])
        TUI_SEAM.register_provider(SimplePromptProvider(), required_signers=[])
        return True
    except Exception:  # noqa: BLE001 — 插片加载失败不崩 CLI
        return False
