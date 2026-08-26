"""文件工具 handler 插片 — 从 coding.py 拆分（灵元：工具是插片）。"""

from __future__ import annotations

from typing import Any


class FileToolsMixin:
    """read / write / edit / file_create / file_insert / file_delete_lines / file_undo。

    依赖 self.file_read / self.file_ops / self.file_edit / self._gate_sensitive。
    """

    def _read_handler(
        self,
        path: str,
        offset: int = 0,
        limit: int | None = None,
        line_numbers: bool = True,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        # T0-2: sensitive_path_gate 检查（带 T0-3 审批逃生门）
        gated = self._gate_sensitive("read", path)
        if gated:
            return {"error": gated}
        result = self.file_read.read(path, offset=offset, limit=limit, line_numbers=line_numbers)
        if result.is_error:
            return {"error": result.error}
        return result.data.to_dict()

    def _write_handler(
        self, path: str, content: str, **_kwargs: Any
    ) -> dict[str, Any]:
        result = self.file_ops.write(path, content)
        if result.is_error:
            return {"error": result.error}
        return {"path": result.data}

    def _edit_handler(
        self,
        path: str,
        old_text: str,
        new_text: str,
        replace_all: bool = False,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        result = self.file_edit.replace(path, old_text, new_text, replace_all)
        if result.is_error:
            return {"error": result.error}
        return result.data.to_dict()

    def _file_create_handler(
        self, path: str, content: str, **_kwargs: Any
    ) -> dict[str, Any]:
        result = self.file_edit.create(path, content)
        if result.is_error:
            return {"error": result.error}
        return result.data.to_dict()

    def _file_insert_handler(
        self, path: str, line: int, text: str, **_kwargs: Any
    ) -> dict[str, Any]:
        result = self.file_edit.insert(path, line, text)
        if result.is_error:
            return {"error": result.error}
        return result.data.to_dict()

    def _file_delete_lines_handler(
        self, path: str, start_line: int, end_line: int, **_kwargs: Any
    ) -> dict[str, Any]:
        result = self.file_edit.delete_lines(path, start_line, end_line)
        if result.is_error:
            return {"error": result.error}
        return result.data.to_dict()

    def _file_undo_handler(self, path: str, **_kwargs: Any) -> dict[str, Any]:
        result = self.file_edit.undo(path)
        if result.is_error:
            return {"error": result.error}
        return {"result": result.data}
