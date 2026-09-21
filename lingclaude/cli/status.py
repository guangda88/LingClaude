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
    # 2026-09-17 第3级b：任务面板常驻数据——每轮由主循环喂入（in_progress 数
    # + pending 数 + 当前执行项摘要），toolbar 右下角角落显示，零额外 I/O。
    task_in_progress: int = 0
    task_pending: int = 0
    task_active: str = ""
    # 2026-09-21: 借鉴 atomcode —— 缓存命中率（0-100，-1=未知不显示）与权限模式
    cache_pct: int = -1
    perm_mode: str = ""
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
                task_in_progress=self.task_in_progress,
                task_pending=self.task_pending,
                task_active=self.task_active,
                cache_pct=self.cache_pct,
                perm_mode=self.perm_mode,
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

    # 2026-09-17 第3级b: 任务面板常驻数据喂入（主循环每轮从 TodoStore 聚合后调用）
    def set_task_panel(self, in_progress: int, pending: int, active: str) -> None:
        with self._lock:
            self.task_in_progress = in_progress
            self.task_pending = pending
            self.task_active = active

    # 2026-09-21: 借鉴 atomcode toolbar —— 缓存命中率 + 权限模式喂入
    def set_cache_pct(self, pct: int) -> None:
        with self._lock:
            self.cache_pct = pct

    def set_perm_mode(self, mode: str) -> None:
        with self._lock:
            self.perm_mode = mode

    def refresh_cwd(self) -> None:
        try:
            cwd = os.getcwd()
        except OSError:
            return
        with self._lock:
            self.cwd = cwd


def toolbar_fragments(s: StatusModel):
    """bottom_toolbar 回调 — 返回 (style, text) 片段列表。

    2026-09-21 重排（借鉴 atomcode）：
      ⏵⏵ auto │ glm5.3-flash │ ~/path │ 12.3k/128k tok (10%) │ cache 94% │ N轮 │ 任务 │ 待办
    上下文占比分色：<60% 绿 / <85% 黄 / >=85% 红+将压缩提示。
    钉住模型显示 [PINNED] 标识。
    """
    frag = []
    # 权限模式前缀（atomcode 的 ⏵⏵ auto 语义）——读运行时实际模式，未知则不显示
    perm = getattr(s, "perm_mode", "")
    if perm:
        frag.append(("class:accent", f" ⏵⏵ {perm} │"))
    cwd_display = s.cwd
    if len(cwd_display) > 28:
        cwd_display = "…" + cwd_display[-27:]
    model_display = s.model
    if s.pinned:
        model_display = f"{s.model} [PINNED]"
    frag.append(("class:accent", f" {model_display} "))
    frag.append(("", f"│ {cwd_display} "))
    # 上下文：k 格式 token 数 + 占比（atomcode 的 169.8k/262k tok (65%) 形态）
    # 自防护（2026-09-21）：窗口 ≤0 显示 n/a 而非除零/负%；分子>窗口时钳制
    # 显示 100%（如压缩层延迟落账瞬间），绝不渲染 >100% 的矛盾数字。
    if s.ctx_window > 0:
        ratio = s.ctx_tokens / s.ctx_window
        shown_ratio = min(ratio, 1.0)
        if ratio >= 0.85:
            style, mark = "class:red", " ⚠将压缩"
        elif ratio >= 0.6:
            style, mark = "class:yellow", ""
        else:
            style, mark = "class:green", ""
        _tk = s.ctx_tokens / 1000.0
        _wk = s.ctx_window / 1000.0
        _fmt = lambda v: f"{v:.1f}k" if v < 1000 else f"{v:.0f}k"  # noqa: E731
        frag.append((style, f"│ {_fmt(_tk)}/{_fmt(_wk)} tok ({shown_ratio:.0%}){mark} "))
    else:
        frag.append(("", f"│ 上下文 {s.ctx_tokens}tok "))
    # 缓存命中率（atomcode 的 cache 94% 语义）：-1 = 未知不显示
    _cp = getattr(s, "cache_pct", -1)
    if _cp >= 0:
        _cs = "class:green" if _cp >= 60 else ("class:yellow" if _cp >= 20 else "class:red")
        frag.append((_cs, f"│ cache {_cp}% "))
    task_display = s.task if len(s.task) <= 40 else s.task[:39] + "…"
    frag.append(("", f"│ {s.turns}轮 │ {task_display}"))
    # P1-4（2026-09-20）: 多行输入常驻提示（问题 3 的交互侧防线——键位记忆兜底）
    frag.append(("", "│ Esc+Enter 换行 · /multi 多行 "))
    if s.pending > 0:
        frag.append(("class:accent", f" │ 挂起×{s.pending}"))
    # 2026-09-17 第3级b: 任务面板角落常驻 — 有活跃任务时右下角显示
    # 🔄当前执行 + 待办数（每轮经 set_task_panel 刷新，零额外 I/O）。
    if s.task_active:
        act = s.task_active if len(s.task_active) <= 18 else s.task_active[:17] + "…"
        frag.append(("class:accent", f" │ 🔄 {act}"))
    if s.task_pending > 0:
        frag.append(("", f" │ 待办×{s.task_pending}"))
    return frag
