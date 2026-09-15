"""TUI 简单输入 provider — 迁移 cli/interface.py 的 FallbackSession。

CapabilityProvider 适配器：prompt 能力（无 prompt_toolkit 时的降级输入）。
"""

from __future__ import annotations

from typing import Any

from lingclaude.cli.interface import create_session


class SimplePromptProvider:
    """简单输入 provider（CapabilityProvider 协议）。"""

    name = "simple_prompt"
    version = "0.1.0"

    def __init__(self) -> None:
        self._session = None

    def _ensure_session(self) -> Any:
        if self._session is None:
            self._session = create_session(completer=None)
        return self._session

    def execute(self, method: str, *args: Any, **kwargs: Any) -> Any:
        if method == "prompt":
            return self._ensure_session().prompt(*args, **kwargs)
        if method == "create":
            return create_session(*args, **kwargs)
        raise KeyError(f"TUI simple_prompt 无此方法: {method}")

    def has(self, method: str) -> bool:
        return method in ("prompt", "create")
