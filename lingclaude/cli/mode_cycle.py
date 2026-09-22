"""Shift+Tab 四态模式环 — 单键管所有模式（对标 cc 的模式循环键）。

环序: auto → ask → strict → plan ─┐
        ↑________________________┘

语义（2026-09-22 与用户确认）:
- auto/ask/strict 段: set_permission_mode()（本进程内存立即生效 + 落盘）；
  跨进程由 permissions._maybe_reload_mode 的 mtime 探测对端自动捡起。
- plan 段: runtime.plan_mode.enter()（叠加态，不覆盖权限模式），进入时
  记忆当前权限模式；环上再次 Shift+Tab 时 plan_mode.exit() 恢复记忆值，
  然后继续权限环。例: auto → ask → strict → plan → strict → auto。
- headless（未 set_plan_runtime_provider）: plan 档静默空转（返回 None），
  调用方可据此跳过提示输出。
"""

from __future__ import annotations

import sys
from typing import Any

from lingclaude.core.permissions import (
    VALID_MODES,
    get_permission_mode,
    set_permission_mode,
)

# plan 环位哨兵（str 类型便于类型一致；不会与合法权限模式值冲突）
PLAN_EXIT = "__plan_exit__"

# 环序: 权限三态 + plan 叠加位。VALID_MODES = ("auto", "ask", "strict")。
MODE_RING: tuple[str, ...] = VALID_MODES + (PLAN_EXIT,)

# plan 进入时记忆的权限模式（恢复用）；None = 当前不在 plan 叠加态
_prev_perm_mode: str | None = None

# 退出 plan 后的一次性跳位闩：plan 槽刚被访问过，环上下一跳越过 plan
# 直接继续权限段（实测发现：无此闩 strict 退出 plan 后再按会重进 plan，
# 与确认语义 plan → strict → auto 不符）
_skip_plan_once: bool = False

# plan runtime 提供者（repl 启动时注入一次；返回 CodingRuntime.plan_mode）
_plan_runtime_provider: Any = None


def set_plan_runtime_provider(provider: Any) -> None:
    """注入 plan_mode 提供者（无参 callable，返回 PlanMode 或 None）。

    repl 装配时调用；传 None 清除（headless / 测试场景）。
    """
    global _plan_runtime_provider
    _plan_runtime_provider = provider


def current_display_mode() -> str:
    """当前展示模式: plan 活跃时返回 "plan"（toolbar/提示用），否则权限模式。"""
    pm = _resolve_plan_mode()
    if pm is not None and getattr(pm, "is_active", False):
        return "plan"
    return get_permission_mode()


def _resolve_plan_mode() -> Any:
    """经 provider 取 PlanMode 实例；未注入/异常一律 None（fail-safe）。"""
    if _plan_runtime_provider is None:
        return None
    try:
        return _plan_runtime_provider()
    except Exception:  # noqa: BLE001 — plan 不可用时不阻塞权限环
        return None


def transition() -> tuple[str | None, str | None] | None:
    """环上推进一步并应用。

    Returns:
        (from_display, to_display) 成功（调用方输出提示）；
        None 表示空转（plan 档 headless / provider 异常），调用方静默。
    """
    global _prev_perm_mode, _skip_plan_once

    pm = _resolve_plan_mode()
    plan_active = pm is not None and getattr(pm, "is_active", False)

    # 当前环位: plan 活跃时即 plan 位（环位由实际态推导，不存游标——
    # 权限模式被外部（API/webUI）热更时环位仍与真实状态一致）
    if plan_active:
        cur = PLAN_EXIT
    else:
        try:
            cur = MODE_RING[MODE_RING.index(get_permission_mode())]
        except ValueError:
            return None  # 内存模式异常损坏: 不推进，等待下次热更/重启恢复

    idx = MODE_RING.index(cur)
    nxt = MODE_RING[(idx + 1) % len(MODE_RING)]
    if nxt == PLAN_EXIT and _skip_plan_once:
        # plan 槽刚访问过（退出 plan 恢复到原位），本跳越过 plan 继续权限环
        nxt = MODE_RING[(idx + 2) % len(MODE_RING)]
        _skip_plan_once = False

    if nxt == PLAN_EXIT:
        # 进入 plan: 叠加态，权限模式保留并记忆（异常恢复路径: 记忆缺失时
        # 用当前权限模式兜底，保证 exit 必有合理恢复值）
        if pm is None:
            return None
        if _prev_perm_mode is None:
            _prev_perm_mode = get_permission_mode()
        pm.enter()
        return (cur, "plan")

    if plan_active:
        # 退出 plan: 恢复记忆的权限模式，置跳位闩（下一跳越过 plan 槽继续环）
        prev = _prev_perm_mode if _prev_perm_mode in VALID_MODES else get_permission_mode()
        _prev_perm_mode = None
        _skip_plan_once = True
        if pm is not None:
            pm.exit()
        set_permission_mode(prev)
        return ("plan", prev)

    # 权限环内普通推进
    if not set_permission_mode(nxt):
        return None
    return (cur, nxt)


def shift_mode(session: Any = None) -> None:
    """Shift+Tab 键位入口: 推进模式环并输出确认。

    全屏 TUI 下 session.append_output 经 stdout 代理进输出窗（full_tui
    唯一公开追加接口）；P1/裸终端退回 sys.stdout.write（与状态栏互不
    干扰——toolbar 1s 快照自动刷新显示）。
    """
    result = transition()
    if result is None:
        return
    frm, to = result
    if to == "plan":
        keep = _prev_perm_mode or get_permission_mode()
        msg = f"[模式] ⏸ plan 开启（只读探索，权限保持 {keep}；再按 Shift+Tab 退出）"
    elif frm == "plan":
        msg = f"[模式] ⏸ plan 关闭（权限恢复 {to}）"
    else:
        msg = f"[模式] 权限: {frm} → {to}"
    write = getattr(session, "append_output", None)
    if callable(write):
        write(msg)
    else:
        try:
            sys.stdout.write(f"\n{msg}\n")
            sys.stdout.flush()
        except (OSError, ValueError):
            pass
