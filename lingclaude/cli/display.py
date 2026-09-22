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


def _plain_no_color() -> bool:
    """plain 模式禁色判定（2026-09-22 终裁：默认纯文本，彩色 opt-in）。

    背景（2026-09-21 乱码战役收尾）：plain 模式 rich 经 stderr 直通渲染
    「彩色正式版」，前提假设是终端真实消费 SGR。实测启动终端声明
    TERM=xterm-256color 却把 ESC 显示为字面 '?'（声明能力≠真实能力）
    → 彩色直通 = 乱码。且环境变量从外层 shell 向主进程传导不可靠
    （会话内 export 只影响子进程），故语义反转为：

    - 默认禁色（返回 True）：color_system=None 物理归零，源头零 ESC
    - LINGCLAUDE_COLOR=1/true/yes/on 显式 opt-in 彩色（优先级最高）
    - 标准 NO_COLOR 仍然尊重
    - LINGCLAUDE_PLAIN_NO_COLOR 已废弃为无害死开关（默认恒禁色，
      保留仅为旧测试兼容，不再单独判定）
    """
    import os

    # 2026-09-22 终裁：默认纯文本，彩色改为显式 opt-in（LINGCLAUDE_COLOR=1）。
    # 理由：终端「声明 TERM=xterm-256color 却把 ESC 显示为字面 '?'」的环境
    # 实测长期存在（声明能力≠真实能力），且环境变量从外层 shell 向
    # lingclaude 主进程传导不可靠（会话内 export 只影响子进程）。
    # opt-in 彩色：LINGCLAUDE_COLOR=1/true/yes/on
    if os.environ.get("LINGCLAUDE_COLOR", "").strip().lower() in {
        "1", "true", "yes", "on",
    }:
        return False
    # 标准 NO_COLOR 仍然尊重
    if os.environ.get("NO_COLOR", "") != "":
        return True
    return True


def _get_console() -> Any:
    if _HAS_RICH:
        # P2 单一输出 owner（2026-09-20，atomcode 借鉴）：Console(stderr=True)
        # 是乱码第四路径的根——stderr 直达真实终端，绕开 stdout 侧全部清洗
        # （proxy/append_output/回放/stream_write）。现全屏 TUI 托管期改走
        # sys.stdout（此时已被 FullTuiSession 的 _StdoutProxy 接管，渲染片段
        # 进输出窗 → 统一经 _strip_ansi_text 清洗）；plain 模式 stdout 是真实
        # 终端（isatty），维持 stderr 直通语义（彩色正式版是设计意图），
        # 但支持 NO_COLOR / LINGCLAUDE_PLAIN_NO_COLOR 强制纯文本降级
        # （2026-09-21：终端声明彩色却不消费 SGR 的环境实测乱码）。
        import sys

        from lingclaude.cli.repl_io import is_full_tui_managed

        if is_full_tui_managed():
            return Console(theme=_THEME, file=sys.stdout, force_terminal=False)
        if _plain_no_color():
            # 2026-09-22 终裁补充：no_color=True 只禁颜色 SGR，粗体/下划线
            # 仍会出码（实测 ESC[1m/[4m 残留）。color_system=None 才是物理
            # 归零：rich 判定终端零能力，从源头不生成任何 ESC 序列。
            return Console(
                theme=_THEME,
                stderr=True,
                force_terminal=False,
                no_color=True,
                color_system=None,
            )
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


def print_diff(diff_text: str, language: str = "diff") -> None:
    """T1-7: diff 高亮 — 绿色新增/红色删除。Rich Syntax 优先，纯文本兜底。"""
    if not diff_text:
        return
    if _HAS_RICH:
        try:
            from rich.syntax import Syntax

            console = _get_console()
            console.print(Syntax(diff_text, language, theme="monokai", line_numbers=False))
            return
        except Exception:  # noqa: BLE001 — Rich 语法高亮失败降级纯文本
            pass
    # 纯文本兜底：逐行前缀着色（unified diff 风格）
    for line in diff_text.splitlines():
        if line.startswith("+"):
            print(f"\033[32m{line}\033[0m")
        elif line.startswith("-"):
            print(f"\033[31m{line}\033[0m")
        elif line.startswith("@@"):
            print(f"\033[36m{line}\033[0m")
        else:
            print(line)


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


