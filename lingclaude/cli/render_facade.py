"""TUI 渲染门面 — 插片优先 + cli.display 回退（Step B）。

按 proposals/2026-09-09_TUI_PLUGIN_SEAM_PROPOSAL.md Step B：
- 渲染函数调用点不再直接 import cli.display，而是经本门面路由。
- 优先用 tui 能力 seam 的 default_renderer provider（免签，纯展示）。
- tui 插件未安装/不可用 → 回退 cli.display 内置实现（双路径不炸）。

设计纪律：
- 本门面是"调用点缝化"的唯一落点；数据类（SessionSummary/QualityReport）
  与 StatusModel/PromptSessionInterface 属类型契约，保持 cli 直引不缝。
- 递归陷阱：default_renderer 的 capabilities 值本身就是 cli.display 的函数
  （display.print_markdown 等）。本门面回退也调 cli.display —— 但 provider
  的 execute 直接绑定 display 函数，不经本门面，故无递归。
"""

from __future__ import annotations

from typing import Any, Callable

from lingclaude.cli import display as _display

# 渲染能力名 → cli.display 实现（fallback）
_FALLBACKS: dict[str, Callable[..., Any]] = {
    "markdown": _display.print_markdown,
    "header": _display.print_header,
    "success": _display.print_success,
    "error": _display.print_error,
    "warning": _display.print_warning,
    "info": _display.print_info,
    "welcome": _display.print_welcome,
    "tool_call": _display.format_tool_call,
    "tool_result": _display.format_tool_result,
    "diff": _display.print_diff,
    "session_summary": _display.print_session_summary,
    "quality_report": _display.print_quality_report,
    "kv": _display.print_kv,
    "trend": _display.print_trend,
    "metrics_stats": _display.print_metrics_stats,
}


def _get_renderer() -> Any | None:
    """获取 tui seam 的 default_renderer provider；不可用返回 None。"""
    try:
        from lingclaude_plugins.tui import TUI_SEAM

        provider = TUI_SEAM.get_provider("default_renderer")
        # 未审批（双签未完成）→ 回退，不执行
        if not getattr(provider, "is_approved", True):
            return None
        # SignedProvider 解包 → 内部 provider（有 has/execute）
        if hasattr(provider, "_wrapped"):
            provider = provider._wrapped
        return provider
    except Exception:  # noqa: BLE001 — 插件缺失/损坏 → 回退
        return None


def render(method: str, *args: Any, **kwargs: Any) -> Any:
    """渲染调用点统一入口：tui provider 优先，cli.display 回退。"""
    provider = _get_renderer()
    if provider is not None and provider.has(method):
        try:
            return provider.execute(method, *args, **kwargs)
        except Exception:  # noqa: BLE001 — provider 执行失败 → 回退
            pass
    fallback = _FALLBACKS.get(method)
    if fallback is None:
        raise KeyError(f"渲染能力不存在: {method}")
    return fallback(*args, **kwargs)


def has(method: str) -> bool:
    """能力是否存在（provider 或 fallback）。"""
    provider = _get_renderer()
    if provider is not None and provider.has(method):
        return True
    return method in _FALLBACKS


# ── 同名薄代理：调用点零改动，仅换 import 源（Step B） ──────────────
# 每个代理转发到 render(method, ...)，provider 优先、cli.display 回退。
# 注意：代理与 _FALLBACKS 里的 cli.display 函数是不同对象（模块属性访问），
# 无递归 —— provider 绑定的是 cli.display 函数，代理绑定的是本模块函数。

def print_markdown(text: str) -> None:
    render("markdown", text)


def print_header(title: str, subtitle: str = "") -> None:
    render("header", title, subtitle)


def print_success(message: str) -> None:
    render("success", message)


def print_error(message: str) -> None:
    render("error", message)


def print_warning(message: str) -> None:
    render("warning", message)


def print_info(message: str) -> None:
    render("info", message)


def print_welcome(version: str, provider: str, model: str, tools: int) -> None:
    render("welcome", version, provider, model, tools)


def format_tool_call(name: str, args_preview: str) -> str:
    return render("tool_call", name, args_preview)


def format_tool_result(is_error: bool, preview: str = "") -> str:
    return render("tool_result", is_error, preview)


def print_diff(diff_text: str, language: str = "diff") -> None:
    render("diff", diff_text, language)


def print_session_summary(summary: Any) -> None:
    render("session_summary", summary)


def print_quality_report(report: Any) -> None:
    render("quality_report", report)


def print_kv(label: str, value: Any, style: str = "") -> None:
    render("kv", label, value, style)


def print_trend(name: str, direction: str, delta: float, moving_avg: float) -> None:
    render("trend", name, direction, delta, moving_avg)


def print_metrics_stats(stats: dict[str, Any]) -> None:
    render("metrics_stats", stats)


__all__ = [
    "render",
    "has",
    "print_markdown",
    "print_header",
    "print_success",
    "print_error",
    "print_warning",
    "print_info",
    "print_welcome",
    "format_tool_call",
    "format_tool_result",
    "print_diff",
    "print_session_summary",
    "print_quality_report",
    "print_kv",
    "print_trend",
    "print_metrics_stats",
]
