"""P0-A 阶段 3（2026-09-22）：LingClaudeThread 薄壳 facade（对标 CodexThread）。

薄 facade：持 hooks/provider/journal 引用，暴露 run_turn()，内部直接调
loop_body 现有循环。**不持状态**（对话缓冲/journal/usage 全在 QueryEngine）——
Thread 的价值随 P1-0（Speculative Fan Out 挂 LoopHooks 子 seam）才兑现，
现在做厚 = 违反契约 §四边界纪律（seam god-object 回潮）。

阶段 4（收尾）后此 facade 是 engine/loop 的对外会话入口；
P1-0 的 pre_decide/post_check/decide_continue 挂载时再充实。
"""
from __future__ import annotations

from typing import Any, Generator

from lingclaude.engine.loop.loop_body import (
    run_call_model_loop,
    run_stream_call_model_loop,
)


class LingClaudeThread:
    """会话级循环入口（薄 facade，对标 CodexThread）。

    构造即绑定一个引擎实例（hooks/provider/journal 均经引擎访问，
    Thread 自身零状态）。run_turn 是唯一公共方法：
    - stream=False → 返回最终文本（str）
    - stream=True  → yield 事件 dict（与 stream_call_model 事件契约一致）
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    @property
    def hooks(self) -> Any:
        """LoopHooks 注入面（透传引擎装配的 seam；P1-0 挂子 seam 的锚点）。"""
        return self._engine.hooks

    def run_turn(self, prompt: str, *, stream: bool = False) -> Any:
        """跑一轮对话循环（委托 loop_body 现有实现，行为零分叉）。"""
        if stream:
            return run_stream_call_model_loop(self._engine, prompt)
        return run_call_model_loop(self._engine, prompt)
