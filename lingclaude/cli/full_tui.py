"""P2 全屏 TUI 会话（Q4 落地，2026-09-15）。

设计文档: docs/cli/TUI_BOTTOM_INPUT_DESIGN.md §二 HSplit 全屏布局。

形态（务实落地，零侵入 P0/P1 逃生逻辑）:
- 每次 prompt() 进入全屏 Application（HSplit 三区：可滚动输出窗 + 状态栏 + 输入框）。
- 输入框提交 → app 退出全屏 → 返回文本；生成期走现有 stdout 流式（P1 形态）。
- 下一轮 prompt() 重新进入全屏，输出窗重绘累积会话历史（output_source 回调注入）。
- 状态栏经 install_bottom_toolbar 注册回调，全屏期间常驻显示 —— 修复 P1 的
  "toolbar 仅 prompt 渲染期可见"债（设计文档 §十二.2 偏差表）。

安全约束（P0/P1 事故防复发）:
- 生成期不在全屏内：Esc 让位输入框编辑，中断由 Ctrl+C 承担（终端信号 → 主线程
  生成循环 except KeyboardInterrupt，与现有语义一致）。
- 不启用 InputPump / _esc_listen_loop 双读者（调用方 repl.py 负责禁用）。
- 输出窗行数上限 800（设计文档 §十一.3 建议 1000，留余量），超出丢弃最旧行。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

# prompt_toolkit 为可选依赖 — 未安装时构造抛 RuntimeError（create_session 捕获回退）
try:
    from prompt_toolkit.application import Application
    from prompt_toolkit.application.current import get_app
    from prompt_toolkit.history import FileHistory, InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.widgets import TextArea

    _HAS_PROMPT_TOOLKIT = True
except ImportError:  # pragma: no cover
    _HAS_PROMPT_TOOLKIT = False

# 与 interface.py 对齐的历史文件（项目内隔离，2026-09-15 P1-2）
DEFAULT_HISTORY_FILE = ".lingclaude/history"

# 输出窗行数上限（超出丢最旧行）
MAX_OUTPUT_LINES = 800


class FullTuiSession:
    """P2 全屏 TUI 会话（PromptSessionInterface 协议实现）。

    架构：prompt() 同步进入全屏 Application；输入框 accept → app.exit(result) →
    run() 返回文本。生成期由主线程流式写 stdout（P1 形态不变）。
    """

    def __init__(
        self,
        history_file: str = DEFAULT_HISTORY_FILE,
        completer: Any | None = None,
        output_source: Callable[[], list[str]] | None = None,
    ) -> None:
        if not _HAS_PROMPT_TOOLKIT:
            raise RuntimeError("prompt_toolkit 未安装，请使用 FallbackSession")
        history_path = Path(history_file).expanduser()
        try:
            history_path.parent.mkdir(parents=True, exist_ok=True)
            self._history = FileHistory(str(history_path))
        except OSError:
            self._history = InMemoryHistory()
        self._completer = completer
        self._output_source = output_source or (lambda: [])
        self._status_cb: Callable[[], list[tuple[str, str]]] | None = None
        self._interrupt = threading.Event()

        # 控件（复用；每次 prompt() 新建 Application）
        self._output_area = TextArea(
            text="",
            read_only=True,
            wrap_lines=True,
            scrollbar=True,
            focusable=False,
            style="class:output",
        )
        self._input_area = TextArea(
            text="",
            # 2026-09-16（长文截断修复）:multiline=True — 单行模式粘贴长文/多行
            # 文本时 prompt_toolkit 只保留第一行、其余被当 Enter 提交丢弃。多行
            # 模式下 Enter 重绑为提交（_kb 里 "enter" 键绑定调 _on_accept），
            # Esc+Enter 换行，保持「敲 Enter 提交」习惯不变。
            multiline=True,
            completer=self._completer,
            history=self._history,
            accept_handler=self._on_accept,
            style="class:input",
        )
        self._status_win = Window(
            height=1,
            content=FormattedTextControl(self._status_fragments),
            style="class:status",
        )

        # 键绑定：Ctrl+C 清行/中断，Ctrl+D 空行退出，Esc 让位输入框编辑
        self._kb = KeyBindings()

        # 2026-09-16（长文截断修复）:multiline=True 后 Enter 默认换行、不提交。
        # 重绑 Enter 为提交（复用 _on_accept），Esc+Enter 换行 —— 保持 CLI
        # 「敲 Enter 提交」习惯，同时支持多行输入不截断。
        @self._kb.add("enter")
        def _on_enter(event: Any) -> None:
            buf = event.app.layout.current_buffer
            if buf is not None:
                self._on_accept(buf)

        @self._kb.add("escape", "enter")
        def _on_newline(event: Any) -> None:
            buf = event.app.layout.current_buffer
            if buf is not None:
                buf.insert_text("\n")

        @self._kb.add("c-c")
        def _on_ctrl_c(event: Any) -> None:
            buf = event.app.layout.current_buffer
            if buf is not None and buf.text:
                buf.text = ""
            else:
                event.app.exit(exception=KeyboardInterrupt)

        @self._kb.add("c-d")
        def _on_ctrl_d(event: Any) -> None:
            buf = event.app.layout.current_buffer
            if buf is None or not buf.text:
                event.app.exit(exception=EOFError)

    # ── PromptSessionInterface 协议 ──

    def prompt(self, message: str = "") -> str:
        """进入全屏等待输入；提交后退出全屏返回文本。"""
        self._interrupt.clear()
        self._refresh_output_area()
        app = Application(
            layout=Layout(HSplit([
                self._output_area,
                Window(height=1, content=FormattedTextControl([("class:sep", "─" * 1)])),
                self._status_win,
                self._input_area,
            ])),
            key_bindings=self._kb,
            full_screen=True,
            mouse_support=True,
            refresh_interval=0.2,
        )
        try:
            result = app.run()
            return result if isinstance(result, str) else ""
        except KeyboardInterrupt:
            self._interrupt.set()
            return ""
        except EOFError:
            raise

    def push_to_history(self, text: str) -> None:
        # FileHistory 在 accept 时自动写入；此接口保留协议一致性
        _ = text

    def stream_print(self, renderable: Any) -> None:
        # 生成期走 stdout（P1 形态）；协议一致性实现
        print(renderable, end="", flush=True)

    def install_bottom_toolbar(self, get_fragments: Any) -> None:
        """保存状态栏片段回调（全屏期间每帧渲染调用）。"""
        self._status_cb = get_fragments

    def interrupt_event(self) -> threading.Event:
        return self._interrupt

    def set_streaming(self, streaming: bool) -> None:
        """全屏 TUI 全程渲染输入框，streaming 期间不改变行为（no-op）。

        FullTuiSession 的全屏 Application 独占整个终端，输入框始终可见。
        streaming 输出写入 output_area（通过 install_output_source 注入历史源）。
        pump 不在 FullTuiSession 期间运行（repl.py 检测到全屏即跳过 pump）。
        """
        _ = streaming  # 全屏 TUI 不需要此标志改变行为

    # ── 扩展接口（调用方可选注入） ──

    def install_output_source(self, source: Callable[[], list[str]]) -> None:
        """注入输出窗内容来源（会话历史行）。"""
        self._output_source = source

    # ── 内部 ──

    def _refresh_output_area(self) -> None:
        """进入全屏前重绘输出窗（取历史最近 MAX_OUTPUT_LINES 行）。"""
        try:
            lines = list(self._output_source() or [])
        except Exception:  # noqa: BLE001 — 历史源异常不阻塞输入
            lines = []
        if len(lines) > MAX_OUTPUT_LINES:
            lines = lines[-MAX_OUTPUT_LINES:]
        self._output_area.text = "\n".join(lines) + ("\n" if lines else "")

    def _status_fragments(self) -> list[tuple[str, str]]:
        if self._status_cb is not None:
            try:
                return self._status_cb() or []
            except Exception:  # noqa: BLE001 — 状态栏异常静默
                return []
        return []

    def _on_accept(self, buf: Any) -> bool:
        text = buf.text
        buf.text = ""
        if text:
            get_app().exit(result=text)
        return True
