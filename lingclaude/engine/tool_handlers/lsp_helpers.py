"""LSP 纯函数助手（A组清偿③ lsp_tools 拆分，2026-09-22）。

从 lsp_tools.py（216 行超插片 200 行契约）拆出的无状态助手：
- _format_locations：LSP 结果统一 dict 结构
- _detect_workspace_root：workspace 根探测（pyproject/Cargo/.git）

无状态纯函数，与宿主 LspToolsMixin 无共享状态，独立成文件便于复用与单测。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _format_locations(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, list):
        out: list[dict[str, Any]] = []
        for loc in result:
            if isinstance(loc, dict):
                # P2-12：outline/search/diagnostics 已在 provider 侧解析为 dict，直传
                out.append(loc)
            else:
                out.append(
                    {"uri": loc.uri, "line": loc.range.start.line,
                     "col": loc.range.start.character}
                )
        return out
    # hover: 返回 Hover / None — 统一成 list 结构便于调用方判空
    if result is None:
        return []
    contents = getattr(result, "contents", None)
    if contents is not None:
        return [{"contents": contents, "range": getattr(result, "range", None)}]
    return []


def _detect_workspace_root(file_path: str) -> Path:
    """Auto-detect workspace root: find pyproject.toml / Cargo.toml / .git。"""
    if file_path:
        cwd = Path(file_path).resolve().parent
    else:
        cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
        if (parent / "Cargo.toml").exists():
            return parent
        if (parent / ".git").exists():
            return parent
    return cwd
