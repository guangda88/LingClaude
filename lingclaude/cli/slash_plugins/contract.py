"""斜杠命令插件：/contract —— 任务级验收契约管理（M2 gate 契约化 2026-10-02）。

用法：
  /contract new 项1 项2 ...   建新契约（覆盖旧契约）
  /contract add 项            追加验收项
  /contract done <序号>       勾验收项（1-based）
  /contract clear             整单收口
  /contract                   查看清单与进度
退出语义：有未清项时 /quit 被拦一次出报告；/quit force 显式越过。
"""
from __future__ import annotations


def contract_cmd(processor, arg: str = "") -> None:
    from lingclaude.core import task_contract as tc

    parts = arg.strip().split()
    if not parts:
        print(tc.render_status())
        return
    sub, rest = parts[0], " ".join(parts[1:])

    if sub == "new":
        if not rest:
            print("[/contract] 用法：/contract new 项1 项2 ...")
            return
        items = tc.contract_new(rest.split())
        print(f"[contract] 已建 {len(items)} 项验收契约")
        print(tc.render_status())
        return
    if sub == "add":
        if not rest:
            print("[/contract] 用法：/contract add 项描述")
            return
        tc.contract_add(rest)
        print(f"[contract] 已追加：{rest}")
        return
    if sub == "done":
        try:
            idx = int(rest.split()[0])
        except (ValueError, IndexError):
            print("[/contract] 用法：/contract done <序号>")
            return
        print(f"[contract] {idx}: " + ("已验收" if tc.contract_done(idx) else "序号无效"))
        return
    if sub == "clear":
        tc.contract_clear()
        print("[contract] 契约已收口清除")
        return
    print("[/contract] 用法：new/add/done/clear 或无参查看")


def register(add) -> None:
    """loader 契约：add(name, fn, desc, aliases=(), needs_args=, arg_hint=)。"""
    add(
        "/contract", contract_cmd,
        "任务验收契约：new/add/done/clear；未清零时 /quit 会被拦（force 可越）",
    )
