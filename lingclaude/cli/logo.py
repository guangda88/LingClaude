"""灵克启动 Logo（2026-09-17）。

电路图腾字符画（灵克/LINGCLAUDE 行，不带版本号+模式行——下方横幅已承载）。
ANSI 色仅 isatty 时启用（CI/管道降级纯色）。
接入点：repl._interactive_loop 两处横幅（plain + 全屏 TUI 输出窗）。
"""
from __future__ import annotations

import sys

_LINES = [
    "     ┌─┬──┐",
    "     │ │  │",
    "     ├─┼──┤",
    "     │ │  │  灵 克",
    "     ├─┼──┤  LINGCLAUDE",
    "     │ │  │",
    "     └─┴──┘",
    "     ─────────────",
]


def render(version: str, mode: str, *, color: bool = True) -> str:
    """渲染 Logo：电路图腾三行字符画（version/mode 参数保留仅为调用签名兼容，不渲染）。"""
    if color and sys.stdout.isatty():
        cyan = "\033[36m"
        reset = "\033[0m"
        return "\n".join(f"{cyan}{l}{reset}" for l in _LINES) + "\n"
    return "\n".join(_LINES) + "\n"


def render_plain(version: str, mode: str = "交互模式") -> str:
    """无 ANSI 色（管道/CI/日志），供 output-format=json 等降级路径。"""
    return render(version, mode, color=False)
