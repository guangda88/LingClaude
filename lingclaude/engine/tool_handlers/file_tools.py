"""文件工具 handler 插片 — 从 coding.py 拆分（灵元：工具是插片）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lingclaude.core.types import ToolResult


class FileToolsMixin:
    """read / write / edit / file_create / file_insert / file_delete_lines / file_undo。

    依赖 self.file_read / self.file_ops / self.file_edit / self._gate_sensitive。
    """

    # 瘦身读（2026-09-23）: 模型不传 limit 时的默认读取窗口（行）。
    # 对齐 Claude Code read 纪律；显式 limit 优先，limit=0 = 读全文。
    DEFAULT_READ_WINDOW = 200

    # P2-2: 写路径白名单 — verification.allowed_write_roots 非空时强制。
    # 空列表 = 不限制(向后兼容)。fail-closed:路径解析失败一律拒绝。
    def _write_allowed(self, path: str) -> str | None:
        config = getattr(self, "config", None)
        roots = tuple(getattr(getattr(config, "verification", None), "allowed_write_roots", ()) or ())
        if not roots:
            return None
        try:
            resolved = Path(path).resolve()
        except OSError:
            return f"路径无法解析: {path}"
        cwd = Path.cwd()
        for root in roots:
            root_path = Path(root)
            if not root_path.is_absolute():
                root_path = cwd / root_path
            try:
                if resolved.is_relative_to(root_path.resolve()):
                    return None
            except (OSError, ValueError):
                continue
        return f"路径不在 allowed_write_roots 白名单内: {resolved}（允许: {list(roots)}）"

    def _read_handler(
        self,
        path: str,
        offset: int = 0,
        limit: int | None = None,
        line_numbers: bool = True,
        **_kwargs: Any,
    ) -> ToolResult[dict[str, Any]]:
        # T0-2: sensitive_path_gate 检查（带 T0-3 审批逃生门）
        gated = self._gate_sensitive("read", path)
        if gated:
            return ToolResult.err(gated, tool_name="read")
        # 瘦身读（2026-09-23）: handler 层默认窗口——模型不传 limit 时不再
        # 全量返回（全量会让大文件撑爆上下文，再被 loop 层 1600 字符截断
        # 只剩头部）。分层契约: 库层 file_read.py limit=None=全读不变；
        # MCP 桥 server.py limit=0=不限不变；仅 TUI agent 工具面默认收窗。
        applied_limit = limit
        if limit is None:
            applied_limit = self.DEFAULT_READ_WINDOW
        elif limit == 0:
            # 0 = 不限制（对齐 MCP 桥「limit=0 表示不限制」契约）: 库层把 0
            # 解释为「读 0 行」，此处归一化为 None(全读)，保证 _read_hint
            # 给模型的逃生门指令真实有效。
            applied_limit = None
        result = self.file_read.read(
            path, offset=offset, limit=applied_limit, line_numbers=line_numbers
        )
        if result.is_error:
            return ToolResult.err(str(result.error), tool_name="read")
        payload = result.data.to_dict()
        if (
            limit is None
            and result.data.truncated
            and result.data.lines > (applied_limit or 0)
        ):
            base = result.data.offset or 0
            shown = base + min(applied_limit or 0, max(result.data.lines - base, 0))
            payload["_read_hint"] = (
                f"[read] 文件共 {result.data.lines} 行，本次显示第 "
                f"{base + 1}-{shown} 行。继续读: offset={shown}；读全文: limit=0。"
            )
        return ToolResult.ok(payload)

    def _write_handler(
        self, path: str, content: str, **_kwargs: Any
    ) -> ToolResult[dict[str, Any]]:
        denied = self._write_allowed(path)
        if denied:
            return ToolResult.err(denied, tool_name="write")
        result = self.file_ops.write(path, content)
        if result.is_error:
            return ToolResult.err(str(result.error), tool_name="write")
        return ToolResult.ok({"path": result.data})

    def _edit_handler(
        self,
        path: str,
        old_text: str,
        new_text: str,
        replace_all: bool = False,
        **_kwargs: Any,
    ) -> ToolResult[dict[str, Any]]:
        denied = self._write_allowed(path)
        if denied:
            return ToolResult.err(denied, tool_name="edit")
        result = self.file_edit.replace(path, old_text, new_text, replace_all)
        if result.is_error:
            return ToolResult.err(str(result.error), tool_name="edit")
        return ToolResult.ok(result.data.to_dict())

    def _file_create_handler(
        self, path: str, content: str, **_kwargs: Any
    ) -> ToolResult[dict[str, Any]]:
        denied = self._write_allowed(path)
        if denied:
            return ToolResult.err(denied, tool_name="file_create")
        result = self.file_edit.create(path, content)
        if result.is_error:
            return ToolResult.err(str(result.error), tool_name="file_create")
        return ToolResult.ok(result.data.to_dict())

    def _file_insert_handler(
        self, path: str, line: int, text: str, **_kwargs: Any
    ) -> ToolResult[dict[str, Any]]:
        denied = self._write_allowed(path)
        if denied:
            return ToolResult.err(denied, tool_name="file_insert")
        result = self.file_edit.insert(path, line, text)
        if result.is_error:
            return ToolResult.err(str(result.error), tool_name="file_insert")
        return ToolResult.ok(result.data.to_dict())

    def _file_delete_lines_handler(
        self, path: str, start_line: int, end_line: int, **_kwargs: Any
    ) -> ToolResult[dict[str, Any]]:
        denied = self._write_allowed(path)
        if denied:
            return ToolResult.err(denied, tool_name="file_delete_lines")
        result = self.file_edit.delete_lines(path, start_line, end_line)
        if result.is_error:
            return ToolResult.err(str(result.error), tool_name="file_delete_lines")
        return ToolResult.ok(result.data.to_dict())

    def _file_undo_handler(self, path: str, **_kwargs: Any) -> ToolResult[dict[str, Any]]:
        result = self.file_edit.undo(path)
        if result.is_error:
            return ToolResult.err(str(result.error), tool_name="file_undo")
        return ToolResult.ok({"result": result.data})
