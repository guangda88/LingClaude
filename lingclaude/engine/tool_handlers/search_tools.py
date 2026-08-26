"""搜索工具 handler 插片 — 从 coding.py 拆分（灵元：工具是插片）。"""

from __future__ import annotations

from typing import Any


class SearchToolsMixin:
    """glob / grep 工具 handler（依赖 self.file_ops / self.grep_tool / self._gate_sensitive）。"""

    def _glob_handler(self, pattern: str, path: str | None = None, **_kwargs: Any) -> dict[str, Any]:
        # T0-2: sensitive_path_gate 检查（带 T0-3 审批逃生门）
        gated = self._gate_sensitive("glob", pattern, path)
        if gated:
            return {"error": gated}
        result = self.file_ops.glob(pattern, path=path)
        if result.is_error:
            return {"error": result.error}
        return {"files": result.data}

    def _grep_handler(
        self,
        pattern: str,
        path: str | None = None,
        include: str | None = None,
        literal: bool = False,
        case_sensitive: bool = True,
        before: int = 0,
        after: int = 0,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        # T0-2: sensitive_path_gate 检查（带 T0-3 审批逃生门）
        gated = self._gate_sensitive("grep", pattern, path, include)
        if gated:
            return {"error": gated}
        result = self.grep_tool.search(
            pattern, include=include, literal=literal, case_sensitive=case_sensitive,
            path=path, before=before, after=after,
        )
        if result.is_error:
            return {"error": result.error}
        return result.data.to_dict()
