"""P2 全屏 TUI 会话 — 常驻形态（2026-09-16，用户需求：输入行常驻屏幕底部）。

设计文档: docs/cli/TUI_BOTTOM_INPUT_DESIGN.md §二 HSplit 全屏布局。

形态（常驻 Application，v2 重写）:
- 后台线程运行全屏 Application（HSplit：可滚动输出窗 + 分隔线 + 状态栏 + 底部输入框），
  从 start() 起持续驻留，直到 close()。
- 输入框 Enter 提交 → 内部提交队列（deque+Condition）→ prompt() 阻塞取用。
  生成期提交同样入队（不退出全屏），轮结束后被主循环消费 —— 「随时可输入」。
- 输出历史滚动（2026-09-19）：滚轮 / PageUp/PageDown / Shift+↑↓ / Ctrl+Home
  / Ctrl+End 移动输出 buffer 光标行实现回看；光标回文末自动恢复跟随模式
  （分隔线提示「回看输出历史」）。输出窗=Window+BufferControl 子类手工组合
  （TextArea 不支持传 key_bindings，且其控件对无焦点滚轮直接 NotImplemented）；
  滚轮事件在控件层拦截 —— 无需焦点、不抢输入框焦点。
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

import logging
import re
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

from lingclaude.cli.input_queue import EOF_SENTINEL
from lingclaude.cli.interface import (
    _fallback_strip_ansi,
    _patch_pt_modifier_enter,
    _strip_ansi_text,
)
from lingclaude.engine.lineedit import add_history_line, ensure_readline

# prompt_toolkit 为可选依赖 — 未安装时构造抛 RuntimeError（create_session 捕获回退）
try:
    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.document import Document
    from prompt_toolkit.history import FileHistory, InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, Window
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.layout.margins import ScrollbarMargin
    from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
    from prompt_toolkit.output import create_output
    from prompt_toolkit.widgets import TextArea

    _HAS_PROMPT_TOOLKIT = True
except ImportError:  # pragma: no cover
    _HAS_PROMPT_TOOLKIT = False

if _HAS_PROMPT_TOOLKIT:
    from prompt_toolkit.keys import Keys  # noqa: E402 — 可选依赖条件导入

# 与 interface.py 对齐的历史文件（项目内隔离，2026-09-15 P1-2）
DEFAULT_HISTORY_FILE = ".lingclaude/history"

# 输出窗行数上限（超出丢最旧行）。
# P2-2（2026-09-20）: 800→5000 —— 长会话生成内容此前被静默裁剪，回看不完整；
# 5000 行约 0.5MB 内存，代价可忽略。
MAX_OUTPUT_LINES = 5000

# ---------------------------------------------------------------------------
# 长文本粘贴折叠（2026-09-21）：
# P2 全屏输入框粘贴多行长文本时逐行平铺——占满视口、淹没正在编辑的短行，
# 提交后输出窗回显也会被同一段文本二次刷屏。改为 Claude Code 式占位块：
# 粘贴 ≥ _PASTE_FOLD_MIN_LINES 行时，输入框内折叠为单行占位符
# 「[文本块 #N · M行 · C字符]」，提交时还原全文进提交队列（prompt() 的
# 消费方拿到的与未折叠行为无差异；输出窗回显保留占位符形态防刷屏）。
# 阈值常数便于小测试 monkeypatch 调低。
_PASTE_FOLD_MIN_LINES = 6

# 占位符模板与还原正则。还原按「编号在 _paste_registry 中存在」判定——
# 用户手打的同形字面串若编号从未登记过，不会被误还原。
_PLACEHOLDER_FMT = "[文本块 #{n} · {lines}行 · {chars}字符]"
_PLACEHOLDER_RE = re.compile(r"\[文本块 #(\d+) · \d+行 · \d+字符\]")


if _HAS_PROMPT_TOOLKIT:

    class _OutputScrollControl(BufferControl):
        """输出窗控件 — 在 BufferControl 基础上拦截滚轮事件。

        为什么子类化：原生 BufferControl.mouse_handler 只在**当前聚焦控件**
        是自己时才处理滚轮，否则直接 NotImplemented（controls.py:829 分支）。
        输出窗 focusable=False 永不聚焦 → 原生滚轮永远失效。这里把
        SCROLL_UP/SCROLL_DOWN 转成 on_wheel(+1/-1) 回调（无需焦点、不抢
        输入框焦点），其余事件交还父类。
        """

        def __init__(self, *args: Any, on_wheel: Callable[[int], None] | None = None, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._on_wheel = on_wheel

        def mouse_handler(self, mouse_event: MouseEvent) -> Any:
            et = mouse_event.event_type
            if self._on_wheel is not None:
                if et == MouseEventType.SCROLL_UP:
                    self._on_wheel(-1)
                    return None
                if et == MouseEventType.SCROLL_DOWN:
                    self._on_wheel(1)
                    return None
            return super().mouse_handler(mouse_event)


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
        # P1-3（2026-09-20）: 跨 write 调用的不完整转义序列尾部（拆片拼接）
        self._esc_hold = b""

    def write(self, s: str) -> int:
        if not s:
            return 0
        try:
            # P1-3（2026-09-20，TUI 优化方案）: 转义清洗 —— 终端残留/上游
            # 混入的 CSI 序列（CPR 应答、DECSCUSR、DECRQM 等）不清洗会字面
            # 进输出窗成噪声。复用 P0-2 状态机（CSI 纯 ASCII，UTF-8 编码后
            # 处理安全：多字节字符的首/续字节均不落 0x40-0x7E）。
            data = self._esc_hold + s.encode("utf-8", errors="replace")
            self._esc_hold = b""
            data, hold, _ = _fallback_strip_ansi(data, False)
            self._esc_hold = hold
            s2 = data.decode("utf-8", errors="replace")
            for ch in s2:
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
        if self._esc_hold:
            # P1-3: flush 时仍扣着的不完整序列 = 无终结字节的残骸，丢弃
            self._esc_hold = b""

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

        # 长文本粘贴折叠状态（_register_paste 写 / _expand_placeholders 读）：
        # _paste_seq 占位符编号单调递增；_paste_registry 编号 → (全文, 行数)。
        # 生命周期：全会话累计、不随提交清空——已消费占位符的残留条目无害
        # （还原只发生在提交瞬间，按当前 buffer 文本里出现的编号命中）。
        self._paste_seq = 0
        self._paste_registry: dict[int, tuple[str, int]] = {}

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

        # 控件（常驻复用）。输出窗 = Buffer + BufferControl子类 + Window 手工
        # 组合（TextArea 不支持传 key_bindings/自定义控件；见模块 docstring）。
        # 滚动模型：输出 buffer 光标 = 视口锚点（PT 渲染层 keep-cursor-visible
        # 按 cursor 行钳制 vertical_scroll）。跟随模式 = 光标钉在文末；滚轮/
        # 翻页把光标移进历史 → 回看模式；光标回文末自动恢复跟随。
        self._follow_output = True
        self._out_buffer = Buffer(
            document=Document("", 0),
            read_only=True,
            multiline=True,
            name="output-window",
        )
        self._out_control = _OutputScrollControl(
            buffer=self._out_buffer,
            focusable=False,
            focus_on_click=False,
            include_default_input_processors=False,
            on_wheel=self._on_out_wheel,
        )
        self._output_area = Window(
            content=self._out_control,
            wrap_lines=True,
            right_margins=[ScrollbarMargin(display_arrows=True)],
            style="class:output",
            always_hide_cursor=True,
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
            content=FormattedTextControl(self._sep_fragments),
            style="class:sep",
        )

        # 键绑定：Enter 提交 / Esc+Enter（或 Ctrl+Enter / Shift+Enter，经
        # interface._patch_pt_modifier_enter 映射）换行 / Ctrl+C 清行或打断 / Ctrl+D 空退出
        self._kb = KeyBindings()

        # 输出历史滚动键（app 级：优先级高于 emacs 默认绑定，application.py
        # _create_key_bindings 反转列表后当前控件链 > app > 默认）。全部为
        # 「移动输出 buffer 光标行」语义；光标回文末自动恢复跟随模式。
        @self._kb.add("pageup")
        def _out_page_up(event: Any) -> None:
            self._scroll_out_pages(-1)

        @self._kb.add("pagedown")
        def _out_page_down(event: Any) -> None:
            self._scroll_out_pages(1)

        @self._kb.add("s-up")
        def _out_line_up(event: Any) -> None:
            self._scroll_out_lines(-1)

        @self._kb.add("s-down")
        def _out_line_down(event: Any) -> None:
            self._scroll_out_lines(1)

        @self._kb.add("c-home")
        def _out_home(event: Any) -> None:
            self._out_buffer.cursor_position = 0
            self._invalidate()

        @self._kb.add("c-end")
        def _out_end(event: Any) -> None:
            self._out_buffer.cursor_position = len(self._out_buffer.text)
            self._invalidate()

        # 长文本粘贴折叠：接管 BracketedPaste（app 级绑定优先于 PT 默认的
        # 「直接整段插入」绑定——application.py:_create_key_bindings 反转
        # 绑定列表后 key_processor._process 只调 matches[-1]，唯一赢家，
        # 默认 handler 不会重复执行）。eager=True：粘贴语义独立成键，不等
        # 更长序列匹配，行为确定。
        @self._kb.add(Keys.BracketedPaste, eager=True)
        def _on_bracketed_paste(event: Any) -> None:
            self._handle_paste(event)

        @self._kb.add("enter")
        def _on_enter(event: Any) -> None:
            # 2026-09-19 输入历史修复：走 PT 标准 accept 流程
            # （validate_and_handle → accept_handler → append_to_history →
            # reset），保证提交的输入写进 FileHistory（Up 可翻）。
            # 旧实现直调 _on_accept 且内部清空文本，历史写入永远拿到空串。
            buf = event.app.layout.current_buffer
            if buf is not None:
                buf.validate_and_handle()

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

    def _build_application(self, output: Any) -> Any:
        """P2-13（Pi chord 双代热更）：新一代全屏 Application 的构造工厂。

        把 start() 里原本硬编码的 Application 构造抽成工厂——双代 cutover
        对「新一代」与「首代」共用同一构建逻辑。output 由调用方注入
        （首代取真实 stdout；候选代可注 fake 做离线 verify，不抢终端控制权）。
        返回未 run 的 Application 实例（生命周期归 cutover/start 管）。
        """
        return Application(
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
            output=output,
        )

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
        self._app = self._build_application(out)
        self._stdout_proxy = _StdoutProxy(self, self._stdout_original)
        sys.stdout = self._stdout_proxy
        self._running = True
        self._ever_started = True
        self._app_thread = threading.Thread(
            target=self._run_app, daemon=True, name="full-tui-app",
        )
        self._app_thread.start()
        # P2-3（2026-09-20，TUI 优化方案）: 全屏健康自检 —— 1s 后未驻留
        # （启动即死/从未进入运行态）时 stderr 显式留痕，消除「用户不知情
        # 被降级成简易输入模式」的静默失败。
        _probe = threading.Timer(1.0, self._startup_health_probe)
        _probe.daemon = True
        _probe.start()

    def cutover_generation(
        self,
        build_new: "Callable[[], Any] | None" = None,
        verify: "Callable[[Any], None] | None" = None,
    ) -> bool:
        """P2-13（Pi chord 双代热更）：新一代渲染 cutover（蓝绿，零不可用窗口）。

        Pi chord 语义：新一代渲染器以 **candidate** 先构建 + 验证（不动现役
        app），验证成功才 **cutover**（旧代优雅退役 → 新代接管），失败则
        **dispose candidate**、旧代继续服务。旧代全程在线兜底，用户零感知。

        参数：
        - build_new: 新一代 Application 工厂。缺省用 self._build_application
          （注真实 stdout 同源 output，等价当前 start() 的构造逻辑）；
          宿主可注入自定义工厂（换渲染主题/布局/刷新策略等「新一代」形态）。
        - verify: 候选验证钩子（candidate → 断言/异常）。缺省做最小健全性
          检查（非 None + 是 Application + 有 layout）。verify 抛异常即候选
          验证失败 → dispose 回退旧代。

        返回 True=切换成功；False=候选失败已回退（旧代仍服务，零不可用窗口）。

        与 plugin_lifecycle.hot_swap 对齐的蓝绿语义（本方法是其「渲染层」
        对位——插片实例面已在 plugin_lifecycle 落地，此处补齐 TUI 渲染代次
        的双代 cutover，即 §3.2 P2-13 缺口）：
        - candidate 构建在现役 app 之外（不抢终端控制权，output 可离线注入）；
        - 切换原子化：旧代 app.exit() + 线程 join 完成后才起新代线程；
        - 失败不破坏旧代：candidate dispose + 旧代零扰动。

        线程安全：持有 self._app_thread 的 join 语义；与 prompt()/_run_app
        通过 _submit_cond / _running 状态协作（切换瞬间输出窗按「旧线程退出、
        新线程接管」有序接力，不丢行——pending_lines 跨代保留）。
        """
        # 1) 构建候选（不动现役 self._app）
        try:
            if build_new is not None:
                candidate = build_new()
            else:
                # 缺省工厂：与 start() 同源——真实 stdout 的 output
                out = create_output(stdout=self._stdout_original or sys.stdout)
                candidate = self._build_application(out)
        except Exception:  # noqa: BLE001 — 构建失败 = 候选不可用，旧代不动
            logger.warning("cutover_generation: 候选构建失败，保留旧代", exc_info=True)
            return False

        # 2) 验证候选（候选态，未接管）
        try:
            if verify is not None:
                verify(candidate)
            else:
                self._verify_candidate(candidate)
        except Exception:  # noqa: BLE001 — 验证失败 = dispose 候选，旧代不动
            logger.warning("cutover_generation: 候选验证失败，保留旧代", exc_info=True)
            return False

        # 3) 切换（原子接力：旧代退役 → 新代接管）
        old_app = self._app
        old_thread = self._app_thread
        self._app = candidate

        # 3a) 优雅退役旧代（若正在运行）：exit + join（超时兜底不阻塞）
        if old_app is not None:
            try:
                old_app.exit()
            except Exception:  # noqa: BLE001
                pass
        if old_thread is not None and old_thread.is_alive():
            old_thread.join(timeout=3.0)

        # 3b) 起新代线程（接管 stdout 代理与事件循环）
        if self._running or (old_thread is not None):
            self._app_thread = threading.Thread(
                target=self._run_app, daemon=True, name="full-tui-app-gen2",
            )
            self._app_thread.start()
        else:
            # 首代尚未 start（冷 cutover）：补全 start() 的初始化路径
            self._stdout_proxy = _StdoutProxy(self, self._stdout_original or sys.stdout)
            sys.stdout = self._stdout_proxy
            self._running = True
            self._ever_started = True
            self._app_thread = threading.Thread(
                target=self._run_app, daemon=True, name="full-tui-app-gen2",
            )
            self._app_thread.start()

        logger.info("cutover_generation: 蓝绿切换完成（旧代退役 → 新代接管）")
        return True

    def _verify_candidate(self, candidate: Any) -> None:
        """P2-13: 候选代最小健全性验证（verify 缺省实现）。

        健全性门（不真 run 抢终端）：非 None + 是 Application 实例 + 有
        layout + 有 key_bindings。宿主可传自定义 verify 覆盖（更强断言）。
        """
        if candidate is None:
            raise ValueError("候选 Application 为 None")
        from prompt_toolkit.application import Application as _PTApp
        if not isinstance(candidate, _PTApp):
            raise TypeError(f"候选不是 prompt_toolkit Application: {type(candidate)}")
        if not getattr(candidate, "layout", None):
            raise ValueError("候选 Application 缺 layout（无法接管渲染）")

    def _startup_health_probe(self) -> None:
        """P2-3: start 后 1s 自检 —— 全屏未驻留时显式告知已降级。"""
        try:
            alive = (
                self._running
                and self._app is not None
                and self._app_thread is not None
                and self._app_thread.is_alive()
            )
            if alive:
                return
            err = self._app_error or "Application 未进入运行态"
            print(
                f"[全屏TUI] 启动自检未通过，已降级为简易输入模式（原因: {err}）",
                file=sys.stderr,
            )
        except Exception:  # noqa: BLE001 — 自检失败不影响主流程
            pass

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
        # P0-4（2026-09-20，TUI 优化方案）: 退出全屏后再发一次终端增强模式
        # 复位（对称卫生：清别人残留，也别留自己的）。⚠ 顺序必须 app.exit()
        # 并还原 stdout 之后 —— 先复位会被 PT 退场序列/重绘重新进入增强模式，
        # 等于白发。reset_terminal_key_modes 内部自带 isatty 防御。
        try:
            from lingclaude.engine.lineedit import reset_terminal_key_modes

            reset_terminal_key_modes()
        except Exception:  # noqa: BLE001 — 增强路径，绝不反噬退出流程
            pass

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

    def _register_paste(self, data: str) -> tuple[str, int]:
        """登记一次粘贴：返回（插入物, 行数）。

        换行数 < _PASTE_FOLD_MIN_LINES → 原样返回（短粘贴不打扰编辑）；
        达到阈值 → 折叠为占位符并登记全文（提交时还原）。占位符编号
        全会话单调递增，注册表只增不减（残留条目无害，见 __init__ 注释）。
        """
        # 与 PT 默认绑定对齐的换行归一（iTerm2 粘贴 \r\n，见 basic.py:244）
        data = data.replace("\r\n", "\n").replace("\r", "\n")
        lines = data.count("\n") + 1
        if lines < _PASTE_FOLD_MIN_LINES:
            return data, lines
        self._paste_seq += 1
        n = self._paste_seq
        self._paste_registry[n] = (data, lines)
        placeholder = _PLACEHOLDER_FMT.format(n=n, lines=lines, chars=len(data))
        return placeholder, lines

    def _handle_paste(self, event: Any) -> None:
        """BracketedPaste 处理器：折叠长粘贴 → 插入占位符/短文本。

        仅在聚焦 buffer 成功时插入；任何异常静默降级为默认行为
        （直接插入原文）——粘贴是高频路径，不能因折叠逻辑故障而丢输入。
        """
        data = event.data or ""
        buf = event.app.layout.current_buffer if event.app is not None else None
        if buf is None:
            return
        try:
            insert, _lines = self._register_paste(data)
        except Exception:  # noqa: BLE001 — 折叠失败降级为原样插入
            insert = data.replace("\r\n", "\n").replace("\r", "\n")
        buf.insert_text(insert)

    def _expand_placeholders(self, text: str) -> str:
        """提交前还原：buffer 文本中的占位符 → 登记的粘贴全文。

        只还原注册表里存在的编号（用户手打的同形字面串不会误伤）；
        已注册但文本中不存在的占位符忽略（复制粘贴占位符本身的边界）。
        """
        if not self._paste_registry:
            return text

        def _sub(m: Any) -> str:
            entry = self._paste_registry.get(int(m.group(1)))
            return entry[0] if entry is not None else m.group(0)

        return _PLACEHOLDER_RE.sub(_sub, text)

    def _fold_echo(self, full_text: str) -> str:
        """回显折叠：还原后的全文把已登记粘贴重新折回占位符（防刷屏）。

        按粘贴长度降序替换——长粘贴可能是短粘贴的超集（两次粘贴部分
        重叠），先替换短的会把长粘贴内部截断、导致其全文匹配失败串位。
        """
        if not self._paste_registry:
            return full_text
        for n, (data, lines) in sorted(
            self._paste_registry.items(), key=lambda kv: len(kv[1][0]), reverse=True
        ):
            if data in full_text:
                full_text = full_text.replace(
                    data, _PLACEHOLDER_FMT.format(n=n, lines=lines, chars=len(data))
                )
        return full_text

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
        # 2026-09-20 乱码修复：append_output 直通路径此前无清洗，模型回复
        # 内嵌 SGR 序列（\x1b[1;4m…）落 TextArea 被渲染成 '?[1;4m' 明文。
        # 汇聚点统一剥 ANSI（stdout 代理已剥过，幂等无害）。
        s = _strip_ansi_text(s)
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

    def resync(self) -> None:
        """P3 全量重绘原语（2026-09-20，atomcode invalidate 借鉴）。

        终端状态可疑（resize/怀疑渲染失步/想强制刷新回放）时，调用方
        无需知道哪条路径漏了——直接从 output_source 重建整个输出窗文档
        并触发重绘。一个原语覆盖所有「窗口内容 vs 期望状态」失步场景，
        替代逐路径打补丁。线程安全性同 _write_via_buffer（主线程调用
        最佳；他线程调用经 _invalidate 请求重绘，文档替换本身幂等）。
        """
        self._refresh_output_area()
        self._invalidate()

    # ── 内部 ──

    def _refresh_output_area(self) -> None:
        """启动时重绘输出窗（取历史最近 MAX_OUTPUT_LINES 行）。"""
        try:
            lines = list(self._output_source() or [])
        except Exception:  # noqa: BLE001 — 历史源异常不阻塞输入
            lines = []
        if len(lines) > MAX_OUTPUT_LINES:
            lines = lines[-MAX_OUTPUT_LINES:]
        self._set_output_lines(lines)

    def _set_output_lines(self, lines: list[str]) -> None:
        """整体替换输出窗内容，光标钉回文末（跟随模式）。

        注意：文本末尾**不加**换行 —— 否则光标钉文末时落在幻影空行上，
        cursor_position_row = line_count（比最后一行实际行号大 1），滚动
        计算会整体偏 1。

        2026-09-20 乱码修复（第二路径）：历史回放（_refresh_output_area）
        的行来自会话 transcript，可能含模型回复内嵌的 SGR 序列——此前
        唯一未清洗的 set_document 入口，重启回放后 0x1b 渲染成 '?' 再漏
        明文。与 _write_via_buffer 同用一剥离器（幂等，双洗无害）。
        """
        text = "\n".join(_strip_ansi_text(line) for line in lines)
        self._out_buffer.set_document(Document(text, 0), bypass_readonly=True)
        self._out_buffer.cursor_position = len(text)
        self._follow_output = True

    def _invalidate(self) -> None:
        """请求重绘（app 未运行时静默；可从任意线程调用）。"""
        app = self._app
        if app is not None and self._running:
            try:
                app.invalidate()
            except Exception:  # noqa: BLE001 — 重绘请求失败不反噬调用方
                pass

    # ── 输出历史滚动（滚轮回调与键绑定共用「移动光标行」语义） ──

    def _on_out_wheel(self, direction: int) -> None:
        """滚轮事件（_OutputScrollControl 回调）：+1 向下 / -1 向上。"""
        self._scroll_out_lines(direction)

    def _scroll_out_lines(self, delta: int) -> None:
        """输出窗光标上/下移 delta 行（负值向历史）；边界钳制。

        滚到最后一行时光标钉到文末 → is_cursor_at_the_end=True → 自动
        恢复跟随模式（后续新输出把视口拽回底部）。
        """
        try:
            buf = self._out_buffer
            doc = buf.document
            line_count = doc.line_count
            if not line_count:
                return
            row = doc.cursor_position_row + delta
            row = max(0, min(row, line_count - 1))
            if row >= line_count - 1:
                buf.cursor_position = len(buf.text)
            else:
                buf.cursor_position = doc.translate_row_col_to_index(row, 0)
            self._follow_output = buf.document.is_cursor_at_the_end
            self._invalidate()
        except Exception:  # noqa: BLE001 — 滚动异常不反噬事件循环
            pass

    def _scroll_out_pages(self, pages: int) -> None:
        """输出窗整页滚动（PageUp/PageDown）：按可视高度移动光标行。"""
        try:
            info = self._output_area.render_info
            height = info.window_height if info is not None else 0
            if height <= 0:
                height = 10  # 尚无渲染信息（未首帧）时的兜底页高
            self._scroll_out_lines(pages * max(1, height - 1))
        except Exception:  # noqa: BLE001 — 滚动异常不反噬事件循环
            pass

    def _append_output_lines(self, lines: list[str]) -> None:
        """追加行进输出窗（跨线程安全：_area_lock 串行化读改写）。

        行数超限丢最旧行；Application 未运行时仅更新缓冲（不渲染，无害）。
        跟随模式：光标钉回文末（新输出可见）；回看模式：光标行保持不变
        （set_document 会重置光标，必须显式恢复），仅当旧行被裁掉时按裁剪
        量上移光标修正视口锚点。
        """
        try:
            with self._area_lock:
                buf = self._out_buffer
                old_text = buf.text
                old_row = buf.document.cursor_position_row
                all_lines = old_text.split("\n") if old_text else []
                all_lines.extend(lines)
                dropped = 0
                if len(all_lines) > MAX_OUTPUT_LINES:
                    dropped = len(all_lines) - MAX_OUTPUT_LINES
                    all_lines = all_lines[-MAX_OUTPUT_LINES:]
                new_text = "\n".join(all_lines)
                buf.set_document(Document(new_text, 0), bypass_readonly=True)
                if self._follow_output or not new_text:
                    buf.cursor_position = len(new_text)
                else:
                    # 回看中：恢复光标行；首部被裁时按裁剪量上移（视口锚点
                    # 随内容平移，视觉位置不变）；滚到最后一行=回到文末
                    row = max(0, old_row - dropped)
                    if row >= len(all_lines) - 1:
                        buf.cursor_position = len(new_text)
                    else:
                        buf.cursor_position = buf.document.translate_row_col_to_index(
                            row, 0
                        )
                follow_now = buf.document.is_cursor_at_the_end
            self._follow_output = follow_now
            self._invalidate()
        except Exception:  # noqa: BLE001 — 输出窗异常不反噬生成主线程
            pass

    def _sep_fragments(self) -> list[tuple[str, str]]:
        """分隔线片段：回看模式时在行内提示（含恢复跟随的键位）。"""
        if self._follow_output:
            return [("class:sep", "─" * 200)]
        hint = (
            "← 回看输出历史（滚轮/Shift+↑↓/PageUp·Down 浏览，"
            "Ctrl+End 或滚到底恢复跟随） "
        )
        sep = "─" * max(0, 200 - len(hint) - 1)
        return [("class:sep", sep + "┤ "), ("class:sep:reverse", hint), ("class:sep", " ├")]

    def _status_fragments(self) -> list[tuple[str, str]]:
        if self._status_cb is not None:
            try:
                return self._status_cb() or []
            except Exception:  # noqa: BLE001 — 状态栏异常静默
                return []
        return []

    def _on_accept(self, buf: Any) -> bool:
        text = buf.text
        if text:
            # 2026-09-21 输入回显：提交的输入即时进输出窗（"> " 前缀，终端惯例）。
            # 此前提交后 buffer 被 PT reset 清空，输出窗无痕——用户输入与
            # 模型回复在历史里混在一起无法区分。回显走 append_output
            # （_area_lock 跨线程安全，未启动时仅更新缓冲无害）。
            # 注意：回显的是输入文本本身，escape 后换行符已被替换。
            # 2026-09-21 粘贴折叠配套：回显前还原占位符 → 用户在输出窗
            # 看到完整提交内容（含粘贴全文）；但回显再次折叠为占位符——
            # 长粘贴回显会刷屏，占位符形态与输入框所见一致。
            full_text = self._expand_placeholders(text)
            echo_text = self._fold_echo(full_text)
            try:
                self.append_output("> " + echo_text + "\n")
            except Exception:  # noqa: BLE001 — 回显失败不阻断提交
                pass
            self._submit(full_text)
        # 2026-09-19 输入历史修复：**不得在此清空 buffer.text**。
        # PT 标准 accept 流程（buffer.validate_and_handle）是先调 accept_handler
        # 再 append_to_history → reset；旧实现先置 buf.text=""，append_to_history
        # 读到空串直接跳过（buffer.py:1363 if self.text:）→ P2 全屏会话提交的
        # 输入从未进入 FileHistory → Up 无史可翻。
        # 文本清空交给 PT 的 reset()（accept_handler 返回 False 即可）。
        # 历史游标（working_index）保持不动：翻历史后提交，下一帧首帧渲染时
        # load_history_if_not_yet_loaded 会以 FileHistory 最新内容重放装载。
        return False
