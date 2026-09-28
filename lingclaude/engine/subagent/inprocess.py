"""In-process subagent backend (P1-2): 复用现有 SubAgent 单后端逻辑.

通过 import 旧版 lingclaude.engine.sub_agent.SubAgent 保持行为兼容,
但把 provider 解析从 hardcode 提升为可配置 request.provider.
T1-6 深化: 支持 parallel 并行 + control_channel 状态跟踪.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

logger = logging.getLogger(__name__)

from lingclaude.engine.subagent.base import (
    SubagentBackend,
    SubagentContext,
    SubagentRequest,
    SubagentResult,
    SubagentStatus,
)
import uuid


def _resolve_provider(model_provider: Any) -> Any:
    """P1: 兼容 SlotHandle（调用时解析）与裸 provider 实例。

    主干 model_provider 已入槽（coding.py 持 SlotHandle），ctx.model_provider
    可能是 SlotHandle —— 这里解析成真实 provider；裸实例则原样返回（向后兼容）。
    """
    if model_provider is None:
        return None
    # duck-typing：SlotHandle 有 instance() 解析入口；裸 provider 没有
    instance_fn = getattr(model_provider, "instance", None)
    if callable(instance_fn):
        try:
            return instance_fn()
        except Exception:  # noqa: BLE001 — 解析失败按 None 处理（No model provider）
            return None
    return model_provider


class InProcessSubagentBackend(SubagentBackend):
    """Same-process execution using the agent runtime + model provider."""

    name = "inprocess"

    def __init__(self) -> None:
        super().__init__()
        # T1-6: 控制通道 — 跟踪运行中的 agent_id → (thread, result_holder)
        self._running: dict[str, tuple[threading.Thread, dict[str, object]]] = {}
        self._lock = threading.Lock()

    def run(self, request: SubagentRequest, ctx: SubagentContext) -> SubagentResult:
        from lingclaude.engine.loop.sub_agent import SubAgent, SubAgentConfig

        if ctx.runtime is None:
            return SubagentResult(
                agent_id="",
                task=request.task,
                output="",
                success=False,
                error="No runtime",
                status=SubagentStatus.FAILED,
            )
        if ctx.model_provider is None:
            return SubagentResult(
                agent_id="",
                task=request.task,
                output="",
                success=False,
                error="No model provider",
                status=SubagentStatus.FAILED,
            )

        # P1 (2026-09-28): ctx.model_provider 可能是 SlotHandle（调用时解析），
        # 这里解析成真实 provider 实例再交给 SubAgent（ SubAgent 调 .complete()）。
        provider = _resolve_provider(ctx.model_provider)
        if provider is None:
            return SubagentResult(
                agent_id="",
                task=request.task,
                output="",
                success=False,
                error="No model provider",
                status=SubagentStatus.FAILED,
            )

        # T1-6: 并行执行
        # P1-20260928: 并发预算——provider 熔断器开启（连续 429 冷却中）时
        # 降级串行，避免 N+1 路并发打同一 key 放大硬配额耗尽（主会话+子代理
        # 共享 inprocess provider 实例 → 共享上游配额）。
        if request.parallel > 1:
            retry_policy = getattr(provider, "_retry_policy", None)
            if retry_policy is not None and getattr(retry_policy, "circuit_open", False):
                logger.warning(
                    "subagent 并发预算：provider 熔断器开启（circuit_429=%d），"
                    "parallel=%d 降级为串行",
                    getattr(retry_policy, "_circuit_consecutive_429", -1),
                    request.parallel,
                )
                # P1-20260928: 429 预检——熔断器开启时拒绝并行，直接返回失败
                return SubagentResult(
                    agent_id=f"parallel-rejected-{uuid.uuid4().hex[:8]}",
                    task=request.task,
                    output="",
                    success=False,
                    error="Provider circuit breaker open (429 cooldown), parallel dispatch rejected",
                    provider=self.name,
                    status=SubagentStatus.FAILED,
                )
            else:
                return self._run_parallel(request, ctx, provider)

        config = SubAgentConfig(
            max_rounds=request.max_rounds,
            allowed_tools=ctx.allowed_tools,
        )
        agent = SubAgent(config=config, runtime=ctx.runtime, provider=provider,
                         hooks=getattr(ctx.runtime, "hooks", None) if ctx.runtime is not None else None)
        result = agent.run(request.task, request.context)
        subagent_result = SubagentResult(
            agent_id=result.agent_id,
            task=request.task,
            output=result.output,
            success=result.success,
            error=result.error,
            tools_used=tuple(result.tools_used),
            rounds=result.rounds,
            provider=self.name,
            status=SubagentStatus.COMPLETED if result.success else SubagentStatus.FAILED,
        )
        # T1-6: 控制通道注册
        if request.control_channel:
            self._register_running(result.agent_id, subagent_result)
        return subagent_result

    def _run_parallel(self, request: SubagentRequest, ctx: SubagentContext, provider: Any) -> SubagentResult:
        """T1-6: 并行执行多个子任务（每个任务独立 SubAgent）。

        P1-20260928: 输出限流——聚合时限制总输出长度（默认 50KB），防止并行结果撑爆内存。
        """
        from lingclaude.engine.loop.sub_agent import SubAgent, SubAgentConfig

        config = SubAgentConfig(
            max_rounds=request.max_rounds,
            allowed_tools=ctx.allowed_tools,
        )
        num_parallel = max(request.parallel, 2)
        results: list[SubagentResult] = []

        def _run_one(i: int) -> SubagentResult:
            agent = SubAgent(config=config, runtime=ctx.runtime, provider=provider,
                         hooks=getattr(ctx.runtime, "hooks", None) if ctx.runtime is not None else None)
            task = f"{request.task} [parallel-{i+1}/{num_parallel}]"
            result = agent.run(task, request.context)
            return SubagentResult(
                agent_id=f"{result.agent_id}-{i}",
                task=request.task,
                output=result.output,
                success=result.success,
                error=result.error,
                tools_used=tuple(result.tools_used),
                rounds=result.rounds,
                provider=self.name,
                status=SubagentStatus.COMPLETED if result.success else SubagentStatus.FAILED,
            )

        with ThreadPoolExecutor(max_workers=num_parallel) as pool:
            futures = [pool.submit(_run_one, i) for i in range(num_parallel)]
            for fut in as_completed(futures):
                try:
                    results.append(fut.result())
                except Exception as e:  # noqa: BLE001
                    results.append(SubagentResult(
                        agent_id=str(uuid.uuid4())[:8],
                        task=request.task,
                        output="",
                        success=False,
                        error=str(e),
                        status=SubagentStatus.FAILED,
                    ))

        # P1-20260928: 输出限流——聚合时限制总输出长度，防止并行结果撑爆内存
        MAX_AGGREGATE_OUTPUT = 50 * 1024  # 50KB 上限
        success_count = sum(1 for r in results if r.success)
        all_output_parts = [r.output for r in results if r.output]
        all_output = "\n\n".join(all_output_parts)
        if len(all_output) > MAX_AGGREGATE_OUTPUT:
            logger.warning(
                "subagent 并行输出限流：聚合长度 %d 超过上限 %d，截断",
                len(all_output), MAX_AGGREGATE_OUTPUT,
            )
            # 保留前 N 个结果，丢弃尾部
            truncated_output = all_output[:MAX_AGGREGATE_OUTPUT]
            truncated_output += f"\n\n... [TRUNCATED: {len(all_output) - MAX_AGGREGATE_OUTPUT} bytes omitted]"
            all_output = truncated_output

        all_tools = tuple({tool for r in results for tool in r.tools_used})
        total_rounds = sum(r.rounds for r in results)
        return SubagentResult(
            agent_id=f"parallel-{uuid.uuid4().hex[:8]}",
            task=request.task,
            output=all_output,
            success=success_count > 0,
            error=f"{num_parallel - success_count}/{num_parallel} tasks failed" if success_count < num_parallel else None,
            tools_used=all_tools,
            rounds=total_rounds,
            provider=self.name,
            status=SubagentStatus.COMPLETED if success_count == num_parallel else SubagentStatus.FAILED,
        )

    def _register_running(self, agent_id: str, result: SubagentResult) -> None:
        """T1-6: 控制通道 — 注册运行中的 agent。"""
        with self._lock:
            self._running[agent_id] = (None, {"result": result})

    def _unregister_running(self, agent_id: str) -> None:
        """T1-6: 控制通道 — 移除已完成的 agent。"""
        with self._lock:
            self._running.pop(agent_id, None)

    def abort(self, agent_id: str) -> bool:
        """T1-6: 中止运行中的子代理。"""
        with self._lock:
            if agent_id in self._running:
                # 标记为中止（inprocess 无法强杀线程，只能标记）
                self._running[agent_id][1]["aborted"] = True
                self._unregister_running(agent_id)
                return True
        return False

    def status(self, agent_id: str) -> SubagentStatus:
        """T1-6: 查询子代理状态。"""
        with self._lock:
            if agent_id not in self._running:
                return SubagentStatus.COMPLETED
            if self._running[agent_id][1].get("aborted"):
                return SubagentStatus.ABORTED
        return SubagentStatus.RUNNING