# ── Step 5: Markdown / ToolCallPanel / StatusBar（RFC §3.2）──────────────────


def print_markdown(text: str) -> None:
    """Step 5: rich.markdown 渲染模型输出（含代码块/列表/链接）。纯文本兜底。"""
    if not text:
        return
    if _HAS_RICH:
        try:
            from rich.markdown import Markdown

            console = _get_console()
            console.print(Markdown(text))
            return
        except Exception:  # noqa: BLE001 — Markdown 渲染失败降级纯文本
            pass
    print(text)


class ToolCallPanel:
    """Step 5: 工具调用/结果流式面板（Rich Live）。

    用法：panel = ToolCallPanel(); panel.start(); ...; panel.stop()。
    """

    def __init__(self, console: Any | None = None) -> None:
        self._console = console or (_get_console() if _HAS_RICH else None)
        self._live: Any = None
        self._lines: list[str] = []

    def start(self) -> None:
        if self._console is not None:
            try:
                from rich.live import Live
                from rich.panel import Panel

                self._live = Live(
                    Panel("", title="工具调用", border_style="cyan"),
                    console=self._console,
                    refresh_per_second=10,
                    transient=True,
                )
                self._live.start()
            except Exception:  # noqa: BLE001 — Live 不可用时降级为逐行打印
                self._live = None

    def add_tool_start(self, name: str, args_preview: str = "") -> None:
        self._lines.append(f"[info]▶ {name}[/info] {args_preview}")
        self._render()

    def add_tool_end(self, is_error: bool, preview: str = "") -> None:
        mark = "❌" if is_error else "✅"
        style = "error" if is_error else "success"
        # 终端宽度动态截断（替代固定 80）——窄终端不低于 60，
        # 宽终端用 columns-8 留余白；非 TTY 回落 80
        try:
            import shutil as _sh, sys as _sys
            cols = max(60, (_sh.get_terminal_size().columns or 80) - 8) if _sys.stdout.isatty() else 80
        except Exception:  # noqa: BLE001
            cols = 80
        self._lines.append(f"[{style}]{mark} {preview[:cols]}[/{style}]")
        self._render()

    def _render(self) -> None:
        if self._live is not None:
            from rich.panel import Panel

            self._live.update(Panel("\n".join(self._lines), title="工具调用", border_style="cyan"))
        elif self._console is not None:
            self._console.print(self._lines[-1] if self._lines else "")

    def stop(self) -> None:
        if self._live is not None:
            try:
                self._live.stop()
            except Exception:  # noqa: BLE001
                pass
            self._live = None


class StatusBar:
    """Step 5: 底部状态栏 — 模型 / token 用量 / 当前模式（RFC §3.2）。"""

    def __init__(self, console: Any | None = None) -> None:
        self._console = console or (_get_console() if _HAS_RICH else None)

    def render(self, model: str, tokens: int = 0, mode: str = "") -> str:
        """构造状态栏文本（不自动打印，由调用方决定刷新策略）。"""
        parts = [f"[accent]{model}[/accent]"]
        if tokens:
            parts.append(f"[muted]{tokens} tokens[/muted]")
        if mode:
            parts.append(f"[label]{mode}[/label]")
        return " | ".join(parts)

    def print(self, model: str, tokens: int = 0, mode: str = "") -> None:
        text = self.render(model, tokens, mode)
        if self._console is not None:
            try:
                from rich.panel import Panel

                self._console.print(Panel(text, title="状态", border_style="dim"))
                return
            except Exception:  # noqa: BLE001 — 降级纯文本
                pass
        print(text)
