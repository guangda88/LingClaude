"""P1: 状态栏数据模型 — 单一数据源，主循环写，bottom_toolbar 读。

上下文占比口径（与 /compact 审计#9 修复同源）：
分母 = engine.config.context_window_tokens or max_budget_tokens；
分子 = _estimate_message_tokens(engine._messages)。
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field


@dataclass
class StatusModel:
    """可变状态容器。主循环在关键节点更新，toolbar 回调读取。"""

    model: str = "?"
    cwd: str = ""
    ctx_tokens: int = 0
    ctx_window: int = 0  # 0 = 未知，占比显示 n/a
    turns: int = 0
    task: str = "空闲"
    pending: int = 0
    pinned: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> "StatusModel":
        """toolbar 线程安全读取（返回浅拷贝，字符串/ int 均不可变，安全）。"""
        with self._lock:
            return StatusModel(
                model=self.model,
                cwd=self.cwd,
                ctx_tokens=self.ctx_tokens,
                ctx_window=self.ctx_window,
                turns=self.turns,
                task=self.task,
                pending=self.pending,
                pinned=self.pinned,
            )

    # ---- 更新方法（主循环调用） ----

    def set_task(self, task: str) -> None:
        with self._lock:
            self.task = task

    def bump_turns(self) -> None:
        with self._lock:
            self.turns += 1

    def set_pending(self, n: int) -> None:
        with self._lock:
            self.pending = n

    def set_ctx(self, tokens: int, window: int) -> None:
        with self._lock:
            self.ctx_tokens = tokens
            self.ctx_window = window

    def set_model(self, model: str) -> None:
        with self._lock:
            self.model = model

    def set_pinned(self, pinned: bool) -> None:
        with self._lock:
            self.pinned = pinned

    def refresh_cwd(self) -> None:
        try:
            cwd = os.getcwd()
        except OSError:
            return
        with self._lock:
            self.cwd = cwd


def toolbar_fragments(s: StatusModel):
    """bottom_toolbar 回调 — 返回 (style, text) 片段列表。

    上下文占比分色：<60% 绿 / <85% 黄 / >=85% 红+将压缩提示。
    钉住模型显示 [PINNED] 标识。
    """
    frag = []
    cwd_display = s.cwd
    if len(cwd_display) > 28:
        cwd_display = "…" + cwd_display[-27:]
    model_display = s.model
    if s.pinned:
        model_display = f"{s.model} [PINNED]"
    frag.append(("class:accent", f" {model_display} "))
    frag.append(("", f"│ {cwd_display} "))
    if s.ctx_window > 0:
        ratio = s.ctx_tokens / s.ctx_window
        if ratio >= 0.85:
            style, mark = "class:red", " ⚠将压缩"
        elif ratio >= 0.6:
            style, mark = "class:yellow", ""
        else:
            style, mark = "class:green", ""
        frag.append((style, f"│ 上下文 {ratio:.0%}{mark} "))
    else:
        frag.append(("", f"│ 上下文 {s.ctx_tokens}tok "))
    task_display = s.task if len(s.task) <= 40 else s.task[:39] + "…"
    frag.append(("", f"│ {s.turns}轮 │ {task_display}"))
    if s.pending > 0:
        frag.append(("class:accent", f" │ 挂起×{s.pending}"))
    return frag
