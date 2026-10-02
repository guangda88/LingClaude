"""斜杠命令插件：/budget —— 会话预算线查看与重置（灵克 P1② 接线轮 2026-10-02）。

配套 core/session_budget_gate.py（生产消费层）+ core/session_budget.py（纯核心）。
语义：/budget 无参查看四维计数与裁决；/budget reset 清零本进程会话计数（用户
恢复出口，与阈值暂停报告中的提示一致）。调阈值改
core/policies/session_budget_policy.yaml 后 /policy reload 热更生效。
"""
from __future__ import annotations


def budget_cmd(processor, arg: str = "") -> None:
    from lingclaude.core import session_budget_gate as gate

    sub = arg.strip().split()
    if sub and sub[0] == "reset":
        gate.reset()
        print("[budget] 计数已清零（本进程会话重新计数；阈值不变）")
        return
    if sub:
        print("[/budget] 用法：/budget（查看）｜/budget reset（清零）")
        return
    info = gate.snapshot_for_display()
    print(f"[budget] 策略启用: {info['enabled']}")
    counters = info.get("counters") or {}
    if counters:
        for dim in sorted(counters):
            print(f"  {dim}: {counters[dim]}")
    else:
        print("  （尚无记录：发起一轮对话/工具调用后出现）")
    print(f"  {info.get('summary', '')}")


def register(add) -> None:
    """loader 契约：add(name, fn, desc, aliases=(), needs_args=, arg_hint=)。"""
    add(
        "/budget", budget_cmd,
        "会话预算线：/budget 查看用量与阈值；/budget reset 清零恢复",
    )
