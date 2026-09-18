"""P1: 挂起输入队列 + 后台输入泵。

语义（三方案对齐定稿 2026-09-06）：
- 生成中提交的普通文本 -> 入队 FIFO，当前轮结束后逐条执行（复用现有执行分支，
  guard/落盘自动继承）
- 斜杠命令 -> 入队但轮结束后优先连续消费（不中断生成期，规避跨线程改
  engine._messages 的竞态；「即时」语义以「轮内即时」近似）
- Esc -> 只打断当前生成，不清队列
- 退出 -> drain() 丢弃未执行项并返回清单（半成品不落盘）
- EOF 哨兵 "\\x00EOF"：真实输入不含 NUL，安全区分于普通文本
"""
from __future__ import annotations

from collections.abc import Callable

import logging
import queue
import sys
import threading
import time
import types

EOF_SENTINEL = "\x00EOF"


def is_slash_command(text: str) -> bool:
    """斜杠命令判定。`//` 开头视为转义文本（用户想聊 URL 路径等场景）。"""
    stripped = text.strip()
    return stripped[:1] == "/" and stripped[:2] != "//"


class InputQueue:
    """线程安全挂起队列 -- 主循环消费，InputPump 生产。"""

    def __init__(self) -> None:
        self._q: "queue.Queue[str]" = queue.Queue()

    def put(self, text: str) -> None:
        self._q.put(text)

    def put_eof(self) -> None:
        self._q.put(EOF_SENTINEL)

    def get(self, timeout: float = 0.2):
        """阻塞取一条；超时返回 None（让主循环得以做别的事）。"""
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    @staticmethod
    def is_eof(item: str) -> bool:
        return item == EOF_SENTINEL

    def pending(self) -> int:
        return self._q.qsize()

    def drain(self) -> list:
        """取空队列（退出前调用），返回被丢弃内容供 UI 提示。"""
        dropped = []
        while True:
            try:
                dropped.append(self._q.get_nowait())
            except queue.Empty:
                break
        return [x for x in dropped if x != EOF_SENTINEL]


