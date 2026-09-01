"""CLI I/O 抽象层 — RFC §3.3（CLI_INTERACTION_RFC v1.1）。

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

# prompt_toolkit 为可选依赖 — 未安装时 PromptToolkitSession 不可用，FallbackSession 兜底
try:
    from prompt_toolkit import PromptSession as _PTSession
    from prompt_toolkit.history import FileHistory, InMemoryHistory
    from prompt_toolkit.shortcuts import prompt as _pt_prompt

    _HAS_PROMPT_TOOLKIT = True
except ImportError:
    _HAS_PROMPT_TOOLKIT = False


DEFAULT_HISTORY_FILE = "~/.lingclaude/history"


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

    def interrupt_event(self) -> threading.Event:
        """返回 threading.Event，set 后取消当前生成（Esc / Ctrl+C 触发）。"""
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
        self._session = _PTSession(
            history=self._history,
            completer=completer,
        )
        self._interrupt = threading.Event()

    def prompt(self, message: str = "") -> str:
        self._interrupt.clear()
        try:
            return self._session.prompt(message)
        except KeyboardInterrupt:
            # Ctrl+C 软中断：清空当前输入，返回空串让上层继续
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

    def stream_print(self, renderable: Any) -> None:
        # 流式输出：直接写 stdout（Rich Live 在调用方管理刷新）
        print(renderable, end="", flush=True)

    def interrupt_event(self) -> threading.Event:
        return self._interrupt


class FallbackSession:
    """兜底实现 — 原裸 input() + sys.stdout.write + threading.Event。

    WebUI/IDE/CI 强制走这个；非 TTY 下 prompt_toolkit 不可用时的安全回退。
    """

    def __init__(self, history_file: str = DEFAULT_HISTORY_FILE) -> None:
        self._history: list[str] = []
        self._history_file = Path(history_file).expanduser()
        self._interrupt = threading.Event()
        self._load_history()

    def prompt(self, message: str = "") -> str:
        self._interrupt.clear()
        try:
            return input(message)
        except EOFError:
            # 审计#1 修复:EOF 传播（同 PromptToolkitSession）— 默认吞掉会造成
            # 非 TTY 场景「空输入→continue」死循环挂死。
            raise
        except KeyboardInterrupt:
            return ""

    def push_to_history(self, text: str) -> None:
        if text.strip():
            self._history.append(text)
            self._save_history()

    def stream_print(self, renderable: Any) -> None:
        sys.stdout.write(str(renderable))
        sys.stdout.flush()

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
    """入口选择：plain 或非 TTY → Fallback；否则 PromptToolkit（未装则 Fallback）。"""
    if os.environ.get("LINGCLAUDE_CLI_MODE") == "plain":
        return FallbackSession()
    if not sys.stdin.isatty():
        return FallbackSession()
    if not _HAS_PROMPT_TOOLKIT:
        return FallbackSession()
    try:
        return PromptToolkitSession(completer=completer)
    except RuntimeError:
        return FallbackSession()
