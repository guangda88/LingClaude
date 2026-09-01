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
        except EOFError:
            # AC#1 修复:env LINGCLAUDE_RAISE_EOF=1 → 重抛,让 _interactive_loop 退出
            if os.environ.get("LINGCLAUDE_RAISE_EOF") == "1":
                raise
            return ""

    def push_to_history(self, text: str) -> None:
        if text.strip():
            self._history.append_string(text)

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
            # AC#1 修复:env LINGCLAUDE_RAISE_EOF=1 → 重抛(默认吞 EOF 防误 Ctrl+D)
            if os.environ.get("LINGCLAUDE_RAISE_EOF") == "1":
                raise
            return ""
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