class InputPump:
    """后台线程跑 session.prompt() -- 输入常驻可打字，提交行进队列。

    与 Esc 的关系：P1 保留现有 _esc_listen_loop（单一职责：只抓 Esc），
    InputPump 只在生成期收文本。两者不同时读 stdin 的前提见 interface.py
    （生成期 prompt 暂停读端）。EOF 信号化传递，不静默吞。
    """

    def __init__(
        self,
        session,
        input_queue: InputQueue,
        prompt_text: str | Callable[[], str] = "灵克> ",
    ) -> None:
        self._session = session
        self._q = input_queue
        self._prompt_text = prompt_text
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.dead = False  # 线程异常死亡标记（主循环健康检查用）
        self.death_reason: str = ""  # 死因（诊断用，2026-09-09 静默死亡事故）
        # 心跳（2026-09-12 失活检测）:最近一次「prompt 成功返回」时刻。
        # 语义:仅反映 pump 线程是否在正常轮转,不反映用户是否打字 ——
        # prompt() 阻塞等输入期间心跳也会停滞,故主循环不得单凭心跳判死,
        # 必须结合 stdin 可读性（repl._maybe_stall_escape 的复合判定）。
        self._last_beat = time.monotonic()

    def start(self) -> None:
        # H17-TUI 修复:stop() 置位 _stop 后若直接再 start(),新线程第一轮
        # _run 检查 _stop.is_set() 立即退出 — pump「永远只能用一轮」。
        # 每次启动重建事件,支持逐轮启停（生成期启动/轮结束停止的调度模型）。
        # P0-join fix: start() 单读者防重入 — 旧线程未退时不再开新 prompt，
        # 防止两个 stdin 读者瓜分字节。
        if self._thread is not None and self._thread.is_alive():
            # 旧线程还活着（可能卡在 prompt_toolkit.prompt() 的阻塞 read），
            # 先 join 有限时间，未退则放弃 start 让主循环降级为阻塞 _read_input
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                self.dead = True
                logging.getLogger(__name__).warning(
                    "[输入泵] 旧线程 join 超时未退出，标记 dead 并放弃重建，"
                    "主循环将降级为直读。请检查输入泵线程是否卡死。"
                )
                return
        self._stop = threading.Event()
        self._start_t = time.monotonic()  # 诊断/测试:启动时刻基线
        self._wake_pending = False  # stop() 内部唤醒标记（抑制 [已打断] 噪声）
        self._thread = threading.Thread(target=self._run, daemon=True, name="input-pump")
        self._thread.start()

    def stop(self) -> None:
        """请求停转（2026-09-18 输入丢失修复重写）。

        旧实现只 set interrupt_event —— PT 根本不监听 wrapper 自建的 Event，
        阻塞在终端 read 的 prompt 永不返回，线程成为僵尸读者；上层降级后
        裸 input() 与之构成双读者，键入字节被僵尸吞掉（「打字被吞、回车
        无响应、需重输」根因）。新语义三步：
        1) 置 _stop：本轮 prompt 返回后循环自然退出；
        2) 硬唤醒：阻塞在 PT Application 里（app.is_running）时，先抢救
           default_buffer 半行入队（已敲未提交的文本不丢），再
           app.exit(KeyboardInterrupt) 让 prompt 立即抛出 —— PT 自身清理
           终端状态（raw 模式 / bracketed paste 复位），不留僵尸；
        3) 兜底：非 PT（fake/Fallback/FullTui 无 .app）走 interrupt_event()
           旧唤醒路。最后 join 有限时长，不挂死主循环。
        """
        self._stop.set()
        woke = False
        try:
            # wrapper._session 才是内层 PT PromptSession（app/default_buffer 在这）
            _pt = getattr(self._session, "_session", None)
            app = getattr(_pt, "app", None)
            if app is not None and getattr(app, "is_running", False):
                # 抢救半行：被打断 prompt 里已敲入、未提交的文本入队，
                # 下次 prompt 后由主循环消费 —— 用户不用重打
                try:
                    _buf = getattr(_pt, "default_buffer", None)
                    _text = getattr(_buf, "text", "") if _buf is not None else ""
                    if _text and _text.strip():
                        self._q.put(_text)
                except Exception:  # noqa: BLE001 — 抢救失败不阻塞停转
                    pass
                # 唤醒标记：抑制 [已打断] 噪声（内部唤醒，非用户打断）
                self._wake_pending = True
                app.exit(exception=KeyboardInterrupt)
                woke = True
        except Exception:  # noqa: BLE001 — PT 版本差异时退回旧唤醒路
            woke = False
        if not woke:
            try:
                self._session.interrupt_event().set()
            except Exception:  # noqa: BLE001 — fake session 无此方法时静默
                pass
        # join(timeout) 确保旧线程退出；2s 兜底不挂死主循环。
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def last_beat(self) -> float:
        """最近一次正常轮转时刻（monotonic 秒）。主循环失活判定用。"""
        return self._last_beat

    def _beat(self) -> None:
        """prompt 成功返回后打拍 —— 线程仍在正常轮转的最强证据。"""
        self._last_beat = time.monotonic()

    # 2026-09-18 误杀修复:生成期活跃标志 —— _run_stream_turn 每个流事件
    # note_activity()，_maybe_stall_escape 看到它就跳过失活判定（生成期
    # 心跳停滞 = prompt 正常阻塞，不是病态卡死）。
    streaming_active = False

    def note_activity(self) -> None:
        """流事件心跳：生成期每个事件调用，压住失活误判窗口。"""
        self.streaming_active = True

    def _run(self) -> None:
        # 2026-09-18 重复输入事故修复:泵优先走 prompt_collect（真读，无视
        # streaming 短路）。此前生成期 PT 包装层 prompt() 短路返回 ""，泵
        # 空转不读 stdin，用户输入滞留终端缓冲至流结束才处理（失活重建
        # TCSAFLUSH/键序列混入时首条真丢）→「无响应需重输」。第三方/
        # fake session 未实现 prompt_collect 时降级旧路径（协议兼容）。
        # 只认真绑定方法：MagicMock/属性代理的动态属性不算数（mock 未配置
        # 的属性调用会吞掉本应触发的异常路径）。
        _collect = getattr(self._session, "prompt_collect", None)
        if not isinstance(_collect, types.MethodType):
            _collect = None
        while not self._stop.is_set() and not self.dead:
            _msg = self._prompt_text() if callable(self._prompt_text) else self._prompt_text
            try:
                text = _collect(_msg) if _collect is not None else self._session.prompt(_msg)
            except EOFError:
                self._q.put_eof()
                return
            except KeyboardInterrupt:
                # Ctrl+C 清行语义：pump 独占 prompt() 后，空闲期 Ctrl+C 在本线程
                # 触发。清行重绘提示符，不退出线程（退出走 EOF 哨兵/quit）。
                # H17-输入泵修复:静默 continue 让 pump 线程被 Ctrl+C 中断时用户无感知，
                # 主循环若在 pump 重启前下一轮又调用 stream，会产生"stream 无故跳过"的
                # 假象。与主循环的 "[已打断]" 对齐，留痕不泄漏。
                # 2026-09-18:stop() 内部唤醒（app.exit）也走这里 —— _wake_pending
                # 置位时不打印 [已打断]（不是用户打断，是停转自唤醒），清标记后
                # 循环顶部 _stop 已置位自然退出。
                if getattr(self, "_wake_pending", False):
                    self._wake_pending = False
                else:
                    print("[已打断]", file=sys.stderr)
                continue
            except Exception as e:
                # prompt_toolkit 在极端终端下可能抛意外异常：标记死亡，
                # 主循环 is_alive() 检查后降级阻塞输入。死因必须留痕
                # （2026-09-09 静默死亡 → 挂起队列 6 条未消费，无从诊断）。
                self.death_reason = f"{type(e).__name__}: {e}"
                print(f"[输入泵异常退出] {self.death_reason}", file=sys.stderr)
                self.dead = True
                return
            # 心跳:prompt 正常返回（含空行）即打拍 —— 线程在轮转的硬证据
            self._beat()
            if text.strip():
                self._q.put(text)
