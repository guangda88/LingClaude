"""P2 全屏 TUI 会话 — 常驻形态（2026-09-16，用户需求：输入行常驻屏幕底部）。

设计文档: docs/cli/TUI_BOTTOM_INPUT_DESIGN.md §二 HSplit 全屏布局。

形态（常驻 Application，v2 重写）:
- 后台线程运行全屏 Application（HSplit：可滚动输出窗 + 分隔线 + 状态栏 + 底部输入框），
  从 start() 起持续驻留，直到 close()。
- 输入框 Enter 提交 → 内部提交队列（deque+Condition）→ prompt() 阻塞取用。
  生成期提交同样入队（不退出全屏），轮结束后被主循环消费 —— 「随时可输入」。
- stdout 代理：Application 运行期间接管 sys.stdout，所有输出（含生成期
  sys.stdout.write 流式）按行追加进输出窗 —— 输出与输入框互不践踏。
- Ctrl+C：有文字清行；空缓冲 set interrupt_event 打断当前生成（与
  PromptToolkitSession 的 Ctrl+C 软中断语义对齐）。
- Ctrl+D（空输入）→ EOF 哨兵入队 → prompt() 抛 EOFError → 主循环正常退出。
- Esc 让位输入框编辑；Esc+Enter 换行（multiline 提交习惯不变）。

安全约束（P0/P1 事故防复发）:
- 不启用 InputPump / _esc_listen_loop 双读者（调用方 repl.py 对 FullTui
  不启 pump —— isinstance 判定天然跳过；stdin 唯一读者是 PT 事件循环）。
- prompt() 不直接操作终端；终端控制权全程归 PT 事件循环（后台线程）。
- 主循环退出路径 close() → app.exit() → PT 恢复终端状态，再打退出统计。

降级:
- start() 失败（极端终端）→ _running=False → prompt() 回退裸 input()。
- prompt_toolkit 未安装 → 构造抛 RuntimeError（create_session 捕获回退 P1）。
"""

from __future__ import annotations

import sys
import threading
from collections import deque
from pathlib import Path
from typing import Any, Callable

from lingclaude.cli.input_queue import EOF_SENTINEL
from lingclaude.cli.interface import _patch_pt_modifier_enter
from lingclaude.core.lineedit import add_history_line, ensure_readline

# prompt_toolkit 为可选依赖 — 未安装时构造抛 RuntimeError（create_session 捕获回退）
try:
    from prompt_toolkit.application import Application
    from prompt_toolkit.history import FileHistory, InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.output import create_output
    from prompt_toolkit.widgets import TextArea

    _HAS_PROMPT_TOOLKIT = True
except ImportError:  # pragma: no cover
    _HAS_PROMPT_TOOLKIT = False

# 与 interface.py 对齐的历史文件（项目内隔离，2026-09-15 P1-2）
DEFAULT_HISTORY_FILE = ".lingclaude/history"

# 输出窗行数上限（超出丢最旧行）
MAX_OUTPUT_LINES = 800


class _StdoutProxy:
    """stdout 代理 — 按行累积写入全屏输出窗（线程安全）。

    Application 驻留期间替换 sys.stdout；任何线程的 print / sys.stdout.write
    都被路由进输出窗 TextArea。isatty=False → rich Console 自动走 plain 渲染。
    \\r（进度式覆写）简单丢弃当前半行，不做原地重绘（输出窗是纯文本追加模型）。
    """

    def __init__(self, owner: "FullTuiSession", original: Any) -> None:
        self._owner = owner
        self._original = original
        self._frag: list[str] = []

    def write(self, s: str) -> int:
        if not s:
            return 0
        try:
            for ch in s:
                if ch == "\n":
                    self._flush_line()
                elif ch == "\r":
                    self._frag.clear()
                else:
                    self._frag.append(ch)
                    if len(self._frag) > 4096:  # 超长无换行防御
                        self._flush_line()
        except Exception:  # noqa: BLE001 — 输出代理异常不反噬调用方
            pass
        return len(s)

    def _flush_line(self) -> None:
        line = "".join(self._frag)
        self._frag.clear()
        if line:
            self._owner._write_via_buffer(line + "\n")

    def flush(self) -> None:
        # 半行也落窗（流式中途停滞时内容可见）
        try:
            self._flush_line()
        except Exception:  # noqa: BLE001
            pass

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise OSError("stdout proxy has no fd")

    @property
    def encoding(self) -> str:
        return getattr(self._original, "encoding", "utf-8")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


