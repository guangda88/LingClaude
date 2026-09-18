"""

PromptSessionInterface Protocol + 两个实现：
- PromptToolkitSession: L1 实现，包 prompt_toolkit.PromptSession + rich.live.Live
- FallbackSession: 兜底实现，原裸 input() + sys.stdout.write + threading.Event

入口选择：LINGCLAUDE_CLI_MODE=plain 或非 TTY → FallbackSession（WebUI/IDE/CI 零影响）；
CLI TTY 默认 → PromptToolkitSession。
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from lingclaude.cli.repl_io import replay_stdin_bytes

# prompt_toolkit 为可选依赖 — 未安装时 PromptToolkitSession 不可用，FallbackSession 兜底
try:
    from prompt_toolkit import PromptSession as _PTSession
    from prompt_toolkit.history import FileHistory, InMemoryHistory
    from prompt_toolkit.shortcuts import prompt as _pt_prompt

    _HAS_PROMPT_TOOLKIT = True
except ImportError:
    _HAS_PROMPT_TOOLKIT = False


# 2026-09-15（会话问题重构 P1-2）: 命令历史从全局移到项目内 —— 此前
# "~/.lingclaude/history" 被所有项目进程共享，在 ~/lingflow 输入的命令
# 在 ~/lingclaude 按上键也会还原出来（跨项目泄露）。改为 ".lingclaude/history"
# （相对当前工作目录）：每个项目独立历史，不跨项目污染。
DEFAULT_HISTORY_FILE = ".lingclaude/history"


@runtime_checkable
class PromptSessionInterface(Protocol):
    """CLI 输入/输出/打断的统一接口。"""

    def prompt(self, message: str = "") -> str:
        """读取一行输入（含历史/键位）。"""
        ...

    def push_to_history(self, text: str) -> None:
        """把输入写入历史（prompt_toolkit 自动做；Fallback 也调）。"""
        ...

    def stream_print(self, renderable: Any) -> None:
        """生成中流式输出（prompt_toolkit 用 patch_stdout；Fallback 用 sys.stdout.write）。"""
        ...

    def install_bottom_toolbar(self, get_fragments: Any) -> None:
        """P1: 挂载状态栏回调（仅 PT 实现有效；Fallback 为 no-op）。"""
        ...

    def interrupt_event(self) -> threading.Event:
        """返回 threading.Event，set 后取消当前生成（Esc / Ctrl+C 触发）。"""
        ...

    def set_streaming(self, streaming: bool) -> None:
        """标记当前是否在流式输出期间（影响 prompt() 行为）。"""
        ...

    def prompt_collect(self, message: str = "") -> str:
        """输入泵专用收集读：无视 streaming 短路，真读一行输入。

        背景（2026-09-18 重复输入事故）：H17 会话级泵架构下，生成期唯一
        调 prompt() 的是 pump 线程，而 PT 包装层 streaming 短路让它空转
        完全不读 stdin —— 用户生成期打的命令滞留终端缓冲，流结束才被
        一次性处理（失活重建 TCSAFLUSH/序列混入时首条真丢）→ 体感
        「无响应需重输」。pump 应优先用本方法；未实现时调用方降级
        prompt()（兼容第三方/fake session）。
        """
        ...


class PromptToolkitSession:
    """L1 实现 — 包 prompt_toolkit.PromptSession + FileHistory。"""

    def __init__(
        self,
        history_file: str = DEFAULT_HISTORY_FILE,
        completer: Any | None = None,
    ) -> None:
        if not _HAS_PROMPT_TOOLKIT:
            raise RuntimeError("prompt_toolkit 未安装，请使用 FallbackSession")
        history_path = Path(history_file).expanduser()
        history_path.parent.mkdir(parents=True, exist_ok=True)
        self._history = FileHistory(str(history_path))
        # 2026-09-16（长文截断修复）:multiline=True — 单行模式粘贴长文/多行文本
        # 时 prompt_toolkit 只保留第一行、其余行被当作 Enter 提交丢弃（「长文字
        # 被截断吞没」）。多行模式下 Enter 重绑为提交、Shift+Enter 换行（见下方
        # _build_key_bindings），保持 CLI「敲 Enter 提交」习惯不变。
        self._session = _PTSession(
            history=self._history,
            completer=completer,
            multiline=True,
            key_bindings=self._build_key_bindings(),
        )
        self._interrupt = threading.Event()
        # 2026-09-16（TUI 输入泵问题修复）: streaming 标志。streaming 期间
        # prompt() 不阻塞（流式输出占用主线程，输入由 pump 线程异步收集），
        # 防止 session.prompt() 和 pump 线程双阻塞导致假死。
        self._streaming = False

    @staticmethod
    def _build_key_bindings() -> Any:
        """多行模式键位：Enter（无修饰）提交、Esc+Enter 换行。

        2026-09-16（长文截断修复）:multiline=True 后 prompt_toolkit 默认
        Enter 是换行、Meta+Enter 才提交 —— 不符合 CLI 习惯。按官方配方重绑：
        - Enter       → 提交（validate_and_handle）
        - Esc + Enter → 插入换行（粘贴长文/显式多行时用）
        单缓冲 PromptSession 无需 HasFocus 过滤；自动补全未展开时 Enter 仍
        先收下补全选择。
        """
        try:
            from prompt_toolkit.key_binding import KeyBindings
        except Exception:  # noqa: BLE001 — PT 版本差异时静默回退默认键位
            return None

        kb = KeyBindings()

        @kb.add("enter")
        def _submit(event: Any) -> None:
            buffer = event.app.current_buffer
            if buffer.complete_state is not None:
                # 自动补全下拉未关闭：Enter 先确认补全候选，不提交整行
                event.app.current_buffer.complete_state = None
                return
            buffer.validate_and_handle()

        @kb.add("escape", "enter")
        def _newline(event: Any) -> None:
            event.app.current_buffer.insert_text("\n")

        return kb

    def set_streaming(self, streaming: bool) -> None:
        """标记当前是否在流式输出期间。

        streaming=True 时 prompt() 返回 ""（不阻塞），让主线程继续流式输出。
        pump 线程异步收集输入，用户随时可打字；流结束后调用 prompt() 正常读取。
        """
        self._streaming = streaming

    def prompt(self, message: str = "") -> str:
        self._interrupt.clear()
        # 2026-09-16（TUI 输入泵问题修复）: streaming 期间不阻塞。
        # pump 线程在读 stdin，主线程阻塞 prompt() 会形成双阻塞：
        # 主线程等 prompt() 返回 ← 用户按 Enter ← pump 线程读完 ← 流结束
        # → pump 线程永远等用户按 Enter（因为 prompt() 在等）→ 假死。
        # 返回 "" 让上层立即处理队列已有输入（pump 已收集），不等待。
        if self._streaming:
            return ""
        try:
            return self._session.prompt(message)
        except KeyboardInterrupt:
            # Ctrl+C 软中断：清空当前输入，返回空串让上层继续。
            # 2026-09-15（会话问题重构 P0-2）：pump 模式下生成期 Ctrl+C 此前
            # 在此被吞成 ""（清行），InputPump 的 except KeyboardInterrupt 分支
            # 永不触发（异常已被本层捕获）→ 生成期中止无响应。修复：清行的
            # 同时 set interrupt_event —— 主线程流循环（repl.py 检查
            # interrupt_event）立即打断生成；空闲期 set 的 interrupt 由下一次
            # prompt() 开头的 clear() 清除，无副作用。
            self._interrupt.set()
            return ""
        # 审计#1/#7 修复:EOF（Ctrl+D / stdin 耗尽）直接传播让上层退出。
        # 此前默认吞成 ""，`lingclaude run -i < /dev/null` 会「空输入→continue→
        # 再读→再 EOF」死循环 100% CPU；exit 途径只剩输入 exit/quit。
        # （旧 LINGCLAUDE_RAISE_EOF=1 测试逃生门已无必要 — 默认即重抛，env 失效。）
        # 注意：不要在此捕获 EOFError 返回 ""。

    def push_to_history(self, text: str) -> None:
        # 审计#11 修复:PromptSession(prompt_toolkit) 在 prompt() 返回时已自动
        # 写入 FileHistory — 这里再 append_string 会让每条输入在历史文件里
        # 出现两遍。保留接口（Protocol 一致性），实现为 no-op。
        _ = text

    def prompt_collect(self, message: str = "") -> str:
        """泵专用收集读：无视 streaming 短路，真读 PT session。

        streaming 短路（prompt 返回 ""）是给主线程的防双阻塞设计；
        pump 线程是生成期唯一 stdin 读者，短路会让它空转不读 stdin
        （2026-09-18 重复输入事故根因）。本方法供 pump 使用，永远真读。

        修复（2026-09-18 五症同源）：必须先临时关闭 _streaming 标志，
        否则 PT 内部 PromptSession.prompt() 在 streaming 期间会直接返回 ""
        （根本不碰 stdin）。关闭后再读，读完恢复原值——PT PromptSession
        本身不支持"永远不短路"，只能靠外层包装绕行。
        """
        try:
            # 2026-09-18 五症同源修复: 临时撤销 streaming 标志 → PT 真读
            _was_streaming = self._streaming
            self._streaming = False
            try:
                return self._session.prompt(message)
            finally:
                self._streaming = _was_streaming
        except KeyboardInterrupt:
            # 生成期用户 Ctrl+C：set interrupt 让流循环打断（与 prompt()
            # 的软中断语义一致），本层不吞异常（pump 线程有自己的
            # KeyboardInterrupt 分支负责清行续转）。
            self._interrupt.set()
            raise

    def stream_print(self, renderable: Any) -> None:
        # 流式输出：直接写 stdout（Rich Live 在调用方管理刷新）
        print(renderable, end="", flush=True)

    def install_bottom_toolbar(self, get_fragments: Any) -> None:
        """P1: 挂载状态栏回调。prompt() 渲染时自动调用 get_fragments() 取片段。

        prompt_toolkit 原生管理该行（不碰光标位置，规避审计#6 的 termios 病灶）。
        已知边界：toolbar 仅在 prompt() 渲染期间可见 — 生成期屏幕上没有提示符，
        常驻可见性由 P2 全屏 TUI 解决；生成期反馈由挂起行回显提供。
        """
        try:
            self._session.bottom_toolbar = get_fragments
        except Exception:  # noqa: BLE001 — PT 版本差异时静默降级为无状态栏
            pass

    def interrupt_event(self) -> threading.Event:
        return self._interrupt


class FallbackSession:
    """兜底实现 — 原裸 input() + sys.stdout.write + threading.Event。

    WebUI/IDE/CI 强制走这个；非 TTY 下 prompt_toolkit 不可用时的安全回退。
    streaming 期间用 termios 非阻塞 select 读单行（不卡死流式输出）。
    支持方向键上/下翻历史。
    """

    def __init__(self, history_file: str = DEFAULT_HISTORY_FILE) -> None:
        self._history: list[str] = []
        self._history_file = Path(history_file).expanduser()
        self._interrupt = threading.Event()
        self._load_history()
        # 2026-09-16（TUI 输入泵问题修复）: streaming 标志
        self._streaming = False
        # readline 历史翻页位置（-1 = 最末，即新输入位置）
        self._rl_pos = -1
        # H19:streaming 非阻塞读超时时遗留的半行（下轮拼接续传，不再凭空丢失）
        self._rl_pending = b""
        # 2026-09-18 多行截断修复:bracketed paste 状态跨调用/跨超时持久 ——
        # 粘贴段内的 \n 是正文换行（不提交），只有裸 Enter（paste 段外的
        # \n/\r）才提交整行。旧实现读到一个 \n 就 break，多行粘贴只剩首行。
        self._rl_in_paste = False

    def set_streaming(self, streaming: bool) -> None:
        self._streaming = streaming

    def prompt(self, message: str = "") -> str:
        self._interrupt.clear()
        # 2026-09-16（TUI 输入泵问题修复）: streaming 期间非阻塞读。
        if self._streaming:
            return self._nonblocking_readline(message)
        try:
            return input(message)
        except EOFError:
            # 审计#1 修复:EOF 传播（同 PromptToolkitSession）— 默认吞掉会造成
            # 非 TTY 场景「空输入→continue」死循环挂死。
            raise
        except KeyboardInterrupt:
            return ""

    def _nonblocking_readline(self, message: str = "") -> str:
        """streaming 期间非阻塞读一行。

        用 termios + select 非阻塞读取，键盘缓冲区有完整行时立即返回，
        无数据时返回空串（不卡死流式输出主线程）。
        支持方向键上/下翻历史（readline 序列：\\x1b[A 上 / \\x1b[B 下）。
        """
        import select
        import termios

        if not sys.stdin.isatty():
            return ""

        try:
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
        except Exception:  # noqa: BLE001
            return ""

        try:
            # 原始模式：读单字节，不回显
            new = [list(x) if isinstance(x, list) else x for x in old]
            new[3] &= ~(termios.ICANON | termios.ECHO)
            new[6][termios.VMIN] = 0
            new[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSANOW, new)

            buf = bytearray()
            if message:
                os.write(sys.stdout.fileno(), message.encode())

            while True:
                # 2026-09-18 吞字修复:Esc 探测线程替读的用户键入优先取回
                # （必须最前，顺序在新字节之前 —— 探测先于本轮读发生）
                _rp = replay_stdin_bytes()
                if _rp:
                    buf.extend(_rp)
                    os.write(sys.stdout.fileno(), _rp)

                # H19:上轮超时遗留的半行先续传（必须在 drain 之前拼接，否则
                # 新到字节先进 buf、pending 尾随 → 顺序颠倒 "defabc"）
                if self._rl_pending:
                    buf.extend(self._rl_pending)
                    os.write(sys.stdout.fileno(), self._rl_pending)
                    self._rl_pending = b""

                # drain 残留字节（Ctrl+C / 方向键序列首字节触发 UnicodeDecodeError）
                # H19:此前读后即弃 — 粘贴大文本分片在 select 空窗期落入时
                # 被整片丢弃，是"长文本分段丢失"的第一来源。现把文本字节
                # 追加进 buf（顺带修退格回显错位）。
                while True:
                    r, _, _ = select.select([fd], [], [], 0.0)
                    if not r:
                        break
                    try:
                        leftover = os.read(fd, 4096)
                        if not leftover:
                            raise EOFError
                    except OSError:  # noqa: BLE001
                        break
                    if leftover.startswith(b"\x1b[200~"):
                        # bracketed paste 段:剥开/闭标记后正文照常入 buf
                        # （降级路径无行编辑器，标记留着会污染输入）；
                        # 整块只有标记时剥完为空，跳过。
                        leftover = leftover.replace(b"\x1b[200~", b"", 1).replace(
                            b"\x1b[201~", b""
                        )
                        if not leftover:
                            continue
                    elif leftover.startswith(b"\x1b"):
                        continue  # 其他转义序列残骸，丢弃
                    else:
                        # 2026-09-18 粘贴漏字修复:H19 逻辑只覆盖「以 \x1b 开头」
                        # 的分片 —— 标记/正文夹在其他正文中到达时（startswith
                        # 不命中），\x1b[200~ 字面漏进输入行。状态机剥离：
                        # 任何位置出现的开/闭标记都剥掉，正文保留。
                        leftover = leftover.replace(_PASTE_START, b"").replace(
                            _PASTE_END, b""
                        )
                        if not leftover:
                            continue
                    buf.extend(leftover)
                    os.write(sys.stdout.fileno(), leftover)

                # 等键盘（0.05s 超时，避免卡住流式输出）
                r, _, _ = select.select([fd], [], [], 0.05)
                if not r:
                    # 超时：无完整行 — 半行留到下轮拼接，不再凭空丢失
                    if buf:
                        self._rl_pending = bytes(buf)
                        buf = bytearray()
                    os.write(sys.stdout.fileno(), b"\r\x1b[K")
                    return ""

                ch = os.read(fd, 1)
                if not ch:
                    raise EOFError
                buf.extend(ch)
                os.write(sys.stdout.fileno(), ch)

                # 2026-09-18 转义序列统一处理（合并原 H18 分支）:
                # - \x1b[200~/\x1b[201~ 粘贴标记 → 翻转跨调用状态机（段内
                #   换行是正文不提交 = 多行粘贴不再截断）
                # - \x1b[A/\x1b[B → 历史翻页（此前 CSI 整体被字面回显 = 方向键 bug）
                # - 其他 CSI → 读到终结字节整体消费，不留残字节
                if ch == b"\x1b":
                    buf[-1:] = b""  # 摘掉先入 buf 的 \x1b（任何分支都不算正文）
                    r5, _, _ = select.select([fd], [], [], 0.05)
                    if not r5:
                        continue  # 孤立 Esc：消费掉
                    _n = os.read(fd, 4096)
                    if _n.startswith(b"[200~"):
                        self._rl_in_paste = True
                        _n = _n[5:]
                    elif _n.startswith(b"[201~"):
                        self._rl_in_paste = False
                        _n = _n[5:]
                    elif _n == b"[A" or _n == b"[B":  # 上/下:历史翻页
                        up = _n == b"[A"
                        if up:
                            if self._history and self._rl_pos < len(self._history) - 1:
                                self._rl_pos += 1
                            line = (
                                self._history[-(self._rl_pos + 1)]
                                if self._rl_pos >= 0
                                else ""
                            )
                        else:
                            if self._rl_pos > 0:
                                self._rl_pos -= 1
                                line = self._history[-(self._rl_pos + 1)]
                            elif self._rl_pos == 0:
                                self._rl_pos = -1
                                line = ""
                            else:
                                line = ""
                        self._erase_and_show(fd, buf, line)
                        buf = bytearray(line.encode())
                        continue
                    elif _n[:1] == b"[":
                        # 其他 CSI：读到终结字节（0x40-0x7E）为止，整体丢弃
                        while not (_n and 0x40 <= _n[-1] <= 0x7E):
                            r6, _, _ = select.select([fd], [], [], 0.05)
                            if not r6:
                                break
                            _n += os.read(fd, 4096)
                        continue
                    # 剩余正文（含标记剥离，防跨分片残留）
                    _n = _n.replace(_PASTE_START, b"").replace(_PASTE_END, b"")
                    if _n:
                        buf.extend(_n)
                        os.write(sys.stdout.fileno(), _n)
                    continue

                # Enter 提交 —— 仅 paste 段外；段内换行保留为正文
                # （多行粘贴不再只剩首行；段内换行回显已随上方 os.write(ch) 完成）
                if ch == b"\n" and not self._rl_in_paste:
                    break

                # 退格:先摘掉退格字节本身，再删它前面的字符 —— 旧实现只删
                # 退格字节，前字符留在 buf（视觉删了、提交时又出现）。
                if ch in (b"\x7f", b"\x08"):
                    buf = buf[:-1]
                    if buf:
                        buf = buf[:-1]
                        os.write(sys.stdout.fileno(), b"\x08 \x08")
                    continue

                # Ctrl+C
                if ch == b"\x03":
                    os.write(sys.stdout.fileno(), b"^C\n")
                    self._interrupt.set()
                    return ""

                # Ctrl+D
                if ch == b"\x04":
                    os.write(sys.stdout.fileno(), b"^D\n")
                    raise EOFError

                # CR → LF
                if ch == b"\r":
                    os.write(sys.stdout.fileno(), b"\n")
                    buf = buf[:-1] + b"\n"
                    break

                # 其他控制字符忽略
                if ch[0] < 32:
                    continue

            result = bytes(buf).decode("utf-8", errors="replace").rstrip("\n")
            if result:
                self._history.append(result)
                self._save_history()
            self._rl_pos = -1
            return result

        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    @staticmethod
    def _erase_and_show(fd: int, old_buf: bytearray, new_line: str) -> None:
        """擦掉旧行内容，显示新内容。"""
        spaces = " " * max(len(old_buf), 1)
        new_bytes = new_line.encode()
        os.write(fd, (f"\r\x1b[K{spaces}\r{new_bytes}").encode())

    def push_to_history(self, text: str) -> None:
        if text.strip():
            self._history.append(text)
            self._save_history()

    def stream_print(self, renderable: Any) -> None:
        sys.stdout.write(str(renderable))
        sys.stdout.flush()

    def install_bottom_toolbar(self, get_fragments: Any) -> None:
        # 兜底实现无状态栏能力 — no-op 保持接口一致
        _ = get_fragments

    def prompt_collect(self, message: str = "") -> str:
        """泵专用收集读：无视 streaming 短路，真读一行。

        修复（2026-09-18 五症同源）：prompt() 在 streaming 期间走
        _nonblocking_readline（非阻塞，无输入时立即返回空串），
        pump 线程空转 → 用户输入完全无响应。必须临时关闭 _streaming
        走阻塞 input()，等用户输完一行再恢复。
        """
        _was_streaming = self._streaming
        self._streaming = False
        try:
            return input(message)
        finally:
            self._streaming = _was_streaming

    def interrupt_event(self) -> threading.Event:
        return self._interrupt

    def _load_history(self) -> None:
        try:
            if self._history_file.exists():
                self._history = [
                    line for line in self._history_file.read_text(encoding="utf-8").splitlines() if line.strip()
                ]
        except OSError:
            self._history = []

    def _save_history(self) -> None:
        try:
            self._history_file.parent.mkdir(parents=True, exist_ok=True)
            self._history_file.write_text("\n".join(self._history[-200:]), encoding="utf-8")
        except OSError:
            pass


def create_session(completer: Any | None = None) -> PromptSessionInterface:
    """入口选择（优先级从高到低，设计文档 docs/cli/TUI_BOTTOM_INPUT_DESIGN.md §九）：

    1. LINGCLAUDE_TUI=0   → 强制 Fallback（CI/headless 关 TUI）
    2. LINGCLAUDE_TUI=2   → 强制 P2 全屏 TUI（TTY+PT 可用时；Q4 落地 2026-09-15）
    3. LINGCLAUDE_TUI=1   → TTY+PT 可用时强制启用（覆盖 CLI_MODE=plain；P1 形态）
    4. LINGCLAUDE_CLI_MODE=plain → Fallback（原有开关）
    5. 非 TTY / PT 未安装 / PT 构造失败 → Fallback
    """
    tui_env = os.environ.get("LINGCLAUDE_TUI")
    if tui_env == "0":
        return FallbackSession()
    if tui_env not in ("1", "2") and os.environ.get("LINGCLAUDE_CLI_MODE") == "plain":
        # 未设置 TUI 开关时维持原有 plain 语义；=1/=2 时 plain 被覆盖
        return FallbackSession()
    if not sys.stdin.isatty():
        return FallbackSession()
    if not _HAS_PROMPT_TOOLKIT:
        return FallbackSession()
    if tui_env == "2":
        # P2 全屏 TUI（Q4 落地）：构造失败回退 P1 形态，再失败回退 Fallback。
        # 全屏模式由 LINGCLAUDE_TUI=2 显式开启 —— 默认路径不受影响（P1 形态）。
        try:
            from lingclaude.cli.full_tui import FullTuiSession

            return FullTuiSession(completer=completer)
        except Exception:  # noqa: BLE001 — 全屏构造失败降级 P1，不崩
            pass
    try:
        return PromptToolkitSession(completer=completer)
    except RuntimeError:
        return FallbackSession()
