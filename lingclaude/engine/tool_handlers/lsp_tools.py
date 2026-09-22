"""LSP 工具 handler 插片 — 从 coding.py 拆分（灵元：工具是插片）。

LspToolsMixin: lsp（依赖 self._lsp_provider / self._lsp_workspace_root + StdioLspProvider + lsp_registry）。

2026-09-17 codex P1-1 落地：生产路径改走 LspSessionPool 常驻会话池。
旧实现每次调用 ``asyncio.run(run())``：
  - 一次性事件循环随调用结束销毁 → reader task 死亡 → 第二次调用起必然 30s 超时；
  - server 子进程从不 shutdown → 每次调用泄漏一个进程；
  - 单 ``_lsp_provider`` 槽位 → 混合语言项目第二次调用复用错误 server。
新路径按 (server_cmd, workspace_root) 缓存常驻会话，didOpen/didChange
保证 server 文档视图与磁盘一致；``self._lsp_provider`` 预置注入的
legacy 路径保留（测试/宿主显式注入场景）。

2026-09-21 P2-12（crush 式 LSP 工具面）：新增 3 个结构查询命令——
  - outline：documentSymbol 文件符号大纲（替代读整文件猜结构）
  - search：workspace/symbol 按名跨文件搜符号（替代 grep 找定义）
  - diagnostics：publishDiagnostics 文件诊断（替代编译/试错看错）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lingclaude.core.types import ToolResult
from lingclaude.engine.lsp_provider import StdioLspProvider

# P2-12：支持的 LSP 命令白名单（导航 4 + 结构查询 3）
_LSP_COMMANDS = (
    "goto_def", "find_refs", "hover", "goto_impl",
    "outline", "search", "diagnostics",
)


class LspToolsMixin:
    """lsp 工具 handler（P1-1: LSP dispatcher + P2-12 结构查询）。"""

    def _lsp_handler(
        self,
        command: str,
        file_path: str,
        line: int = 0,
        character: int = 0,
        **_: Any,
    ) -> ToolResult[dict[str, Any]]:
        """P1-1: LSP dispatcher — 常驻池路由；provider 注入时走 legacy 路径。

        P2-12: 新增 outline/search/diagnostics 命令；search 用 query 参数
        （workspace/symbol 全仓查询），其余命令 query 忽略。
        """
        cmd = command.lower()
        if cmd not in _LSP_COMMANDS:
            return ToolResult.err(
                f"unknown LSP command: {command}",
                tool_name="lsp",
            )
        query = str(_.get("query", "") or "")

        # legacy 注入路径：宿主显式预置 provider（测试/特殊宿主）时沿用原语义
        if self._lsp_provider is not None:
            return _lsp_run_injected(
                self._lsp_provider, self._lsp_workspace_root,
                cmd, file_path, line, character, query,
            )

        # 生产路径：常驻会话池
        from lingclaude.engine.lsp_registry import detect_lang, get_server
        from lingclaude.engine.lsp_session import get_pool

        lang = detect_lang(file_path)
        if lang is None:
            if cmd == "search":
                # workspace/symbol 全仓查询：无 file_path 时回退 python 默认 server
                lang = "python"
            else:
                return ToolResult.err(
                    f"unsupported language for file: {file_path}",
                    tool_name="lsp",
                )
        server_cfg = get_server(lang)
        server_command = server_cfg["command"] if server_cfg else "pylsp"
        args = server_cfg.get("args", []) if server_cfg else []
        workspace_root = self._lsp_workspace_root or _detect_workspace_root(file_path)
        self._lsp_workspace_root = workspace_root

        try:
            result = get_pool().run_with_session(
                [server_command, *args],
                workspace_root,
                lambda session: _sync_then_dispatch(
                    session, cmd, file_path, line, character, query
                ),
            )
            return ToolResult.ok(
                {
                    "ok": True,
                    "command": cmd,
                    "result": _format_locations(result),
                }
            )
        except Exception as e:  # noqa: BLE001 — 统一结构化错误（含 server 未安装）
            return ToolResult.err(
                f"lsp error: {e}",
                tool_name="lsp",
            )

    # ----- legacy 注入路径改为模块级函数（见文件尾 _lsp_run_injected）-----


# ----- helpers -----


def _lsp_run_injected(
    provider,
    workspace_root,
    cmd: str,
    file_path: str,
    line: int,
    character: int,
    query: str = "",
):
    """provider 由宿主预置时的旧路径（模块级：宿主测试类只绑定单方法）。
    保留原因：test_lsp_tools.py 通过 _RT 预置 _FakeProvider 验证 mixin
    契约；一次性循环的三个缺陷对注入场景不适用（生命周期由宿主管理）。"""
    import asyncio

    async def run():
        if not getattr(provider, "_initialized", False):
            await provider.initialize(workspace_root or Path.cwd())
            provider._initialized = True
        return await _dispatch(provider, cmd, file_path, line, character, query)

    try:
        result = asyncio.run(run())
        return ToolResult.ok(
            {
                "ok": True,
                "command": cmd,
                "result": _format_locations(result),
            }
        )
    except Exception as e:  # noqa: BLE001
        return ToolResult.err(
            f"lsp error: {e}",
            tool_name="lsp",
        )


async def _sync_then_dispatch(session, cmd: str, file_path: str, line: int,
                              character: int, query: str = ""):
    """didOpen 同步文档视图后执行查询（await 确保严格序，不依赖 and 短路）。

    P2-12：outline/diagnostics 依赖单文件 didOpen；search（workspace/symbol）
    是全仓查询不依赖 didOpen，跳过同步省一次全量文本写入。
    """
    if cmd in ("outline", "diagnostics") and file_path:
        await session.ensure_open(file_path)
    return await _dispatch(session.provider, cmd, file_path, line, character, query)


async def _dispatch(provider, cmd: str, file_path: str, line: int, character: int,
                    query: str = ""):
    if cmd == "goto_def":
        return await provider.go_to_definition(file_path, line, character)
    if cmd == "find_refs":
        return await provider.find_references(file_path, line, character)
    if cmd == "hover":
        return await provider.hover(file_path, line, character)
    if cmd == "goto_impl":
        return await provider.go_to_implementation(file_path, line, character)
    # P2-12：crush 式结构查询（省 token）
    if cmd == "outline":
        return await provider.document_symbols(file_path)
    if cmd == "search":
        return await provider.workspace_symbols(query)
    if cmd == "diagnostics":
        return await provider.diagnostics(file_path)
    raise ValueError(f"unknown LSP command: {cmd}")


# A组清偿③（2026-09-22）：_format_locations/_detect_workspace_root 为无状态纯函数，
# 拆出 lsp_helpers.py（本文件 216→170 行回 200 行契约内），回 import 保持消费面。
from lingclaude.engine.tool_handlers.lsp_helpers import (  # noqa: E402,F401
    _detect_workspace_root,
    _format_locations,
)
