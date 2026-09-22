"""P1: 状态栏数据模型 — 单一数据源，主循环写，bottom_toolbar 读。

上下文占比口径（2026-09-22 ctx 口径修复后）：
分母 = config.context_window_tokens，缺省回退 128_000（repl.py 代码级回退）；
max_budget_tokens 是会话累计预算，不再充当窗口分母。
分子 = turn 内最后成功请求轮的真实 prompt_tokens（provider 未回传 usage
时为负哨兵 → 消费方走字符估算）。
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
    # 2026-09-22: 任务清单面板（对标 atomcode todo panel 常驻 toolbar 上方）——
    # 每秒由 _toolbar_snapshot 从 TodoStore 聚合喂入（(status_value, content) 元组），
    # 渲染层 toolbar_fragments 在状态行上方展开为多行清单。空元组 = 不占版面。
    todo_items: tuple[tuple[str, str], ...] = ()
    # 2026-09-22: 状态球（对标 atomcode）——运行状态三色，渲染层映射绿/黄/红：
    #   "busy"   生成中（streaming 或活跃任务）→ 黄
    #   "blocked" 阻塞/中断（interrupt_event 置位）→ 红（优先级最高）
    #   "idle"   空闲（以上皆否）→ 绿
    # 由 _toolbar_snapshot 每秒从 session/task_active 判定喂入；判定纯只读、
    # 失败静默留 idle（绿）——状态球是装饰性常驻，不反噬主循环。
    state_level: str = "idle"
    # 2026-09-22: plan 模式叠加态指示（⏸ plan）——plan 是 runtime 内存叠加态
    # （不落盘、不覆盖权限模式），由 _toolbar_snapshot 每秒从
    # engine._runtime.plan_mode.is_active 读入；true 时状态行追加 ⏸ plan 段。
    plan_active: bool = False
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
                # 2026-09-22: todo panel 明细随快照透传（元组不可变，浅拷贝安全）
                todo_items=self.todo_items,
                # 2026-09-22: 状态球三色透传（str 不可变，浅拷贝安全）
                state_level=self.state_level,
                # 2026-09-22: plan 叠加态透传（bool 不可变，浅拷贝安全）
                plan_active=self.plan_active,
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

    # 2026-09-22: 任务清单明细喂入（todo panel 常驻 toolbar 上方，对标 atomcode）。
    # items: (status_value, content) 元组序列，只收未完成项（面板语义），
    # 空/None 清空面板。渲染放 toolbar_fragments，喂入走 1s 节流的快照路径。
    def set_todo_items(self, items: "list[tuple[str, str]] | tuple | None") -> None:
        norm = tuple(tuple(x) for x in items) if items else ()
        with self._lock:
            self.todo_items = norm

    # 2026-09-22: 状态球喂入（对标 atomcode）——_toolbar_snapshot 每秒判定后调用。
    # level ∈ {"idle","busy","blocked"}；未知值按 idle 处理（渲染层兜绿）。
    def set_state_level(self, level: str) -> None:
        with self._lock:
            self.state_level = level if level in ("idle", "busy", "blocked") else "idle"

    # 2026-09-21: 借鉴 atomcode toolbar —— 缓存命中率 + 权限模式喂入
    def set_cache_pct(self, pct: int) -> None:
        with self._lock:
            self.cache_pct = pct

    def set_perm_mode(self, mode: str) -> None:
        with self._lock:
            self.perm_mode = mode

    # 2026-09-22: plan 叠加态喂入（_toolbar_snapshot 每秒读
    # runtime.plan_mode.is_active 同步）；无 runtime/查询失败静默保留原值。
    def set_plan_active(self, active: bool) -> None:
        with self._lock:
            self.plan_active = bool(active)

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
    # 2026-09-22: 任务清单面板（todo panel，常驻状态栏上方，对标 atomcode）——
    # 每项一行：⚙ in_progress / · pending / ✓ completed / ✗ cancelled。
    # 有未完成项时先渲染清单行再渲染状态行（bottom_toolbar 多行片段 PT 原生支持，
    # 全屏 TUI _status_win 高度自适应配套）；无任务时零行，不占版面。
    _TODO_MARK = {
        "in_progress": ("class:accent", "⚙"),
        "pending": ("class:yellow", "·"),
        "completed": ("class:green", "✓"),
        "cancelled": ("class:red", "✗"),
    }
    for _st, _content in getattr(s, "todo_items", ()):
        _ms, _mark = _TODO_MARK.get(_st, ("", "·"))
        _show = _content if len(_content) <= 46 else _content[:45] + "…"
        frag.append((_ms, f"{_mark} {_show}\n"))
    # 2026-09-22: 状态球（对标 atomcode）——状态行最前一粒绿/黄/红圆点，
    # 一眼标定运行状态：idle=绿 / busy=黄 / blocked=红。优先级 blocked > busy > idle，
    # 由 _toolbar_snapshot 每秒判定 state_level 喂入；未知值兜绿。
    _STATE_STYLE = {"idle": "class:green", "busy": "class:yellow", "blocked": "class:red"}
    _sl = getattr(s, "state_level", "idle")
    frag.append((_STATE_STYLE.get(_sl, "class:green"), "●"))
    # 权限模式前缀（atomcode 的 ⏵⏵ auto 语义）——读运行时实际模式，未知则不显示
    perm = getattr(s, "perm_mode", "")
    if perm:
        frag.append(("class:accent", f" ⏵⏵ {perm} │"))
    # 2026-09-22: plan 叠加指示（对标 cc 的 plan mode on）——叠加态独立于
    # 权限模式段，两段可同时点亮；快照无字段时静默跳过（向后兼容）。
    if getattr(s, "plan_active", False):
        frag.append(("class:accent", " ⏸ plan │"))
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