class FullTuiSession:
    """P2 常驻全屏 TUI 会话（PromptSessionInterface 协议实现）。

    架构：start() 后台线程跑常驻 Application；输入框 accept → 提交队列 →
    prompt() 返回；生成期 stdout 被代理进输出窗，输入框全程可编辑可提交。
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
        # 2026-09-18 多行输入增强:构造 UI 前改写 PT 输入序列表，让
        # Ctrl+Enter / Shift+Enter 复用下方 Esc+Enter 换行 chord
        # （单源补丁在 interface.py，P1/P2 共用）。
        _patch_pt_modifier_enter()
        # 流式生成期标志（set_streaming 置位；prompt 据此决定是否消费 interrupt）
        self._streaming = False

        # 常驻运行状态
        self._submit_q: deque[str] = deque()
        self._submit_cond = threading.Condition()
        self._app: Any = None
        self._app_thread: threading.Thread | None = None
        self._running = False
        self._ever_started = False  # prompt() 降级判定：未启动过→input()；已启动→EOF
        self._app_error: str = ""  # 后台线程异常留痕（2026-09-09 静默死亡教训）
        self._stdout_proxy: _StdoutProxy | None = None
        self._stdout_original: Any = None
        # 输出窗行缓冲（跨线程安全追加）：任意线程 write → pending_lines，
        # drain 进 TextArea。_area_lock 保护 TextArea 的读改写竞态
        # （多线程同时 join 搬运会互相覆盖丢行）。
        self._out_lock = threading.Lock()
        self._pending_lines: list[str] = []
        self._area_lock = threading.Lock()

        # 控件（常驻复用）
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
        self._sep_win = Window(
            height=1,
            content=FormattedTextControl([("class:sep", "─" * 200)]),
            style="class:sep",
        )

        # 键绑定：Enter 提交 / Esc+Enter（或 Ctrl+Enter / Shift+Enter，经
        # interface._patch_pt_modifier_enter 映射）换行 / Ctrl+C 清行或打断 / Ctrl+D 空退出
        self._kb = KeyBindings()

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
                # 空缓冲 Ctrl+C = 打断当前生成（interrupt_event 语义与 PT 会话对齐）
                self._interrupt.set()

        @self._kb.add("c-d")
        def _on_ctrl_d(event: Any) -> None:
            buf = event.app.layout.current_buffer
            if buf is None or not buf.text:
                self._submit(EOF_SENTINEL)

    # ── 生命周期 ──

    def start(self) -> None:
        """启动常驻全屏 Application（后台线程 + stdout 代理接管）。"""
        if self._running:
            return
        self._refresh_output_area()
        # 先保存真实 stdout（代理替换之前）—— PT 渲染输出必须直连真 stdout，
        # 否则走 sys.stdout 命中 _StdoutProxy → 写进输出窗 → invalidate →
        # 再渲染 → 死循环。
        self._stdout_original = sys.stdout
        out = create_output(stdout=self._stdout_original)
        self._app = Application(
            layout=Layout(HSplit([
                self._output_area,
                self._sep_win,
                self._status_win,
                self._input_area,
            ])),
            key_bindings=self._kb,
            full_screen=True,
            mouse_support=True,
            refresh_interval=0.2,
            # PT3: output/input 只能在构造期注入（run() 不接受 output 参数，
            # 传了直接 TypeError → 全屏线程启动即死）。
            output=out,
        )
        self._stdout_proxy = _StdoutProxy(self, self._stdout_original)
        sys.stdout = self._stdout_proxy
        self._running = True
        self._ever_started = True
        self._app_thread = threading.Thread(
            target=self._run_app, daemon=True, name="full-tui-app",
        )
        self._app_thread.start()

    def close(self) -> None:
        """退出全屏并恢复 stdout（主循环退出路径调用；幂等）。"""
        if not self._running:
            return
        self._running = False
        # 唤醒可能阻塞在 prompt() 的等待者
        with self._submit_cond:
            self._submit_cond.notify_all()
        try:
            if self._app is not None:
                self._app.exit()
        except Exception:  # noqa: BLE001 — app 已退出等场景静默
            pass
        if self._app_thread is not None:
            self._app_thread.join(timeout=3.0)
        if self._stdout_proxy is not None:
            sys.stdout = self._stdout_original
            self._stdout_proxy = None
        self._app = None

    def _run_app(self) -> None:
        try:
            # PT3: output 已在构造期注入（start()），run() 不得再传 —— 会
            # TypeError。事件循环在本后台线程创建运行，终端控制权全程归 PT。
            self._app.run()
        except Exception as e:  # noqa: BLE001 — 全屏线程死亡必须留痕
            self._app_error = f"{type(e).__name__}: {e}"
            self._running = False
        finally:
            # 线程死亡/退出时还原 stdout —— 否则后续输出全进不可见的
            # 输出窗缓冲，用户看不到任何内容（静默死亡事故防复发）。
            if self._stdout_proxy is not None:
                try:
                    sys.stdout = self._stdout_original
                except Exception:  # noqa: BLE001
                    pass
                self._stdout_proxy = None

    def _submit(self, text: str) -> None:
        with self._submit_cond:
            self._submit_q.append(text)
            self._submit_cond.notify_all()

    def pending_submissions(self) -> int:
        """未消费的提交数（状态栏「挂起×N」用，EOF 哨兵不计）。"""
        with self._submit_cond:
            return sum(1 for x in self._submit_q if x != EOF_SENTINEL)

    def append_output(self, s: str) -> None:
        """公开追加接口：任意线程输出进窗（stdout 代理与渲染层共用）。"""
        self._write_via_buffer(s)

    def _write_via_buffer(self, s: str) -> None:
        if not s:
            return
        frag: list[str] = []
        with self._out_lock:
            for ch in s:
                if ch == "\n":
                    self._pending_lines.append("".join(frag))
                    frag.clear()
                elif ch == "\r":
                    frag.clear()
                else:
                    frag.append(ch)
            if frag:
                self._pending_lines.append("".join(frag))
        self._drain_output_to_area()

    def _drain_output_to_area(self) -> None:
        """把 pending 行搬进 TextArea（跨线程调用安全：两把锁分段）。"""
        with self._out_lock:
            if not self._pending_lines:
                return
            lines = self._pending_lines
            self._pending_lines = []
        if lines:
            self._append_output_lines(lines)

    # ── PromptSessionInterface 协议 ──
    def push_to_history(self, text: str) -> None:
        # FileHistory 在 accept 时自动写入；此接口保留协议一致性
        _ = text

    def stream_print(self, renderable: Any) -> None:
        # stdout 代理驻留期间 print 自动进输出窗；协议一致性实现
        print(renderable, end="", flush=True)

    def install_bottom_toolbar(self, get_fragments: Any) -> None:
        """保存状态栏片段回调（全屏期间每帧渲染调用）。"""
        self._status_cb = get_fragments

    def interrupt_event(self) -> threading.Event:
        return self._interrupt

    def set_streaming(self, streaming: bool) -> None:
        """标记流式生成期。

        streaming=True 期间 prompt()（由 InputPump 线程调用）只等提交、
        **不消费 interrupt_event** —— Ctrl+C 打断归流循环检查
        （repl._run_stream_turn 每事件轮询）；否则 pump 线程会在 ≤0.2s 内
        清掉 interrupt，生成永远无法被打断。
        """
        self._streaming = streaming

    def prompt(self, message: str = "") -> str:
        """阻塞取一条已提交输入；EOF 哨兵抛 EOFError；空闲 Ctrl+C 返回 ""。

        Application 未启动（start 失败/未调用）→ 降级裸 input()（P1 逃生语义）。
        已启动后 close()/后台线程死亡 → EOFError（pump 优雅退出；不得回退
        input() 与 PT 事件循环抢 stdin —— 全屏已还原终端，直读会挂死/错乱）。
        """
        if not self._ever_started:
            # 从未成功启动（start 失败/未调用）→ 降级裸 input()（P1 逃生语义）
            try:
                # 2026-09-18 方向键/历史修复：降级 input() 同样挂 readline +
                # 写内存历史 —— 全屏降级路径与主输入路径行为对齐。
                ensure_readline()
                _line = input(message)
                add_history_line(_line)
                return _line
            except KeyboardInterrupt:
                self._interrupt.set()
                return ""
        while True:
            with self._submit_cond:
                while not self._submit_q:
                    self._submit_cond.wait(timeout=0.2)
                    # 注意：流式标志必须每轮实时读 —— pump 线程是长驻阻塞的
                    # （生成开始前就进入 prompt 等下一轮输入），快照会永远
                    # 停在进入时刻 → 流式期 Ctrl+C 仍被 pump 消费 → 打断失效。
                    if not self._streaming and self._interrupt.is_set():
                        # 空闲期 Ctrl+C 软中断语义：清事件、返回空串继续
                        self._interrupt.clear()
                        return ""
                    if not self._running:
                        # Application 已退出（close 或后台线程死亡）→ 结束会话。
                        # 死因留痕到 stderr（真实流，stdout 可能已还原）。
                        if self._app_error:
                            print(f"[全屏TUI异常退出] {self._app_error}", file=sys.stderr)
                        raise EOFError
                item = self._submit_q.popleft()
            if item == EOF_SENTINEL:
                raise EOFError
            return item

    def prompt_collect(self, message: str = "") -> str:
        """泵专用收集读：全屏 prompt() 走内部提交队列（不碰 stdin、无
        streaming 短路），直接委托，行为与旧路径一致。"""
        return self.prompt(message)

    # ── 扩展接口 ──

    def install_output_source(self, source: Callable[[], list[str]]) -> None:
        """注入输出窗初始内容来源（会话历史行）。"""
        self._output_source = source

    # ── 内部 ──

    def _refresh_output_area(self) -> None:
        """启动时重绘输出窗（取历史最近 MAX_OUTPUT_LINES 行）。"""
        try:
            lines = list(self._output_source() or [])
        except Exception:  # noqa: BLE001 — 历史源异常不阻塞输入
            lines = []
        if len(lines) > MAX_OUTPUT_LINES:
            lines = lines[-MAX_OUTPUT_LINES:]
        self._output_area.text = "\n".join(lines) + ("\n" if lines else "")

    def _append_output_lines(self, lines: list[str]) -> None:
        """追加行进输出窗并滚动到底（跨线程安全：_area_lock 串行化读改写）。

        行数超限丢最旧行；Application 未运行时仅更新缓冲（不渲染，无害）。
        """
        try:
            with self._area_lock:
                text = self._output_area.text
                all_lines = text.split("\n") if text else []
                all_lines.extend(lines)
                if len(all_lines) > MAX_OUTPUT_LINES:
                    all_lines = all_lines[-MAX_OUTPUT_LINES:]
                new_text = "\n".join(all_lines)
                self._output_area.text = new_text
                self._output_area.cursor_position = len(new_text)
            app = self._app
            if app is not None and self._running:
                app.invalidate()
        except Exception:  # noqa: BLE001 — 输出窗异常不反噬生成主线程
            pass

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
            self._submit(text)
        return True
