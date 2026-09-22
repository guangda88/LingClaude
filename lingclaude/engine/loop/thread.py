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
        self._scheduler: Any = None

    @property
    def hooks(self) -> Any:
        """LoopHooks 注入面（透传引擎装配的 seam；P1-0 挂子 seam 的锚点）。"""
        return self._engine.hooks

    # ── P1-0（2026-09-22）：Speculative Fan Out 调度面（orchestrator.loop_stage）──
    # facade 加厚的边界（契约 §四）：Thread 只做调度装配与投机分支执行，
    # 不碰循环体逻辑（循环体已经 hooks.pre_decide/decide_continue 挂点消费调度决策）。
    # 调度器协议（duck typing，三方法全部 fail-soft）：
    #   plan(prompt, messages) -> dict | None    # {"fan_out": [{"prompt","tag"}], ...} | None
    #   verify(tag, result) -> bool              # 投机结果校验
    #   should_continue(prompt, round_idx, plan) -> bool
    def set_fan_out_scheduler(self, scheduler: Any) -> None:
        """注册投机扇出调度器并接通 LoopHooks 三钩子（幂等；None 注销）。"""
        self._scheduler = scheduler
        hooks = self._engine.hooks
        if scheduler is None:
            # 注销：清掉引擎侧 fan-out 函数（DefaultLoopHooks 恢复直通语义）
            for attr in ("_fan_out_pre_decide", "_fan_out_post_check", "_fan_out_decide_continue"):
                if hasattr(hooks, attr):
                    delattr(hooks, attr)
            return
        hooks._fan_out_pre_decide = lambda prompt, messages: (
            scheduler.plan(prompt, messages) if hasattr(scheduler, "plan") else None
        )
        hooks._fan_out_post_check = lambda tag, result: (
            bool(scheduler.verify(tag, result)) if hasattr(scheduler, "verify") else False
        )
        hooks._fan_out_decide_continue = lambda prompt, round_idx, plan: (
            bool(scheduler.should_continue(prompt, round_idx, plan))
            if hasattr(scheduler, "should_continue") else True
        )

    def run_speculative(self, branches: list[dict[str, str]]) -> dict[str, Any]:
        """执行投机分支（并行扇出的串行降级版；每分支 fail-soft 独立捕获）。

        branches: [{"prompt": str, "tag": str}]。返回 {tag: result_str}；
        结果不直接进主路径——由调度器 verify() 决定采纳（契约：投机永不拖垮主循环）。
        """
        results: dict[str, Any] = {}
        for b in branches or []:
            tag = b.get("tag", "")
            try:
                results[tag] = run_call_model_loop(self._engine, b["prompt"])
            except Exception as e:  # noqa: BLE001 — 投机分支失败即丢弃该分支
                results[tag] = f"[speculative-branch-error] {e}"
        return results

    def run_turn(self, prompt: str, *, stream: bool = False) -> Any:
        """跑一轮对话循环（委托 loop_body 现有实现，行为零分叉）。"""
        if stream:
            return run_stream_call_model_loop(self._engine, prompt)
        return run_call_model_loop(self._engine, prompt)
