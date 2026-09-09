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

import queue
import threading

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
                return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="input-pump")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        # P0-join fix: 同时 set session.interrupt_event() 唤醒阻塞中的
        # prompt()（prompt_toolkit 的 prompt() 阻塞在终端 read，只 set _stop
        # 不会退出；interrupt_event 是 PromptSessionInterface 的打断信号）。
        try:
            self._session.interrupt_event().set()
        except Exception:
            pass
        # join(timeout) 确保旧线程退出；2s 兜底不挂死主循环。
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop.is_set() and not self.dead:
            try:
                text = self._session.prompt(
                    self._prompt_text() if callable(self._prompt_text) else self._prompt_text
                )
            except EOFError:
                self._q.put_eof()
                return
            except KeyboardInterrupt:
                # Ctrl+C 清行语义：pump 独占 prompt() 后，空闲期 Ctrl+C 在本线程
                # 触发。清行重绘提示符，不退出线程（退出走 EOF 哨兵/quit）。
                continue
            except Exception as e:
                # prompt_toolkit 在极端终端下可能抛意外异常：标记死亡，
                # 主循环 is_alive() 检查后降级阻塞输入。死因必须留痕
                # （2026-09-09 静默死亡 → 挂起队列 6 条未消费，无从诊断）。
                import sys

                self.death_reason = f"{type(e).__name__}: {e}"
                print(f"[输入泵异常退出] {self.death_reason}", file=sys.stderr)
                self.dead = True
                return
            if text.strip():
                self._q.put(text)
