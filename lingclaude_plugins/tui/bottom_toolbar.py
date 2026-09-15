"""TUI 状态栏 provider — 迁移 cli/status.py 的 StatusModel+toolbar_fragments。

CapabilityProvider 适配器：提供 StatusModel 数据源 + toolbar_fragments 渲染。
"""

from __future__ import annotations

from typing import Any

from lingclaude.cli.status import StatusModel, toolbar_fragments


class ToolbarProvider:
    """状态栏 provider（CapabilityProvider 协议）。

    纯展示能力 → 免签注册（install 时 required_signers=[]）。
    """

    name = "tui_toolbar"
    version = "0.1.0"

    def __init__(self) -> None:
        self._model = StatusModel()

    @property
    def model(self) -> StatusModel:
        """暴露 StatusModel 给主循环写入。"""
        return self._model

    def execute(self, method: str, *args: Any, **kwargs: Any) -> Any:
        if method == "fragments":
            return toolbar_fragments(self._model.snapshot())
        if method == "snapshot":
            return self._model.snapshot()
        # 更新方法透传
        updater = getattr(self._model, method, None)
        if updater is None:
            raise KeyError(f"TUI toolbar 无此方法: {method}")
        return updater(*args, **kwargs)

    def has(self, method: str) -> bool:
        return method in ("fragments", "snapshot") or hasattr(self._model, method)
