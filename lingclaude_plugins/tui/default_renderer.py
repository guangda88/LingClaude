"""TUI 默认渲染 provider — 迁移 cli/display.py 的 Rich+Markdown 渲染能力。

CapabilityProvider 适配器：name/version/execute（execute 路由到渲染方法）。
"""

from __future__ import annotations

from typing import Any

from lingclaude.cli.display import (
    format_tool_call,
    format_tool_result,
    print_diff,
    print_error,
    print_header,
    print_info,
    print_kv,
    print_markdown,
    print_metrics_stats,
    print_quality_report,
    print_session_summary,
    print_success,
    print_trend,
    print_warning,
    print_welcome,
)


class DefaultRendererProvider:
    """TUI 渲染 provider（CapabilityProvider 协议：name/version/execute）。"""

    name = "default_renderer"
    version = "0.1.0"

    def __init__(self) -> None:
        # 渲染能力映射（method -> 实现）
        self._capabilities = {
            "markdown": print_markdown,
            "header": print_header,
            "success": print_success,
            "error": print_error,
            "warning": print_warning,
            "info": print_info,
            "welcome": print_welcome,
            "tool_call": format_tool_call,
            "tool_result": format_tool_result,
            "diff": print_diff,
            "session_summary": print_session_summary,
            "quality_report": print_quality_report,
            "kv": print_kv,
            "trend": print_trend,
            "metrics_stats": print_metrics_stats,
        }

    def execute(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """按 method 分发到渲染函数。未知 method 抛 KeyError（fail fast）。"""
        if method not in self._capabilities:
            raise KeyError(
                f"TUI renderer 无此能力: {method}（可用: {sorted(self._capabilities)}）"
            )
        return self._capabilities[method](*args, **kwargs)

    def has(self, method: str) -> bool:
        return method in self._capabilities

    def capabilities(self) -> list[str]:
        return sorted(self._capabilities)
