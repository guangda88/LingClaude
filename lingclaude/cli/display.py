from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.theme import Theme

    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False


_THEME = Theme({
    "info": "cyan",
    "warning": "yellow",
    "error": "bold red",
    "success": "bold green",
    "muted": "dim",
    "accent": "bold magenta",
    "label": "bold blue",
})


@dataclass
class SessionSummary:
    turns: int
    session_id: str
    usage: dict[str, Any]
    behavior: dict[str, float]
    stop_reason: str = ""


@dataclass
class QualityReport:
    overall: float
    safety: float
    structure: float
    behavior: float
    knowledge: float


def _get_console() -> Any:
    if _HAS_RICH:
        return Console(theme=_THEME, stderr=True)
    return None


def format_header(title: str, subtitle: str = "") -> str:
    parts = [f"[label]{title}[/label]"]
    if subtitle:
        parts.append(f"  [muted]{subtitle}[/muted]")
    return " ".join(parts)


def print_header(title: str, subtitle: str = "") -> None:
    if _HAS_RICH:
        console = _get_console()
        text = format_header(title, subtitle)
        console.print(Panel(text, style="bold blue", expand=False))
    else:
        print(f"{'=' * 10} {title} {'=' * 10}" + (f"\n  {subtitle}" if subtitle else ""))


def print_success(message: str) -> None:
    if _HAS_RICH:
        _get_console().print(f"[success]✓[/success] {message}")
    else:
        print(f"✓ {message}")


def print_error(message: str) -> None:
    if _HAS_RICH:
        _get_console().print(f"[error]✗[/error] {message}")
    else:
        print(f"✗ {message}", file=sys.stderr)


def print_warning(message: str) -> None:
    if _HAS_RICH:
        _get_console().print(f"[warning]⚠[/warning] {message}")
    else:
        print(f"⚠ {message}")


def print_info(message: str) -> None:
    if _HAS_RICH:
        _get_console().print(f"[info]ℹ[/info] {message}")
    else:
        print(f"ℹ {message}")


def print_kv(label: str, value: Any, style: str = "") -> None:
    if _HAS_RICH:
        _get_console().print(f"  [label]{label}:[/label] {value}")
    else:
        print(f"  {label}: {value}")


def format_score(score: float, width: int = 20) -> str:
    filled = int(score * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"{bar} {score:.0%}"


def print_quality_report(report: QualityReport) -> None:
    if _HAS_RICH:
        console = _get_console()
        table = Table(title="质量评分", show_header=True, header_style="bold")
        table.add_column("维度", style="label")
        table.add_column("分数", justify="right")
        table.add_column("可视化")
        dims = [
            ("总体", report.overall),
            ("安全", report.safety),
            ("结构", report.structure),
            ("行为", report.behavior),
            ("知识", report.knowledge),
        ]
        for name, val in dims:
            table.add_row(name, f"{val:.1%}", format_score(val))
        console.print(table)
    else:
        print("\n质量评分:")
        for name, val in [
            ("总体", report.overall),
            ("安全", report.safety),
            ("结构", report.structure),
            ("行为", report.behavior),
            ("知识", report.knowledge),
        ]:
            print(f"  {name}: {val:.0%} {format_score(val)}")


def print_session_summary(summary: SessionSummary) -> None:
    if _HAS_RICH:
        console = _get_console()
        console.print("\n[label]── 会话统计 ──[/label]")
        print_kv("轮次", summary.turns)
        print_kv("会话", summary.session_id)
        if summary.stop_reason:
            print_kv("结束原因", summary.stop_reason)
        for k, v in summary.usage.items():
            print_kv(k, v)
        if summary.behavior:
            console.print("[label]行为指标:[/label]")
            for k, v in summary.behavior.items():
                console.print(f"  {k}: [info]{v:.0%}[/info]")
    else:
        print("\n── 会话统计 ──")
        print(f"  轮次: {summary.turns}")
        print(f"  会话: {summary.session_id}")
        if summary.stop_reason:
            print(f"  结束原因: {summary.stop_reason}")
        for k, v in summary.usage.items():
            print(f"  {k}: {v}")
        if summary.behavior:
            print("  行为指标:")
            for k, v in summary.behavior.items():
                print(f"    {k}: {v:.0%}")


def print_welcome(version: str, provider: str, model: str, tools: int) -> None:
    if _HAS_RICH:
        console = _get_console()
        console.print(Panel(
            f"[accent]灵克[/accent] v{version} — 开源 AI 编程助手\n"
            f"[muted]自知→自觉→自决→进化[/muted]",
            style="bold blue",
            expand=False,
        ))
        console.print(f"  Provider: [info]{provider}[/info]")
        console.print(f"  Model:    [info]{model}[/info]")
        console.print(f"  Tools:    [info]{tools} registered[/info]")
    else:
        print(f"灵克 v{version} — 开源 AI 编程助手")
        print(f"  Provider: {provider}")
        print(f"  Model:    {model}")
        print(f"  Tools:    {tools} registered")


def format_tool_call(name: str, args_preview: str) -> str:
    if _HAS_RICH:
        return f"  [info][{name}][/info] [muted]{args_preview}[/muted] ... "
    return f"  [{name}] {args_preview} ... "


def format_tool_result(is_error: bool, preview: str = "") -> str:
    mark = "✗" if is_error else "✓"
    if preview and not is_error:
        return f"{mark} ({len(preview)} chars)\n"
    return f"{mark}\n"


def print_trend(name: str, direction: str, delta: float, moving_avg: float) -> None:
    arrows = {"up": "↑", "down": "↓", "flat": "→"}
    arrow = arrows.get(direction, "→")
    if _HAS_RICH:
        console = _get_console()
        console.print(f"  {name}: {arrow} avg={moving_avg:.3f} delta={delta:+.3f}")
    else:
        print(f"  {name}: {arrow} avg={moving_avg:.3f} delta={delta:+.3f}")


def print_metrics_stats(stats: dict[str, Any]) -> None:
    if _HAS_RICH:
        console = _get_console()
        console.print("[label]指标统计[/label]")
        print_kv("总数据点", stats.get("total_points", 0))
        cats = stats.get("categories", {})
        if cats:
            console.print("[label]分类:[/label]")
            for cat, count in cats.items():
                console.print(f"  {cat}: [info]{count}[/info]")
    else:
        print("指标统计")
        print(f"  总数据点: {stats.get('total_points', 0)}")
        cats = stats.get("categories", {})
        if cats:
            print("  分类:")
            for cat, count in cats.items():
                print(f"    {cat}: {count}")
