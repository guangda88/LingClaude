"""P1: 状态栏数据模型 — 单一数据源，主循环写，bottom_toolbar 读。

上下文占比口径（2026-09-28 查表化后）：
分母 = resolve_context_window(engine)（core/context_window.py）：
  显式配置 context_window_tokens > 模型查表 > provider 前缀兜底 > 128K。
  原硬编码 128_000 已废（glm-5.3-flash 官方 1M，虚高 7.8×）。
max_budget_tokens 是会话累计预算，不再充当窗口分母。
分子 = turn 内最后成功请求轮的真实 prompt_tokens（provider 未回传 usage
时为负哨兵 → 消费方走字符估算）。
"""
from __future__ import annotations

import os
import shutil
import threading
import unicodedata
from dataclasses import dataclass, field


@dataclass
class StatusModel:
    """可变状态容器。主循环在关键节点更新，toolbar 回调读取。"""

    model: str = "?"
    # 2026-09-27: 用户定义语义——toolbar 的 LLM 名显示此值：
    #   pinned → = 钉住的模型名；未 pinned → = 当前实际生效模型名
    #   （含 F12f 降级后的备选名，如 GLM-5.3-Flash）。
    # 由 _toolbar_snapshot 每秒从 engine 实时解析喂入，替换旧 model 字段
    # （旧 model 在 round_end 一次性写入，降级切换后不会更新）。
    # 空串 = 尚未解析到（渲染层回落 s.model 兼容旧行为）。
    runtime_model: str = ""
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
    # 2026-09-27: 任务面板显隐开关（Ctrl+T 切换，full_tui.py 绑定）——渲染层
    # 据此决定是否输出 ⚙{ip}·{pd} 汇总角标；喂入链路（set_todo_items）不受影响，
    # 隐藏期间数据照常聚合，重新显示时即刻反映最新计数（无 stale 窗口）。
    show_todo_panel: bool = True
    # 2026-09-27 Ctrl+T 复活面板：真开关（任务面板行显隐）。旧名
    # show_todo_panel 退役——它只控 ⚙ 角标，导致 Ctrl+T「无效」观感
    # （P1-5 后角标是唯一受控物，翻转无面板可看）。渲染层读此字段时
    # getattr 兜底：缺失回落旧 show_todo_panel，再回落 True（旧快照兼容）。
    show_task_panel: bool = True
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
    # 2026-09-25 P0 普查（toolbar 事故教训推广）：状态源降级通道——状态链喂入
    # try 块失败时在此登记源名，渲染层点亮红字降级行，替代原「静默 pass」
    # （静默吞 = 状态字段无声消失 + 证据销毁，full_tui.py 旧 950 行事故同款）。
    degraded: tuple[str, ...] = ()
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> "StatusModel":
        """toolbar 线程安全读取（返回浅拷贝，字符串/ int 均不可变，安全）。"""
        with self._lock:
            return StatusModel(
                model=self.model,
                # 2026-09-27: runtime_model 透传（str 不可变，浅拷贝安全）
                runtime_model=self.runtime_model,
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
                # 2026-09-27: 面板显隐开关透传（bool 不可变，浅拷贝安全）
                show_todo_panel=self.show_todo_panel,
                # 2026-09-27 Ctrl+T 复活面板：主开关透传（渲染层 getattr
                # 缺失时回落 show_todo_panel——旧快照对象兼容）
                show_task_panel=self.show_task_panel,
                # 2026-09-22: 状态球三色透传（str 不可变，浅拷贝安全）
                state_level=self.state_level,
                # 2026-09-22: plan 叠加态透传（bool 不可变，浅拷贝安全）
                plan_active=self.plan_active,
                # 2026-09-25: 状态源降级清单透传（元组不可变，浅拷贝安全）
                degraded=self.degraded,
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

    def set_runtime_model(self, name: str) -> None:
        """2026-09-27: 喂入 toolbar 实际显示模型名（用户定义语义字段）。"""
        with self._lock:
            self.runtime_model = name

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

    # 2026-09-27: 任务面板显隐切换（Ctrl+T）——只动渲染开关，不清数据。
    # 双开关同步写（show_task_panel 主 + show_todo_panel 兼容旧消费点）。
    def set_todo_panel_visible(self, visible: bool) -> None:
        with self._lock:
            self.show_task_panel = visible
            self.show_todo_panel = visible

    # 2026-09-27 Ctrl+T 新开关的标准写入口（与上者等价，语义名对齐新字段）。
    def set_task_panel_visible(self, visible: bool) -> None:
        with self._lock:
            self.show_task_panel = visible
            self.show_todo_panel = visible

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

    # 2026-09-25 P0 普查：状态源降级登记——feed 失败记名，成功喂入即清除。
    # 渲染层见非空 degraded 即点亮红字降级行（可见降级，替代静默 pass）。
    def mark_degraded(self, source: str) -> None:
        with self._lock:
            if source not in self.degraded:
                self.degraded = self.degraded + (source,)

    def clear_degraded(self, source: str) -> None:
        with self._lock:
            self.degraded = tuple(x for x in self.degraded if x != source)

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


# 2026-09-25: P1-4 常驻提示升级为 tips 轮换池——原「Esc+Enter 换行 · /multi 多行」
# 会话全程不变，右侧挤占挂起数/任务面板宽度；熟手早已无需。改为每 5 轮轮换一条
# 精选提示（turn 结束才变，不闪屏），键位兜底保住（首条），其余只推广真实注册的
# 斜杠命令（防虚构守卫见 test_cli_status.py：池内命令必须 ∈ SLASH_COMPLETER_WORDS）。
# 2026-09-25 二期：键位/操作类也入池（全部实测验证，见 repl.py:1193 启动横幅与
# interface.py:311-312 chord 表——Ctrl+Enter/Shift+Enter 同为换行）。
_TOOLBAR_TIPS: tuple[str, ...] = (
    # ── EVOLVE-BLOCK: toolbar_tips begin（借鉴③，AlphaEvolve 对标）──────────
    # 可自改区：本块内 tips 条目可由自进化机制增删改；块外（_TIP_EVERY_TURNS、
    # toolbar_tip 轮换逻辑、防虚构守卫）冻结执法。锚点成对性由
    # test_p04_arch_guards::test_evolve_block_paired 锁死。
    "Esc+Enter 换行 · Ctrl+Enter/Shift+Enter 亦然 · /multi 多行",
    "/compact 压缩上下文",
    "/tasks 看任务面板",
    "/checkpoint 快照存档",
    "/fork 分叉会话",
    "/model 换模型",
    "Ctrl+C 中断/清行 · Ctrl+D 退出",
    "/help 全部命令 · /history 会话回看",
    "Tab 补全命令 · 上箭头翻历史",
    "/resync 重绘界面",
    # ── EVOLVE-BLOCK: toolbar_tips end ─────────────────────────────────────
)
_TIP_EVERY_TURNS = 20

# 2026-09-27 Ctrl+T 复活面板：面板行数上限（不含表头/汇总行）。
# 超限收敛为「…另有 N 项」提示，全量走 /tasks——防多任务清单重新霸占
# 状态栏（P1-5 退役的初衷不回退，只恢复受限可见性）。
TASK_PANEL_MAX_ITEMS = 4


def toolbar_tip(turns: int) -> str:
    """按轮数轮换的 toolbar 提示：每 20 轮换一条（2026-09-26 降频 5→20，
    CC 实证提示非必需品），纯函数零状态。"""
    if turns <= 0:
        return _TOOLBAR_TIPS[0]
    return _TOOLBAR_TIPS[(turns // _TIP_EVERY_TURNS) % len(_TOOLBAR_TIPS)]


def _display_width(text: str) -> int:
    """East-Asian Width 感知显示宽度：全角/宽字符（CJK、●⚙🔄⏵⚠│）计 2，
    半角计 1。wcwidth 库未引入依赖，用 unicodedata 等价实现（eaw in
    ('W','F') 计 2，其余 1；不处理零宽组合序列——toolbar 文案均为纯文本）。"""
    return sum(
        2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text
    )


def _wrap_fragments(frag: list, width: int) -> list:
    """按 │ 段界贪心折行（P1-6, 2026-09-26）。

    PT bottom_toolbar Window 无 wrap_lines，超宽剪裁；此处预折行：
    把 frag 按段（含 │ 的文本段为界）聚成行，行宽超过 width 就在该段
    前断行——断行 = 在上一行行尾插 ('', '\\n')，toolbar 高度随内容行数
    自动增长。贪心策略保证「能塞下就同排」，段永不从中间剪断。
    width<=0（终端宽未知，如捕获输出/测试桩）不折行，维持旧行为。
    """
    if width <= 0 or not frag:
        return frag
    # 按段拆分：每段文本按 │ 切成子段（│ 保留在前子段尾部），各子段
    # 继承所在片段的 style。
    cells: list[tuple[str, str]] = []  # (style, text) 原子单元格
    for style, text in frag:
        parts = text.split("│")
        for i, part in enumerate(parts):
            chunk = ("│" + part) if i > 0 else part
            if chunk:
                cells.append((style, chunk))
    lines: list[list[tuple[str, str]]] = []
    cur: list[tuple[str, str]] = []
    cur_w = 0
    for style, chunk in cells:
        w = _display_width(chunk)
        if cur and cur_w + w > width:
            cur.append(("", "\n"))
            lines.append(cur)
            cur, cur_w = [], 0
        cur.append((style, chunk))
        cur_w += w
    if cur:
        lines.append(cur)
    if len(lines) <= 1:
        return frag  # 一行塞得下：原样返回，零回归面
    out: list[tuple[str, str]] = []
    for line in lines:
        out.extend(line)
    return out


def toolbar_fragments(s: StatusModel):
    """bottom_toolbar 回调 — 返回 (style, text) 片段列表。

    2026-09-21 重排（借鉴 atomcode）：
      ⏵⏵ auto │ glm5.3-flash │ ~/path │ 12.3k/128k tok (10%) │ cache 94% │ N轮 │ 任务 │ 待办
    上下文占比分色：<60% 绿 / <85% 黄 / >=85% 红+将压缩提示。
    钉住模型显示 [PINNED] 标识。
    2026-09-26 P1-6: 段界贪心折行——PT bottom_toolbar 的 Window 未开
    wrap_lines（prompt_toolkit prompt.py:589，默认 False），单行超宽直接
    剪裁（SSH 窄屏实测丢信息）。改为按 │ 段界折行插 \\n：toolbar 高度 =
    内容行数（FormattedTextControl.preferred_height），自动长高不截断。
    宽度取 shutil.get_terminal_size()（fd1 ioctl 实时反映 resize；COLUMNS
    环境变量优先，测试可注入）。注意 COLUMNS=0 会被 shutil 视为「未设置」
    回退 ioctl，非 tty 最终取 POSIX 缺省 80——即宽查询永不失败，折行
    总是生效（折行优于剪裁）。
    """
    frag = []
    # 2026-09-22: 任务清单面板（todo panel，常驻状态栏上方，对标 atomcode）——
    # 2026-09-25 渲染防腐：长跑进程历史脏 set 喂 None（interface.py:133 记录过
    # 同源镜像腐坏病灶）会让下方任意比较/len 炸 → 全屏 TUI 整栏空白。渲染层
    # 逐字段兜默认，脏快照永不再炸渲染。8 个 fuzz 实证炸点全覆盖。
    # 位置敏感：必须在 todo 循环与所有 len()/比较之前（首版插晚被 fuzz 当场抓回）。
    # 2026-09-25 P0: getattr 兜底替换直接访问——快照对象缺字段（旧版/测试桩
    # 构造）时整函数曾炸 AttributeError，违背「渲染层永不因脏快照炸」原则。
    _todo_items = getattr(s, "todo_items", None)
    if _todo_items is None:
        s.todo_items = ()
    if getattr(s, "ctx_window", None) is None:
        s.ctx_window = 0
    if getattr(s, "pending", None) is None:
        s.pending = 0
    if getattr(s, "task_pending", None) is None:
        s.task_pending = 0
    if getattr(s, "cache_pct", None) is None:
        s.cache_pct = -1
    s.cwd = getattr(s, "cwd", None) or ""
    s.task = getattr(s, "task", None) or "空闲"
    s.turns = getattr(s, "turns", None) or 0
    # 2026-09-25 P0: 二批补齐——model/pinned/ctx_tokens/task_active 同属裸访问
    if getattr(s, "ctx_tokens", None) is None:
        s.ctx_tokens = 0
    if getattr(s, "model", None) is None:
        s.model = "?"
    if getattr(s, "pinned", None) is None:
        s.pinned = False
    if getattr(s, "task_active", None) is None:
        s.task_active = ""
    # 2026-09-26 P1-5: todo 清单逐项常驻退役（对标 CC status line：单行，
    # 无常驻 todo 面板）——多任务时逐项清单曾霸占 toolbar 5-10 行。改为
    # 一粒汇总角标 ⚙{ip}·{pd}（in_progress/pending 计数，completed/cancelled
    # 不计入）；明细查看走 /tasks（输出进 scrollback，屏上滚即可）。
    # 喂入链路 set_todo_items 保留（repl.py 1s 节流块），渲染侧消费。
    _todo_items = getattr(s, "todo_items", ())
    # 计数契约：in_progress/pending 原样计入；completed/cancelled 排除；
    # 未知态兜底 pending 计入（对齐旧 _TODO_MARK.get(_st, ("", "·")) 契约）
    _KNOWN = ("in_progress", "pending", "completed", "cancelled")
    _ip_n = sum(1 for _st, _ in _todo_items if _st == "in_progress")
    _pd_n = sum(
        1 for _st, _ in _todo_items if _st == "pending" or _st not in _KNOWN
    )
    # 2026-09-27: 显隐开关（Ctrl+T）——隐藏时不输出角标；getattr 兜底 True
    # （旧版快照对象缺字段时保持旧行为=显示，向后兼容）。
    if (getattr(s, "show_todo_panel", True)) and (_ip_n or _pd_n):
        _todo_badge = ("class:accent", f" ⚙{_ip_n}·{_pd_n}")
    # 2026-09-22: 状态球（对标 atomcode）——状态行最前一粒绿/黄/红圆点，
    # 一眼标定运行状态：idle=绿 / busy=黄 / blocked=红。优先级 blocked > busy > idle，
    # 由 _toolbar_snapshot 每秒判定 state_level 喂入；未知值兜绿。
    _STATE_STYLE = {"idle": "class:green", "busy": "class:yellow", "blocked": "class:red"}
    # 2026-09-25 渲染防腐：长跑进程历史脏 set 喂 None（interface.py:133 记录过
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
    # 2026-09-25 P0 普查：状态源降级行——任何喂入源 try 失败即在此红字可见，
    # 替代旧行为「字段无声消失、外层 except 吞为空白」（toolbar 事故教训）。
    _deg = getattr(s, "degraded", ()) or ()
    if _deg:
        frag.append(("class:red", f" ⚠状态源降级:{'+'.join(_deg[:3])}{'…' if len(_deg) > 3 else ''} │"))
    cwd_display = s.cwd
    if len(cwd_display) > 28:
        cwd_display = "…" + cwd_display[-27:]
    # 2026-09-27 用户定义语义（见 StatusModel.runtime_model 字段注释）：
    #   pinned     → 显示钉住的模型名 + [PINNED]
    #   未 pinned  → 显示 runtime_model（当前实际生效模型，含降级备选），
    #                空则回落 s.model（旧启动值，兼容快照缺新字段）。
    _runtime = getattr(s, "runtime_model", "") or ""
    if s.pinned:
        model_display = f"{_runtime or s.model} [PINNED]"
    elif _runtime:
        model_display = _runtime
    else:
        model_display = s.model
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
    # 2026-09-26 P1-5: tips 轮换池降频 5→20 轮（CC 实证：提示非必需品，
    # 常驻占用视觉带宽；20 轮 ≈ 一次典型工作循环才换一条）
    frag.append(("", f"│ {toolbar_tip(s.turns)} "))
    if s.pending > 0:
        frag.append(("class:accent", f" │ 挂起×{s.pending}"))
    # 2026-09-26 P1-5: 待办×N 段退役——todo 汇总角标 ⚙{ip}·{pd} 已含该信息
    #（同源同值，双显冗余）；🔄当前执行段保留（active 名单角标不含）。
    if s.task_active:
        act = s.task_active if len(s.task_active) <= 18 else s.task_active[:17] + "…"
        frag.append(("class:accent", f" │ 🔄 {act}"))
    # todo 汇总角标挂行尾（与 🔄 相邻，任务语义聚拢）
    # 2026-09-27: 消费条件与赋值点（:326）保持同一显隐开关——旧码两处均为
    # 纯计数条件天然同步，引入开关后必须两侧同改，否则隐藏时有任务会
    # UnboundLocalError（_todo_badge 未赋值即消费，test_panel_hidden 实抓）。
    if (getattr(s, "show_todo_panel", True)) and (_ip_n or _pd_n):
        frag.append(_todo_badge)
    # 2026-09-27 Ctrl+T 复活面板真身：P1-5 退役逐项清单后 Ctrl+T 只能翻转
    # ⚙ 角标（无面板可看=「无效」观感的根因）。恢复受限面板——状态行上方
    # 最多 TASK_PANEL_MAX_ITEMS 行：in_progress 优先、pending 次之（组内保持
    # 原序），超限收敛为汇总提示行（全量仍走 /tasks）。数据零新 I/O：复用
    # _toolbar_snapshot 已喂入的 todo_items（快照元组 (状态, 名)）。
    _show_panel = getattr(s, "show_task_panel", None)
    if _show_panel is None:  # 旧快照缺新字段：回落旧开关再回落 True
        _show_panel = getattr(s, "show_todo_panel", True)
    if _show_panel and (_ip_n or _pd_n):
        _MARK = {"in_progress": ("class:green", "▸"), "pending": ("", "·")}
        _panel: list[tuple[str, str]] = [
            ("class:accent", f"── 任务面板 {max(_ip_n, 1)}/{_ip_n + _pd_n} (Ctrl+T 隐藏)"),
            ("", "\n"),
        ]
        _shown = 0
        for _st, _name in _todo_items:  # 组间序：in_progress 先于 pending/未知
            if _shown >= TASK_PANEL_MAX_ITEMS:
                break
            _m = _MARK.get(_st)  # completed/cancelled/未知不占面板行
            if _m is None:
                continue
            if _shown:
                _panel.append(("", "\n"))
            _nm = _name if len(_name) <= 36 else _name[:35] + "…"
            _panel.append((_m[0], f"{_m[1]} {_nm}"))
            _shown += 1
        _hidden = (_ip_n + _pd_n) - _shown
        if _hidden > 0:
            _panel.append(("", "\n"))
            _panel.append(("", f"  …另有 {_hidden} 项（/tasks 看全量）"))
        frag = _panel + [("class:sep", "\n")] + frag
    # 2026-09-26 P1-6: 段界贪心折行——超终端宽时在 │ 段界断行成多行，
    # toolbar 自动长高，信息不再被 PT 剪裁（详见 _wrap_fragments docstring）
    try:
        _cols = shutil.get_terminal_size().columns
    except (ValueError, OSError, AttributeError):
        _cols = 0
    return _wrap_fragments(frag, _cols)
