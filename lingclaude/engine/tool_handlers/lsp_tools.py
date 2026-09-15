"""LSP 工具 handler 插片 — 从 coding.py 拆分（灵元：工具是插片）。

LspToolsMixin: lsp（依赖 self._lsp_provider / self._lsp_workspace_root + StdioLspProvider + lsp_registry）。
"""

from __future__ import annotations

from typing import Any

from lingclaude.core.types import ToolResult
from lingclaude.engine.lsp_provider import StdioLspProvider


class LspToolsMixin:
    """lsp 工具 handler（P1-1: LSP dispatcher）。"""

    def _lsp_handler(
        self,
        command: str,
        file_path: str,
        line: int = 0,
        character: int = 0,
        **_: Any,
    ) -> ToolResult[dict[str, Any]]:
        """P1-1: LSP dispatcher — routes to StdioLspProvider on first use."""
        import asyncio
        from pathlib import Path

        cmd = command.lower()
        if cmd not in ("goto_def", "find_refs", "hover", "goto_impl"):
            return ToolResult.err(
                f"unknown LSP command: {command}",
                tool_name="lsp",
            )

        # Lazy-init LSP provider
        if self._lsp_provider is None:
            # lsp add: 从注册表读取语言 → server 命令（用户配置优先，内置回退）
            from lingclaude.engine.lsp_registry import detect_lang, get_server

            lang = detect_lang(file_path)
            server_cfg = get_server(lang) if lang else None
            # Auto-detect workspace root: find .git / pyproject.toml / Cargo.toml
            cwd = Path(file_path).resolve().parent
            for parent in [cwd, *cwd.parents]:
                if (parent / "pyproject.toml").exists():
                    self._lsp_workspace_root = parent
                    break
                if (parent / "Cargo.toml").exists():
                    self._lsp_workspace_root = parent
                    break
            else:
                self._lsp_workspace_root = cwd
            server_command = server_cfg["command"] if server_cfg else "pylsp"
            args = server_cfg.get("args", []) if server_cfg else []
            server = StdioLspProvider([server_command, *args], workspace_root=self._lsp_workspace_root)
            self._lsp_provider = server

        provider = self._lsp_provider

        async def run():
            if not getattr(provider, "_initialized", False):
                await provider.initialize(self._lsp_workspace_root)
                provider._initialized = True
            if cmd == "goto_def":
                return await provider.go_to_definition(file_path, line, character)
            if cmd == "find_refs":
                return await provider.find_references(file_path, line, character)
            if cmd == "hover":
                return await provider.hover(file_path, line, character)
            return await provider.go_to_implementation(file_path, line, character)

        try:
            result = asyncio.run(run())
            return ToolResult.ok(
                {
                    "ok": True,
                    "command": cmd,
                    "result": [
                        {"uri": loc.uri, "line": loc.range.start.line, "col": loc.range.start.character}
                        for loc in (result if isinstance(result, list) else [])
                    ],
                }
            )
        except Exception as e:
            return ToolResult.err(
                f"lsp error: {e}",
                tool_name="lsp",
            )
